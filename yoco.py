"""Yoco Checkout API integration.

Stdlib-only HTTP — same approach as rfm_api_client.py so we don't add a
runtime dependency. Two public functions:

  create_checkout(...)         -> CheckoutResult
  verify_webhook_signature(...) -> bool   (Svix-style HMAC)

Configuration lives in the Settings table:
  yoco_public_key       — pk_test_... / pk_live_...
  yoco_secret_key       — sk_test_... / sk_live_...   (server-side only!)
  yoco_webhook_secret   — whsec_...
  yoco_test_mode        — "true" / "false" (cosmetic flag — Yoco picks
                           live vs test based on the secret key prefix)

One-time webhook registration after deploy:
  curl -X POST "https://payments.yoco.com/api/webhooks" \
    -H "Authorization: Bearer sk_test_..." \
    -H "Content-Type: application/json" \
    -d '{"name": "RFM portal", "url": "https://<host>/api/payments/yoco/webhook"}'

Copy the "secret" field from the response into Settings.yoco_webhook_secret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional
import urllib.error
import urllib.request


YOCO_CHECKOUTS_URL = "https://payments.yoco.com/api/checkouts"
YOCO_WEBHOOKS_URL = "https://payments.yoco.com/api/webhooks"
WEBHOOK_TOLERANCE_SECONDS = 5 * 60  # reject events older than 5 min
DEFAULT_TIMEOUT = 15
# Yoco's edge (Cloudflare) blocks the default `Python-urllib/3.x` UA as bot
# traffic and returns 403 "The site owner has blocked access based on your
# browser's signature." Using an explicit, legitimate UA fixes it.
USER_AGENT = "RFM-Portal/1.0 (+https://github.com/RevivalFireMinistries)"


class YocoError(RuntimeError):
    """Raised when Yoco rejects a checkout or returns an unexpected response."""


@dataclass(frozen=True)
class CheckoutResult:
    checkout_id: str
    redirect_url: str
    status: str
    raw: dict


# ---------------------------------------------------------------------------
# Settings helpers (kept local so tests can monkeypatch easily)
# ---------------------------------------------------------------------------

def get_setting(db, key: str) -> str:
    from models import Settings
    row = db.query(Settings).filter(Settings.key == key).first()
    return (row.value or "").strip() if row and row.value else ""


def is_configured(db) -> bool:
    return bool(get_setting(db, "yoco_secret_key"))


# ---------------------------------------------------------------------------
# Checkout API
# ---------------------------------------------------------------------------

def create_checkout(
    *,
    secret_key: str,
    amount_cents: int,
    success_url: str,
    cancel_url: str,
    failure_url: str,
    metadata: Optional[dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> CheckoutResult:
    """POST /api/checkouts. Returns the redirect URL the browser must open."""
    if not secret_key:
        raise YocoError("Yoco is not configured (missing secret key)")
    if amount_cents <= 0:
        raise YocoError("Amount must be greater than zero")

    body: dict = {
        "amount": int(amount_cents),
        "currency": "ZAR",
        "successUrl": success_url,
        "cancelUrl": cancel_url,
        "failureUrl": failure_url,
    }
    if metadata:
        # Yoco accepts string-string metadata. We stamp local correlation IDs
        # so the webhook can reconcile against a PaymentTransaction row.
        body["metadata"] = {str(k): str(v) for k, v in metadata.items()}

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        YOCO_CHECKOUTS_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        status = e.code
    except urllib.error.URLError as e:
        raise YocoError(f"Could not reach Yoco: {e.reason}")
    except Exception as e:
        raise YocoError(f"Yoco request failed: {e}")

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}

    if status >= 400:
        msg = payload.get("detail") or payload.get("title") or f"HTTP {status}"
        raise YocoError(f"Yoco rejected the checkout: {msg}")

    checkout_id = payload.get("id")
    redirect_url = payload.get("redirectUrl")
    if not checkout_id or not redirect_url:
        raise YocoError("Yoco response missing id or redirectUrl")

    return CheckoutResult(
        checkout_id=str(checkout_id),
        redirect_url=str(redirect_url),
        status=str(payload.get("status", "created")),
        raw=payload,
    )


def _api_call(method: str, url: str, *, secret_key: str, body: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Helper for arbitrary calls to the Yoco Checkout API surface (webhooks)."""
    if not secret_key:
        raise YocoError("Yoco is not configured (missing secret key)")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        status = e.code
    except urllib.error.URLError as e:
        raise YocoError(f"Could not reach Yoco: {e.reason}")
    except Exception as e:
        raise YocoError(f"Yoco request failed: {e}")
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"_raw": raw}
    if status >= 400:
        msg = payload.get("detail") or payload.get("title") or f"HTTP {status}"
        raise YocoError(f"Yoco API error ({status}): {msg}")
    return payload if isinstance(payload, dict) else {"_raw": payload}


def register_webhook(*, secret_key: str, name: str, url: str) -> dict:
    """Register a webhook subscription. Returns the dict including the
    one-time `secret` (whsec_…) — caller MUST persist it immediately."""
    return _api_call("POST", YOCO_WEBHOOKS_URL, secret_key=secret_key, body={"name": name, "url": url})


def list_webhooks(*, secret_key: str) -> list:
    """List webhook subscriptions registered against this key."""
    payload = _api_call("GET", YOCO_WEBHOOKS_URL, secret_key=secret_key)
    if isinstance(payload, dict) and "subscriptions" in payload:
        return payload["subscriptions"] or []
    if isinstance(payload, list):
        return payload
    return payload.get("data") or []


def delete_webhook(*, secret_key: str, subscription_id: str) -> None:
    """Delete a webhook subscription by id (wsub_...)."""
    _api_call("DELETE", f"{YOCO_WEBHOOKS_URL}/{subscription_id}", secret_key=secret_key)


# ---------------------------------------------------------------------------
# Webhook signature verification (Svix-style)
# ---------------------------------------------------------------------------

def _decode_secret(webhook_secret: str) -> bytes:
    """Strip the whsec_ prefix and base64-decode the remainder."""
    if not webhook_secret:
        return b""
    s = webhook_secret
    if s.startswith("whsec_"):
        s = s[len("whsec_"):]
    pad = "=" * (-len(s) % 4)
    return base64.b64decode(s + pad)


def verify_webhook_signature(
    *,
    body: bytes,
    webhook_id: Optional[str],
    webhook_timestamp: Optional[str],
    webhook_signature: Optional[str],
    webhook_secret: str,
    now: Optional[int] = None,
) -> bool:
    """Constant-time check matching Yoco's Svix signature scheme."""
    if not (webhook_secret and webhook_id and webhook_timestamp and webhook_signature):
        return False
    try:
        ts = int(webhook_timestamp)
    except (TypeError, ValueError):
        return False
    current = int(time.time()) if now is None else int(now)
    if abs(current - ts) > WEBHOOK_TOLERANCE_SECONDS:
        return False
    try:
        secret_bytes = _decode_secret(webhook_secret)
    except Exception:
        return False
    if not secret_bytes:
        return False
    signed = f"{webhook_id}.{webhook_timestamp}.".encode("utf-8") + (body or b"")
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed, hashlib.sha256).digest()
    ).decode("ascii")
    # Header: space-separated `vN,sig` entries (key rotation support)
    for part in webhook_signature.split(" "):
        version, _, sig = part.partition(",")
        if version != "v1" or not sig:
            continue
        if hmac.compare_digest(expected, sig):
            return True
    return False
