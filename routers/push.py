"""Web push endpoints — bridge into rfm-notify.

Post v1.x cutover: the portal no longer stores push subscriptions or
VAPID keys locally. These routes are thin proxies onto rfm-notify so
the PWA's service-worker bootstrap doesn't need to change. Every request
authenticates the member here (same login as before) and then forwards
to https://${RFM_NOTIFY_URL}/api/v1/push-subscriptions/...

  GET    /api/push/public-key      -> rfm-notify's VAPID public key
  POST   /api/push/subscribe       -> enrol via rfm-notify (recipient resolved by email)
  DELETE /api/push/unsubscribe     -> remove the subscription for a given endpoint
  GET    /api/push/me              -> "is push configured?" + a hint we can't see local enrolment
  POST   /api/admin/push/test      -> proxy onto rfm-notify's channel test
  POST   /api/admin/push/vapid     -> proxy onto rfm-notify's generate-vapid

The legacy local push_service / PushSubscription model is now dead
weight; remove after the backfill script (see scripts/migrate_push_to_notify.py)
has run and rfm-notify has at least one row per active enrolment.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import Member
from notifications import rfm_notify_push as bridge


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
    """VAPID public key for `pushManager.subscribe`. The PWA gets a
    `{configured: false, key: null}` shape if rfm-notify hasn't generated
    its keypair yet — the existing JS treats that as "feature disabled".
    """
    info = bridge.get_public_key()
    return {
        "key": info.get("public_key"),
        "configured": bool(info.get("configured")),
    }


@router.get("/push/me")
def my_push_status(request: Request, db: Session = Depends(get_db)):
    """Quick PWA status check. Since enrolment now lives in rfm-notify,
    we don't keep a per-device row here — the PWA can call this just
    to know whether push is available at all.
    """
    member = _current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")
    info = bridge.get_public_key()
    return {
        "configured": bool(info.get("configured")),
        # subscription_count is no longer authoritative on this side; the
        # PWA tracks its own pushManager state. Kept for shape compat.
        "subscription_count": None,
    }


@router.post("/push/subscribe")
def subscribe(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Save (or refresh) a browser-issued PushSubscription.

    The body shape matches what `pushManager.subscribe(...)` returns:
      { endpoint: '...', keys: { p256dh: '...', auth: '...' } }

    rfm-notify resolves the recipient from the member's email, so make
    sure profiles have an email on file before flipping push on.
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

    if not member.email:
        raise HTTPException(
            status_code=400,
            detail=(
                "Push enrolment requires an email on your profile so the "
                "notification service can match you. Add one in your settings, "
                "then try again."
            ),
        )

    user_agent = (request.headers.get("user-agent") or "")[:400] if request else ""
    success, info = bridge.enroll_subscription(
        member_email=member.email,
        member_full_name=member.full_name,
        member_phone=member.phone,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=user_agent,
    )
    if not success:
        raise HTTPException(status_code=502, detail=f"rfm-notify enrolment failed: {info}")
    return {
        "id": (info or {}).get("id") if isinstance(info, dict) else None,
        "created": True,
        "via": "rfm-notify",
    }


@router.delete("/push/unsubscribe")
def unsubscribe(payload: dict = Body(default={}), request: Request = None, db: Session = Depends(get_db)):
    """Remove the subscription for the given endpoint.
    Body: { endpoint: '...' }. Idempotent — unknown endpoint -> 200 with removed=0."""
    member = _current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")
    endpoint = (payload.get("endpoint") or "").strip() if payload else ""
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint is required")
    ok, err = bridge.deactivate_endpoint(endpoint)
    if not ok:
        raise HTTPException(status_code=502, detail=f"rfm-notify unsubscribe failed: {err}")
    return {"removed": 1}


# ---- Admin ----

@router.post("/admin/push/vapid")
def admin_generate_vapid(payload: dict = Body(default={}), request: Request = None, db: Session = Depends(get_db)):
    """VAPID keys live in rfm-notify now — admins should manage them in the
    rfm-notify console (Channels → Push → ⚙ VAPID). This endpoint stays
    around for muscle-memory and points admins at the new home.
    """
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")
    return {
        "moved": True,
        "message": (
            "VAPID keys are now managed in rfm-notify. Open the rfm-notify "
            "admin console → Channels → Push → click ⚙ VAPID to generate "
            "or rotate."
        ),
    }


@router.get("/admin/push/diagnostics")
def admin_push_diagnostics(request: Request, db: Session = Depends(get_db)):
    """Lightweight diagnostic that confirms the bridge to rfm-notify is up.
    For full subscription/key inspection, use the rfm-notify admin console."""
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")
    info = bridge.get_public_key()
    return {
        "configured": bool(info.get("configured")),
        "public_key_length": len((info.get("public_key") or "")),
        "bridge_ok": bridge.is_configured(),
        "note": "Subscription and VAPID details now live in the rfm-notify admin console.",
    }


@router.get("/admin/push/subscriptions")
def admin_list_subs(request: Request, db: Session = Depends(get_db)):
    """Subscriptions are kept in rfm-notify; redirect admins to its console."""
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")
    return {
        "moved": True,
        "message": (
            "Push subscriptions live in rfm-notify now. Open Channels → Push → "
            "Subscriptions in the rfm-notify admin console for the live list."
        ),
    }


@router.post("/admin/push/test")
def admin_test_push(payload: dict = Body(default={}), request: Request = None, db: Session = Depends(get_db)):
    """Send a test push to the admin's own subscriptions via rfm-notify.

    We dispatch as event_code=`portal.admin_test` with channels=["push"]
    so it doesn't require a route or template — body_override carries
    the JSON payload the service worker renders.
    """
    from routers.pages import is_authenticated, get_admin_identity
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")

    target_member_id = payload.get("member_id")
    if not target_member_id:
        identity = get_admin_identity(request) or {}
        target_member_id = identity.get("member_id")
    if not target_member_id:
        raise HTTPException(status_code=400, detail="No target member; provide member_id or sign in with an identity")

    member = db.query(Member).filter(Member.id == int(target_member_id)).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if not member.email:
        raise HTTPException(
            status_code=400,
            detail="Test target has no email on file — cannot resolve recipient in rfm-notify.",
        )

    title = (payload.get("title") or "RFM Portal — test push").strip()
    body = (payload.get("body") or "If you can read this, push notifications are working 🎉").strip()
    url = (payload.get("url") or "/portal").strip()

    ok, info = bridge.send_event_push(
        event_code="portal.admin_test",
        recipient_email=member.email,
        recipient_full_name=member.full_name,
        title=title,
        body=body,
        url=url,
        tag="rfm-test",
        idempotency_key=None,  # test sends shouldn't dedupe — they want to land every time
    )
    if not ok:
        return {"target_member_id": int(target_member_id), "sent": 0, "failed": 1, "removed": 0, "errors": [str(info)]}
    return {"target_member_id": int(target_member_id), "sent": 1, "failed": 0, "removed": 0, "errors": []}
