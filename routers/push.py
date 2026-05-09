"""Web push endpoints.

  GET    /api/push/public-key      -> VAPID public key (browser needs it to subscribe)
  POST   /api/push/subscribe       -> save the browser's PushSubscription JSON
  DELETE /api/push/unsubscribe     -> remove the subscription for the current device
  GET    /api/push/me              -> status: configured? subscribed on this device?
  POST   /api/admin/push/test      -> send a test push to the admin's own subscriptions
  POST   /api/admin/push/vapid     -> generate / replace the VAPID keypair
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import Member, PushSubscription
import push_service


router = APIRouter()


def _current_member(request: Request, db: Session) -> Optional[Member]:
    from routers.pages import get_current_member, get_admin_identity, is_authenticated
    if is_authenticated(request):
        identity = get_admin_identity(request)
        if identity and identity.get("member_id"):
            m = db.query(Member).filter(Member.id == identity["member_id"]).first()
            if m:
                return m
    return get_current_member(request, db)


@router.get("/push/public-key")
def public_key(request: Request, db: Session = Depends(get_db)):
    """Public key for the browser's pushManager.subscribe call. Returns
    {key: null} if the server hasn't been configured yet."""
    return {
        "key": push_service.get_public_key(db) or None,
        "configured": push_service.is_configured(db),
    }


@router.get("/push/me")
def my_push_status(request: Request, db: Session = Depends(get_db)):
    member = _current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")
    sub_count = db.query(PushSubscription).filter(
        PushSubscription.member_id == member.id,
        PushSubscription.is_enabled == True,
    ).count()
    return {
        "configured": push_service.is_configured(db),
        "subscription_count": sub_count,
    }


