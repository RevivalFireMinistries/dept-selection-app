"""External API surface for other church systems (info desk, finance, etc.)
to request push notifications for our members.

Authentication
  X-API-Key: <token>   (one row per integrating system in external_api_keys)

Issued endpoints
  POST /api/external/push/notify     — fire a push to a single member
  POST /api/external/push/broadcast  — fire to many members at once
  GET  /api/external/whoami           — light health check, returns the key's
                                         name + scopes (handy for integrators)

Member resolution priority (any one of these in the body):
  - external_member_id : UUID from rfm-database (preferred — stable)
  - member_id          : local Member.id
  - phone              : e.g. '0721234567' (last 9 digits matched flexibly)

Admin issues / revokes keys at /api/admin/external-keys.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import ExternalApiKey, Member
import push_service


router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _key_from_request(request: Request) -> Optional[str]:
    return (
        request.headers.get("X-API-Key")
        or request.headers.get("x-api-key")
        or request.headers.get("X-Api-Key")
    )


def _require_key(request: Request, db: Session, action: str) -> ExternalApiKey:
    raw = _key_from_request(request)
    if not raw:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    api_key = db.query(ExternalApiKey).filter(
        ExternalApiKey.key == raw.strip(),
        ExternalApiKey.is_active == True,
    ).first()
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    try:
        scopes = json.loads(api_key.allowed_actions or "[]")
    except (ValueError, TypeError):
        scopes = []
    if action not in scopes:
        raise HTTPException(status_code=403, detail=f"This key cannot perform '{action}'")
    # Stamp usage
    api_key.last_used_at = datetime.utcnow()
    api_key.last_used_action = action[:60]
    api_key.last_used_ip = (request.client.host if request.client else "")[:60]
    api_key.use_count = (api_key.use_count or 0) + 1
    db.commit()
    return api_key


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------

def _normalise_phone_for_match(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return digits[-9:]  # last 9 digits — loose match


def _resolve_member(db: Session, *, external_member_id: Optional[str], member_id: Optional[int], phone: Optional[str]) -> Optional[Member]:
    if external_member_id:
        m = db.query(Member).filter(Member.external_member_id == str(external_member_id)).first()
        if m:
            return m
    if member_id:
        m = db.query(Member).filter(Member.id == int(member_id)).first()
        if m:
            return m
    if phone:
        target = _normalise_phone_for_match(phone)
        if target and len(target) >= 9:
            for m in db.query(Member).all():
                if _normalise_phone_for_match(m.phone or "") == target:
                    return m
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/external/whoami")
def external_whoami(request: Request, db: Session = Depends(get_db)):
    """Light health check — confirms the key works and returns its scopes."""
    raw = _key_from_request(request)
    if not raw:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    api_key = db.query(ExternalApiKey).filter(
        ExternalApiKey.key == raw.strip(),
        ExternalApiKey.is_active == True,
    ).first()
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    try:
        scopes = json.loads(api_key.allowed_actions or "[]")
    except (ValueError, TypeError):
        scopes = []
    return {
        "name": api_key.name,
        "scopes": scopes,
        "use_count": api_key.use_count,
        "last_used_at": api_key.last_used_at.isoformat() if api_key.last_used_at else None,
    }


@router.post("/external/push/notify")
def external_push_notify(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Fire a push notification to one member.

    Body:
      title  (required, <=200)
      body   (optional, <=300)
      url    (optional, defaults to /portal)
      tag    (optional, used to collapse duplicate notifications on the device)
      icon   (optional URL)

      And one of:
      external_member_id  — UUID from rfm-database
      member_id           — local Member.id
      phone               — '0721234567' or international format
    """
    api_key = _require_key(request, db, "push.notify")

    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    body = (payload.get("body") or "").strip()
    url = (payload.get("url") or "/portal").strip()
    tag = (payload.get("tag") or None)
    icon = (payload.get("icon") or None)

    member = _resolve_member(
        db,
        external_member_id=payload.get("external_member_id"),
        member_id=payload.get("member_id"),
        phone=payload.get("phone"),
    )
    if not member:
        raise HTTPException(
            status_code=404,
            detail="Member not found. Provide external_member_id, member_id, or phone.",
        )

    if not push_service.is_configured(db):
        raise HTTPException(status_code=503, detail="Push not configured on this portal")

    result = push_service.send_to_member(
        db, member.id,
        title=title[:200], body=body[:300], url=url, tag=tag, icon=icon,
    )

    return {
        "key_name": api_key.name,
        "member_id": member.id,
        "external_member_id": member.external_member_id,
        "subscriptions_sent": result.sent,
        "subscriptions_failed": result.failed,
        "subscriptions_removed": result.removed,
        "errors": result.errors[:3],
    }


