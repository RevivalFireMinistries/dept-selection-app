"""Web push helpers built on pywebpush.

Configuration lives in the Settings table:
  vapid_public_key   — base64url-encoded uncompressed P-256 public key (87 chars)
  vapid_private_key  — base64url-encoded raw private key (43 chars)
  vapid_subject      — mailto: URI identifying the sender (e.g. mailto:russel@rfm.org.za)

generate_vapid_keys() creates a fresh keypair on demand. Idempotent — calling
twice without `force=True` is a no-op so the existing subscriptions don't get
invalidated by accident.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.orm import Session

from models import PushSubscription, Settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _get(db: Session, key: str) -> str:
    row = db.query(Settings).filter(Settings.key == key).first()
    return (row.value or "").strip() if row and row.value else ""


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Settings(key=key, value=value))


def is_configured(db: Session) -> bool:
    return bool(_get(db, "vapid_public_key") and _get(db, "vapid_private_key") and _get(db, "vapid_subject"))


def get_public_key(db: Session) -> str:
    return _get(db, "vapid_public_key")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# VAPID keypair generation
# ---------------------------------------------------------------------------

def generate_vapid_keys(db: Session, *, subject: str, force: bool = False) -> Tuple[str, str]:
    """Generate (or replace) the VAPID keypair stored in Settings.
    Returns (public_key_b64url, applicationServerKey_for_browser).

    Storage shape (post-fix):
      vapid_public_key   — 65-byte uncompressed point as base64url (browser uses this)
      vapid_private_key  — SEC1 PEM ('-----BEGIN EC PRIVATE KEY-----...') so we
                            hand it straight to pywebpush without reconstruction.
                            py_vapid is fussy about reconstructed PKCS8 PEMs.
    """
    if not subject:
        raise ValueError("subject is required (e.g. mailto:russel@rfm.org.za)")
    if not subject.startswith("mailto:") and not subject.startswith("http"):
        subject = f"mailto:{subject.strip()}"

    if not force and is_configured(db):
        # Refresh subject only if needed; keep the keys
        if _get(db, "vapid_subject") != subject:
            _set(db, "vapid_subject", subject)
            db.commit()
        return _get(db, "vapid_public_key"), _get(db, "vapid_public_key")

    # Generate a P-256 ECDSA keypair (Web Push spec)
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())

    # Public key: 65 bytes uncompressed (0x04 || X || Y) → base64url for the browser
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = _b64url(public_bytes)

    # Private key: SEC1 PEM (the format py_vapid loads cleanly).
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    _set(db, "vapid_public_key", public_b64)
    _set(db, "vapid_private_key", private_pem)
    _set(db, "vapid_subject", subject)
    db.commit()

    return public_b64, public_b64


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

@dataclass
class PushResult:
    sent: int = 0
    failed: int = 0
    removed: int = 0  # 410/404 => subscription wiped
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def _vapid_private_pem(db: Session) -> Optional[str]:
    """pywebpush wants the private key as a PEM string. New deployments store
    the SEC1 PEM directly. Legacy deployments (pre-2026-05-09) stored the raw
    32-byte private value as base64url; we still reconstruct that to a SEC1
    PEM so old keypairs keep working without forcing a regenerate."""
    raw = _get(db, "vapid_private_key")
    if not raw:
        return None
    raw = raw.strip()
    # New shape: PEM stored directly
    if raw.startswith("-----BEGIN"):
        return raw
    # Legacy shape: base64url of raw 32 bytes — reconstruct as SEC1
    try:
        pad = "=" * (-len(raw) % 4)
        priv_bytes = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        priv_int = int.from_bytes(priv_bytes, "big")
        private_key = ec.derive_private_key(priv_int, ec.SECP256R1(), default_backend())
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        return pem
    except Exception as e:
        log.exception("Could not derive VAPID private key PEM: %s", e)
        return None


def send_to_member(
    db: Session,
    member_id: int,
    *,
    title: str,
    body: str = "",
    url: Optional[str] = None,
    tag: Optional[str] = None,
    icon: Optional[str] = None,
) -> PushResult:
    """Push a message to every active subscription belonging to a member."""
    subs = db.query(PushSubscription).filter(
        PushSubscription.member_id == member_id,
        PushSubscription.is_enabled == True,
    ).all()
    return _dispatch(db, subs, title=title, body=body, url=url, tag=tag, icon=icon)


def send_to_all(
    db: Session,
    *,
    title: str,
    body: str = "",
    url: Optional[str] = None,
    tag: Optional[str] = None,
    icon: Optional[str] = None,
) -> PushResult:
    """Broadcast to every active subscription. Used for pinned announcements."""
    subs = db.query(PushSubscription).filter(PushSubscription.is_enabled == True).all()
    return _dispatch(db, subs, title=title, body=body, url=url, tag=tag, icon=icon)


def _dispatch(
    db: Session,
    subs: Iterable[PushSubscription],
    *,
    title: str,
    body: str,
    url: Optional[str],
    tag: Optional[str],
    icon: Optional[str],
) -> PushResult:
    result = PushResult()
    if not is_configured(db):
        result.errors.append("VAPID is not configured")
        return result

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        result.errors.append("pywebpush not installed")
        return result

    private_pem = _vapid_private_pem(db)
    if not private_pem:
        result.errors.append("VAPID private key could not be derived")
        return result
    subject = _get(db, "vapid_subject") or "mailto:admin@rfm.org.za"

    payload = {
        "title": title or "RFM Portal",
        "body": body or "",
        "url": url or "/portal",
    }
    if tag: payload["tag"] = tag
    if icon: payload["icon"] = icon

    payload_json = json.dumps(payload)

    for sub in list(subs):
        sub_info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh_key, "auth": sub.auth_key},
        }
        try:
            webpush(
                subscription_info=sub_info,
                data=payload_json,
                vapid_private_key=private_pem,
                vapid_claims={"sub": subject},
                ttl=24 * 3600,
            )
            sub.last_seen_at = datetime.utcnow()
            sub.last_error = None
            sub.last_failed_at = None
            result.sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            err_msg = f"HTTP {status}: {str(e)[:200]}" if status else str(e)[:200]
            if status in (404, 410):
                # Endpoint dead — remove the subscription
                db.delete(sub)
                result.removed += 1
            else:
                sub.last_failed_at = datetime.utcnow()
                sub.last_error = err_msg[:400]
                result.failed += 1
                result.errors.append(err_msg)
        except Exception as e:
            sub.last_failed_at = datetime.utcnow()
            sub.last_error = str(e)[:400]
            result.failed += 1
            result.errors.append(str(e)[:200])
    db.commit()
    return result