@router.post("/push/subscribe")
def subscribe(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Save (or refresh) a browser-issued PushSubscription. The body shape
    matches what pushManager.subscribe(...) returns:
      { endpoint: '...', keys: { p256dh: '...', auth: '...' } }
    """
    member = _current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")

    endpoint = (payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise HTTPException(status_code=400, detail="endpoint, keys.p256dh, and keys.auth are required")

    user_agent = (request.headers.get("user-agent") or "")[:400] if request else ""

    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if existing:
        # Re-bind to current member (in case device is shared) and re-enable
        existing.member_id = member.id
        existing.p256dh_key = p256dh
        existing.auth_key = auth
        existing.user_agent = user_agent or existing.user_agent
        existing.is_enabled = True
        existing.last_seen_at = datetime.utcnow()
        existing.last_error = None
        existing.last_failed_at = None
        db.commit()
        return {"id": existing.id, "created": False}

    sub = PushSubscription(
        member_id=member.id,
        endpoint=endpoint,
        p256dh_key=p256dh,
        auth_key=auth,
        user_agent=user_agent,
        is_enabled=True,
        last_seen_at=datetime.utcnow(),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"id": sub.id, "created": True}


@router.delete("/push/unsubscribe")
def unsubscribe(payload: dict = Body(default={}), request: Request = None, db: Session = Depends(get_db)):
    """Remove the subscription for the given endpoint. Body: { endpoint: '...' }.
    Members can only delete their own subscriptions."""
    member = _current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")
    endpoint = (payload.get("endpoint") or "").strip() if payload else ""
    q = db.query(PushSubscription).filter(PushSubscription.member_id == member.id)
    if endpoint:
        q = q.filter(PushSubscription.endpoint == endpoint)
    rows = q.all()
    for r in rows:
        db.delete(r)
    db.commit()
    return {"removed": len(rows)}


# ---- Admin ----

@router.post("/admin/push/vapid")
def admin_generate_vapid(payload: dict = Body(default={}), request: Request = None, db: Session = Depends(get_db)):
    """Generate (or replace) the VAPID keypair. Force=true wipes the existing
    pair and invalidates every subscription, so use sparingly."""
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")
    subject = (payload.get("subject") or "").strip() or "mailto:admin@rfm.org.za"
    force = bool(payload.get("force", False))
    public_key, _ = push_service.generate_vapid_keys(db, subject=subject, force=force)
    return {
        "public_key": public_key,
        "subject": subject,
        "configured": True,
    }


@router.get("/admin/push/diagnostics")
def admin_push_diagnostics(request: Request, db: Session = Depends(get_db)):
    """Inspect the stored VAPID material and verify it loads cleanly.
    Returns shape info + a load test result so we can pinpoint why
    pywebpush is rejecting the key."""
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")

    raw_priv = push_service._get(db, "vapid_private_key")
    raw_pub = push_service._get(db, "vapid_public_key")
    subject = push_service._get(db, "vapid_subject")

    out = {
        "configured": push_service.is_configured(db),
        "subject": subject,
        "public_key_b64url_length": len(raw_pub or ""),
        "public_key_first": (raw_pub or "")[:12],
        "public_key_last": (raw_pub or "")[-12:],
        "private_key_length": len(raw_priv or ""),
        "private_key_starts_with_begin": (raw_priv or "").lstrip().startswith("-----BEGIN"),
        "private_key_first_line": (raw_priv or "").splitlines()[0] if raw_priv else "",
        "private_key_last_line": (raw_priv or "").splitlines()[-1] if raw_priv else "",
        "private_key_line_count": len((raw_priv or "").splitlines()),
        "load_test": None,
        "load_test_error": None,
    }

    # Try loading the PEM the same way pywebpush will
    try:
        from cryptography.hazmat.backends import default_backend as _be
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        pem = push_service._vapid_private_pem(db)
        if not pem:
            out["load_test"] = "no-pem"
        else:
            key = load_pem_private_key(pem.encode("ascii"), password=None, backend=_be())
            curve = getattr(getattr(key, "curve", None), "name", "?")
            out["load_test"] = "ok"
            out["load_test_curve"] = curve
            out["load_test_pem_first_line"] = pem.splitlines()[0]
    except Exception as e:
        out["load_test"] = "failed"
        out["load_test_error"] = f"{type(e).__name__}: {e}"

    return out


@router.get("/admin/push/subscriptions")
def admin_list_subs(request: Request, db: Session = Depends(get_db)):
    """Admin diagnostic: list every subscription, masked endpoint, last error."""
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")
    rows = db.query(PushSubscription).all()
    out = []
    for s in rows:
        endpoint = s.endpoint or ""
        # Pull just the host + last 6 chars of the path so we can identify
        # which push service this is for without leaking the full token
        try:
            from urllib.parse import urlparse
            u = urlparse(endpoint)
            tail = endpoint[-12:] if len(endpoint) > 12 else endpoint
            label = f"{u.netloc} …{tail}"
        except Exception:
            label = endpoint[:60]
        out.append({
            "id": s.id,
            "member_id": s.member_id,
            "endpoint_label": label,
            "user_agent": s.user_agent,
            "is_enabled": bool(s.is_enabled),
            "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            "last_failed_at": s.last_failed_at.isoformat() if s.last_failed_at else None,
            "last_error": s.last_error,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    return out


@router.post("/admin/push/test")
def admin_test_push(payload: dict = Body(default={}), request: Request = None, db: Session = Depends(get_db)):
    """Send a test push to the admin's own subscriptions (or to a member
    they specify)."""
    from routers.pages import is_authenticated, get_admin_identity
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")

    target_member_id = payload.get("member_id")
    if not target_member_id:
        identity = get_admin_identity(request) or {}
        target_member_id = identity.get("member_id")
    if not target_member_id:
        raise HTTPException(status_code=400, detail="No target member; provide member_id or sign in with an identity")

    title = (payload.get("title") or "RFM Portal — test push").strip()
    body = (payload.get("body") or "If you can read this, push notifications are working 🎉").strip()
    url = (payload.get("url") or "/portal").strip()

    result = push_service.send_to_member(db, int(target_member_id), title=title, body=body, url=url, tag="rfm-test")
    return {
        "target_member_id": int(target_member_id),
        "sent": result.sent,
        "failed": result.failed,
        "removed": result.removed,
        "errors": result.errors[:5],
    }