@router.post("/external/push/broadcast")
def external_push_broadcast(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Fire to a list of recipients in one call. Same body as /notify but the
    recipient identifiers go into a `recipients` array:

      {
        "title": "...",
        "body": "...",
        "recipients": [
          { "external_member_id": "..." },
          { "phone": "0721234567" },
          { "member_id": 42 }
        ]
      }
    """
    api_key = _require_key(request, db, "push.notify")

    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    body = (payload.get("body") or "").strip()
    url = (payload.get("url") or "/portal").strip()
    tag = (payload.get("tag") or None)
    icon = (payload.get("icon") or None)

    recipients_raw = payload.get("recipients") or []
    if not isinstance(recipients_raw, list) or not recipients_raw:
        raise HTTPException(status_code=400, detail="recipients[] is required")
    if len(recipients_raw) > 500:
        raise HTTPException(status_code=400, detail="Max 500 recipients per call")

    if not push_service.is_configured(db):
        raise HTTPException(status_code=503, detail="Push not configured on this portal")

    summary = {"matched": 0, "missing": 0, "sent": 0, "failed": 0, "removed": 0}
    misses: List[dict] = []
    seen_member_ids = set()

    for r in recipients_raw:
        if not isinstance(r, dict):
            summary["missing"] += 1
            continue
        member = _resolve_member(
            db,
            external_member_id=r.get("external_member_id"),
            member_id=r.get("member_id"),
            phone=r.get("phone"),
        )
        if not member:
            summary["missing"] += 1
            if len(misses) < 10:
                misses.append(r)
            continue
        if member.id in seen_member_ids:
            continue
        seen_member_ids.add(member.id)
        summary["matched"] += 1
        result = push_service.send_to_member(
            db, member.id,
            title=title[:200], body=body[:300], url=url, tag=tag, icon=icon,
        )
        summary["sent"] += result.sent
        summary["failed"] += result.failed
        summary["removed"] += result.removed

    return {"key_name": api_key.name, "summary": summary, "missing_sample": misses}


# ---------------------------------------------------------------------------
# Admin: issue / revoke keys
# ---------------------------------------------------------------------------

def _require_admin(request: Request) -> None:
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")


def _serialize_key(k: ExternalApiKey) -> dict:
    try:
        scopes = json.loads(k.allowed_actions or "[]")
    except (ValueError, TypeError):
        scopes = []
    return {
        "id": k.id,
        "name": k.name,
        "description": k.description or "",
        "scopes": scopes,
        "is_active": bool(k.is_active),
        "use_count": k.use_count or 0,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "last_used_action": k.last_used_action,
        "last_used_ip": k.last_used_ip,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        # Mask the raw key on read so it never leaks back over /list
        "key_preview": (k.key or "")[:8] + "…" + (k.key or "")[-4:] if k.key else "",
    }


@router.get("/admin/external-keys")
def admin_list_keys(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    rows = db.query(ExternalApiKey).order_by(ExternalApiKey.created_at.desc()).all()
    return [_serialize_key(k) for k in rows]


@router.post("/admin/external-keys")
def admin_create_key(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Issue a new key. Returns the raw token ONCE in the response — admin must
    save it; subsequent reads only return a masked preview."""
    _require_admin(request)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    description = (payload.get("description") or "").strip() or None
    scopes = payload.get("scopes") or ["push.notify"]
    if not isinstance(scopes, list) or not scopes:
        raise HTTPException(status_code=400, detail="scopes must be a non-empty list")

    raw = "rfm_" + secrets.token_urlsafe(32)
    from routers.pages import get_admin_identity
    identity = get_admin_identity(request) or {}

    k = ExternalApiKey(
        name=name[:120],
        key=raw,
        description=description,
        allowed_actions=json.dumps([str(s) for s in scopes]),
        is_active=True,
        created_by_member_id=identity.get("member_id"),
    )
    db.add(k)
    db.commit()
    db.refresh(k)
    out = _serialize_key(k)
    out["key"] = raw  # one-time return; never shown again
    return out


@router.put("/admin/external-keys/{key_id}")
def admin_update_key(key_id: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    _require_admin(request)
    k = db.query(ExternalApiKey).filter(ExternalApiKey.id == key_id).first()
    if not k:
        raise HTTPException(status_code=404, detail="Key not found")
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if name:
            k.name = name[:120]
    if "description" in payload:
        k.description = (payload.get("description") or "").strip() or None
    if "scopes" in payload:
        scopes = payload.get("scopes") or []
        if isinstance(scopes, list) and scopes:
            k.allowed_actions = json.dumps([str(s) for s in scopes])
    if "is_active" in payload:
        k.is_active = bool(payload["is_active"])
    db.commit()
    db.refresh(k)
    return _serialize_key(k)


@router.post("/admin/external-keys/{key_id}/rotate")
def admin_rotate_key(key_id: int, request: Request, db: Session = Depends(get_db)):
    """Generate a new token for an existing key. The OLD token stops working
    immediately. Returns the new raw token ONCE."""
    _require_admin(request)
    k = db.query(ExternalApiKey).filter(ExternalApiKey.id == key_id).first()
    if not k:
        raise HTTPException(status_code=404, detail="Key not found")
    raw = "rfm_" + secrets.token_urlsafe(32)
    k.key = raw
    db.commit()
    db.refresh(k)
    out = _serialize_key(k)
    out["key"] = raw
    return out


@router.delete("/admin/external-keys/{key_id}")
def admin_delete_key(key_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    k = db.query(ExternalApiKey).filter(ExternalApiKey.id == key_id).first()
    if not k:
        raise HTTPException(status_code=404, detail="Key not found")
    db.delete(k)
    db.commit()
    return {"success": True}
