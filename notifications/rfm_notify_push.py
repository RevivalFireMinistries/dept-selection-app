"""rfm-notify push HTTP client (portal-side).

A tiny wrapper around the four push endpoints the portal needs:

  GET    /api/v1/channels/push/vapid-public-key   (no auth)
  POST   /api/v1/push-subscriptions/enroll        (X-API-Key)
  DELETE /api/v1/push-subscriptions/by-endpoint   (X-API-Key)
  POST   /api/v1/notify     (X-API-Key, channels=["push"], body_override=JSON payload)

Every call is best-effort and returns a (success, payload_or_error) tuple
so callers can degrade gracefully — push enrolment shouldn't block a
member from using the portal if rfm-notify is briefly down.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, Tuple

import requests

log = logging.getLogger(__name__)


def _config() -> Tuple[str, str, str]:
    url = os.getenv("RFM_NOTIFY_URL", "").rstrip("/")
    api_key = os.getenv("RFM_NOTIFY_API_KEY", "")
    app_code = os.getenv("RFM_NOTIFY_APP_CODE", "rfm-portal")
    return url, api_key, app_code


def is_configured() -> bool:
    url, api_key, _ = _config()
    return bool(url and api_key)


# --- Public key (browsers use this to subscribe) ---


def get_public_key() -> dict:
    """Fetch the VAPID public key. Returns
    {"configured": bool, "public_key": str|None}.
    Errors are swallowed and returned as configured=False — the portal
    surfaces "push not available" rather than crashing.
    """
    url, _, _ = _config()
    if not url:
        return {"configured": False, "public_key": None}
    try:
        r = requests.get(f"{url}/api/v1/channels/push/vapid-public-key", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {
                "configured": bool(data.get("configured")),
                "public_key": data.get("public_key"),
            }
        log.warning("[push-bridge] vapid-public-key non-200: %s", r.status_code)
    except requests.RequestException as exc:
        log.warning("[push-bridge] vapid-public-key fetch failed: %s", exc)
    return {"configured": False, "public_key": None}


# --- Subscription enrollment ---


def enroll_subscription(
    *,
    member_email: str,
    member_full_name: Optional[str],
    member_phone: Optional[str],
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: Optional[str] = None,
) -> Tuple[bool, Any]:
    """Forward the browser's subscription to rfm-notify.

    rfm-notify resolves-or-creates the recipient from email/phone so we
    don't have to track recipient UUIDs locally.
    """
    url, api_key, _ = _config()
    if not (url and api_key):
        return False, "rfm-notify not configured"
    payload = {
        "endpoint": endpoint,
        "p256dh": p256dh,
        "auth": auth,
        "user_agent": (user_agent or "")[:400] or None,
        "email": member_email or None,
        "phone": member_phone or None,
        "full_name": member_full_name or None,
    }
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    try:
        r = requests.post(
            f"{url}/api/v1/push-subscriptions/enroll",
            json=payload, headers=headers, timeout=15,
        )
    except requests.RequestException as exc:
        log.warning("[push-bridge] enroll failed: %s", exc)
        return False, f"network error: {exc}"
    if r.status_code >= 400:
        log.warning("[push-bridge] enroll %s -> %s", r.status_code, r.text[:200])
        return False, f"rfm-notify {r.status_code}: {r.text[:200]}"
    return True, r.json().get("data") or r.json()


def deactivate_endpoint(endpoint: str) -> Tuple[bool, Any]:
    """Tell rfm-notify to mark this endpoint inactive (user toggled push off)."""
    url, api_key, _ = _config()
    if not (url and api_key):
        return False, "rfm-notify not configured"
    headers = {"X-API-Key": api_key}
    try:
        r = requests.delete(
            f"{url}/api/v1/push-subscriptions/by-endpoint",
            params={"endpoint": endpoint},
            headers=headers,
            timeout=10,
        )
    except requests.RequestException as exc:
        return False, f"network error: {exc}"
    if r.status_code in (200, 204):
        return True, None
    return False, f"rfm-notify {r.status_code}: {r.text[:200]}"


# --- Sending a push for an event ---


def send_event_push(
    *,
    event_code: str,
    recipient_email: Optional[str],
    recipient_member_id: Any = None,   # accepted but only forwarded if UUID
    recipient_full_name: Optional[str],
    title: str,
    body: str,
    url: str = "/portal",
    tag: Optional[str] = None,
    icon: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Tuple[bool, Any]:
    """Fire one push via rfm-notify's notify endpoint.

    The push channel uses `body_override` carrying a JSON payload — the
    service worker on the receiving browser parses this and shows the
    notification. If we passed a templated event_code instead, rfm-notify
    would try to look up a push template, which we don't define on
    purpose: the portal already knows how it wants every event to look,
    same as for email.
    """
    notify_url, api_key, app_code = _config()
    if not (notify_url and api_key):
        return False, "rfm-notify not configured"
    if not recipient_email:
        return False, "recipient has no email — cannot resolve in rfm-notify"

    payload_json = {
        "title": title or "RFM Portal",
        "body": body or "",
        "url": url or "/portal",
    }
    if tag:
        payload_json["tag"] = tag
    if icon:
        payload_json["icon"] = icon

    recipient_block: dict = {"email": recipient_email}
    if recipient_full_name:
        recipient_block["full_name"] = recipient_full_name
    # Only forward member_id if it parses as a UUID — portal's int member.id
    # is meaningless to rfm-notify (it expects external_member_id UUIDs).
    if recipient_member_id is not None:
        import uuid as _uuid
        try:
            _uuid.UUID(str(recipient_member_id))
            recipient_block["member_id"] = str(recipient_member_id)
        except (ValueError, TypeError):
            pass

    body = {
        "app_code": app_code,
        "event_code": event_code,
        "recipient": recipient_block,
        "channels": ["push"],
        "body_override": json.dumps(payload_json),
        "subject_override": title,  # logged for the audit trail; push doesn't display it
        "priority": "normal",
    }
    if idempotency_key:
        body["idempotency_key"] = idempotency_key

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    try:
        r = requests.post(
            f"{notify_url}/api/v1/notify",
            json=body, headers=headers, timeout=15,
        )
    except requests.RequestException as exc:
        log.warning("[push-bridge] notify failed: %s", exc)
        return False, f"network error: {exc}"

    if r.status_code >= 400:
        log.warning("[push-bridge] notify %s -> %s", r.status_code, r.text[:200])
        return False, f"rfm-notify {r.status_code}: {r.text[:200]}"

    try:
        data = r.json()
    except ValueError:
        return True, None  # 2xx without body — accept

    dispatched = (data or {}).get("dispatched") or []
    push_results = [d for d in dispatched if d.get("channel") == "push"]
    if not push_results:
        return False, "no push branch dispatched (check Event Routes for channel=push)"
    first = push_results[0]
    if first.get("status") in ("sent", "already-sent"):
        return True, first
    return False, first.get("error") or f"push status: {first.get('status')}"
