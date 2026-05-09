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


def build_event_push(event_type, data: dict) -> Tuple[str, str, str]:
    """Map a notification EventType + data to (title, body, url) for push.
    Returns reasonable defaults for any unrecognised event so callers can
    just dispatch_event(...) without thinking about it."""
    et = getattr(event_type, "value", str(event_type))

    def _get(key, default=""):
        v = data.get(key)
        return str(v) if v not in (None, "") else default

    member = _get("recipient_name") or _get("member_name") or "you"
    title_body_url = {
        "member_approved": (
            "Selection approved",
            f"Your selection for {_get('department_name')} has been approved.",
            "/portal",
        ),
        "member_rejected": (
            "Selection update",
            f"Your selection for {_get('department_name')} wasn't approved. Tap to see details.",
            "/portal",
        ),
        "department_assigned": (
            "Assigned to a department",
            f"You've been added to {_get('department_name')}.",
            "/portal",
        ),
        "results_published": (
            "Results published",
            "Department selection results are now available.",
            "/portal",
        ),
        "appeal_submitted": (
            "New appeal",
            f"{_get('member_name', 'A member')} has submitted an appeal.",
            "/admin/appeals",
        ),
        "appeal_resolved": (
            "Appeal resolved",
            f"Your appeal has been {_get('status', 'reviewed')}.",
            "/portal",
        ),
        "meeting_created": (
            f"New meeting: {_get('title', 'check details')}",
            f"{_get('meeting_date')} · {_get('start_time')} · {_get('location') or 'see details'}",
            "/portal",
        ),
        "meeting_reminder": (
            f"Today: {_get('title', 'meeting')}",
            f"{_get('start_time')} · {_get('location') or 'see details'}",
            "/portal",
        ),
        "meeting_updated": (
            f"Updated: {_get('title', 'meeting')}",
            "Details have changed. Tap to see what's new.",
            "/portal",
        ),
        "meeting_cancelled": (
            f"Cancelled: {_get('title', 'meeting')}",
            "This meeting has been cancelled.",
            "/portal",
        ),
        "poster_request_submitted": (
            "New poster request",
            f"{_get('event_name', 'A new design')} needs your eyes.",
            "/admin/poster-requests",
        ),
        "poster_request_acknowledged": (
            "Poster in progress",
            f"The design team has picked up your request: {_get('event_name', '')}.",
            "/portal",
        ),
        "poster_request_completed": (
            "Poster ready",
            f"Your design for {_get('event_name', 'the event')} is ready.",
            "/portal",
        ),
        "program_participant_added": (
            f"You're on the program: {_get('title', 'service')}",
            f"{_get('service_date')} — tap for details.",
            "/portal",
        ),
        "home_church_roster_published": (
            "Home church roster",
            f"{_get('home_church_name')} on {_get('roster_date')} — see who's preaching.",
            "/portal",
        ),
        "home_church_preacher_assigned": (
            "You're preaching",
            f"{_get('home_church_name')} on {_get('roster_date')}.",
            "/portal",
        ),
        "home_church_reminder_leader": (
            "Home church tomorrow",
            "Quick reminder — your home church meets tomorrow.",
            "/portal",
        ),
        "home_church_reminder_preacher": (
            "Preaching tomorrow",
            f"You're preaching at {_get('home_church_name')} tomorrow.",
            "/portal",
        ),
        "home_church_attendance_reminder": (
            "Attendance reports pending",
            f"{_get('pending_count', '?')} home church reports still to capture.",
            "/admin/home-churches",
        ),
    }
    if et in title_body_url:
        return title_body_url[et]
    # Sensible default for any future event type we forget
    return ("Update", str(data.get("title") or "You have a new update."), "/portal")


def send_event_push(db: Session, event_type, data: dict, recipients: List[dict]) -> PushResult:
    """Push the same event we're emailing to every recipient with member_id."""
    result = PushResult()
    if not recipients:
        return result
    title, body, url = build_event_push(event_type, data)
    aggregated_subs: List[PushSubscription] = []
    seen_member_ids = set()
    for r in recipients:
        mid = r.get("id") or r.get("member_id")
        if not mid or mid in seen_member_ids:
            continue
        seen_member_ids.add(mid)
        subs = db.query(PushSubscription).filter(
            PushSubscription.member_id == int(mid),
            PushSubscription.is_enabled == True,
        ).all()
        aggregated_subs.extend(subs)
    if not aggregated_subs:
        return result
    return _dispatch(
        db, aggregated_subs,
        title=title, body=body, url=url,
        tag=f"event-{getattr(event_type, 'value', event_type)}",
        icon=None,
    )


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

    # pywebpush 2.x's webpush() interprets a string as base64url-encoded raw
    # private bytes (calls Vapid02.from_raw on it). Passing PEM blows up.
    # Build the Vapid02 instance from our PEM ourselves and hand it over —
    # webpush() detects the type and uses it directly.
    private_pem = _vapid_private_pem(db)
    if not private_pem:
        result.errors.append("VAPID private key could not be derived")
        return result
    try:
        from py_vapid import Vapid02
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        loaded = load_pem_private_key(private_pem.encode("ascii"), password=None, backend=default_backend())
        vapid = Vapid02()
        vapid._private_key = loaded
        vapid._public_key = loaded.public_key()
    except Exception as e:
        result.errors.append(f"Could not load VAPID key: {type(e).__name__}: {e}")
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

    import traceback as _tb
    for sub in list(subs):
        sub_info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh_key, "auth": sub.auth_key},
        }
        try:
            # Pre-flight: validate the subscriber's p256dh key. If it's malformed,
            # cryptography raises the same generic 'Could not deserialize key data'
            # later inside pywebpush — surfacing it here gives a clearer trail.
            try:
                _validate_p256dh(sub.p256dh_key)
            except Exception as ve:
                raise RuntimeError(f"subscription p256dh invalid: {ve}") from ve

            webpush(
                subscription_info=sub_info,
                data=payload_json,
                vapid_private_key=vapid,
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
                db.delete(sub)
                result.removed += 1
            else:
                sub.last_failed_at = datetime.utcnow()
                sub.last_error = err_msg[:400]
                result.failed += 1
                result.errors.append(err_msg)
        except Exception as e:
            # Capture chain + last frame so we know which step blew up
            cause = repr(e)
            tb = _tb.format_exc().splitlines()
            last_frame = next(
                (l for l in reversed(tb) if "site-packages" in l or "push_service" in l),
                tb[-2] if len(tb) > 1 else "",
            )
            err_msg = f"{type(e).__name__}: {str(e)[:160]} | at {last_frame.strip()[:160]}"
            sub.last_failed_at = datetime.utcnow()
            sub.last_error = err_msg[:400]
            result.failed += 1
            result.errors.append(err_msg)
            log.exception("Push send failed: %s", cause)
    db.commit()
    return result


def _validate_p256dh(p256dh: str) -> None:
    """Decode the subscriber's p256dh key and confirm cryptography accepts it
    as a P-256 uncompressed point (65 bytes starting with 0x04)."""
    if not p256dh:
        raise ValueError("missing p256dh")
    pad = "=" * (-len(p256dh) % 4)
    raw = base64.urlsafe_b64decode((p256dh + pad).encode("ascii"))
    if len(raw) != 65:
        raise ValueError(f"unexpected length {len(raw)} (expected 65 for uncompressed P-256)")
    if raw[0] != 0x04:
        raise ValueError(f"unexpected leading byte 0x{raw[0]:02x} (expected 0x04)")
    # Will raise if cryptography can't load it
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
