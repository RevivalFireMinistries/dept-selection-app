"""
Prayer Requests — members and guests submit prayer requests (optionally
anonymous). The admin assigns recipients (per assembly) who are notified by
email and acknowledge receipt; admins can view all past requests and delete.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy.orm import Session

from database import get_db
from models import Member, Settings, PrayerRequest

router = APIRouter()

RECIPIENTS_KEY = "prayer_request_recipients"  # per-assembly: "<key>:<assembly_id>"

# Status flow: New (submitted) → Received (coordinator acknowledged) → Closed (prayed for)
PRAYER_STATUSES = ["new", "received", "closed"]


# ── Context helpers ───────────────────────────────────────────────────────────

def _default_assembly(db: Session) -> str | None:
    try:
        from routers.api import _resolve_default_assembly_id
        return _resolve_default_assembly_id(db)
    except Exception:
        return None


def _submit_assembly(request: Request, db: Session, member: Member | None) -> str | None:
    """Assembly to file a request under: submitter's own, else request state,
    else the deployment default (so guests still land somewhere)."""
    if member and getattr(member, "external_assembly_id", None):
        return str(member.external_assembly_id)
    assembly = getattr(request.state, "assembly", {}) or {}
    return str(assembly.get("id") or _default_assembly(db) or "") or None


def _recipient_ids(db: Session, assembly_id: str | None) -> list[int]:
    key = f"{RECIPIENTS_KEY}:{assembly_id or 'default'}"
    row = db.query(Settings).filter(Settings.key == key).first()
    if not row or not row.value:
        return []
    try:
        v = json.loads(row.value)
        return [int(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _set_recipient_ids(db: Session, assembly_id: str | None, ids: list[int]):
    key = f"{RECIPIENTS_KEY}:{assembly_id or 'default'}"
    row = db.query(Settings).filter(Settings.key == key).first()
    payload = json.dumps(sorted(set(int(i) for i in ids)))
    if row:
        row.value = payload
    else:
        db.add(Settings(key=key, value=payload))


def _manage_context(request: Request, db: Session):
    """Who is accessing the inbox. Returns (assembly_id, is_admin, member).
    Admins manage their own assembly; assigned recipients see + acknowledge
    their assembly's requests. Anyone else is refused."""
    from routers.pages import is_authenticated, get_admin_identity, get_current_member

    if is_authenticated(request):
        ident = get_admin_identity(request) or {}
        assembly_id = None
        mid = ident.get("member_id")
        if mid:
            m = db.query(Member).filter(Member.id == mid).first()
            if m and m.external_assembly_id:
                assembly_id = str(m.external_assembly_id)
        if not assembly_id:
            assembly = getattr(request.state, "assembly", {}) or {}
            assembly_id = str(assembly.get("id") or _default_assembly(db) or "") or None
        return assembly_id, True, None

    member = get_current_member(request, db)
    if member:
        assembly_id = str(getattr(member, "external_assembly_id", None) or _default_assembly(db) or "") or None
        if member.id in _recipient_ids(db, assembly_id):
            return assembly_id, False, member

    raise HTTPException(status_code=403, detail="You don't have access to prayer requests.")


def member_is_prayer_recipient(member: Member, db: Session) -> bool:
    """Drives the recipient-only 'Prayer requests inbox' portal menu item."""
    if not member:
        return False
    assembly_id = str(getattr(member, "external_assembly_id", None) or _default_assembly(db) or "") or None
    return member.id in _recipient_ids(db, assembly_id)


# ── Serialization ─────────────────────────────────────────────────────────────

def _req_dict(r: PrayerRequest, ack_names: dict) -> dict:
    return {
        "id": r.id,
        "is_anonymous": bool(r.is_anonymous),
        "name": None if r.is_anonymous else (r.name or None),
        "phone": None if r.is_anonymous else (r.phone or None),
        "email": None if r.is_anonymous else (r.email or None),
        "request_text": r.request_text,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
        "acknowledged_by": ack_names.get(r.acknowledged_by_member_id),
    }


# ── Public: submit ────────────────────────────────────────────────────────────

@router.post("/prayer-requests")
def submit_prayer_requests(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    """Submit one or more prayer requests. Works for guests and logged-in
    members; members may still submit anonymously."""
    from routers.pages import get_current_member

    texts = data.get("requests")
    if not isinstance(texts, list):
        texts = [data.get("request_text")]
    texts = [str(t or "").strip() for t in texts if str(t or "").strip()]
    if not texts:
        raise HTTPException(status_code=400, detail="Please enter at least one prayer request.")
    if len(texts) > 20:
        texts = texts[:20]

    is_anonymous = bool(data.get("is_anonymous"))
    member = get_current_member(request, db)

    if is_anonymous:
        name = phone = email = None
        member_id = None
    else:
        name = (data.get("name") or (member.full_name if member else "") or "").strip() or None
        phone = (data.get("phone") or (member.phone if member else "") or "").strip() or None
        email = (data.get("email") or (getattr(member, "email", None) if member else "") or "").strip() or None
        member_id = member.id if member else None

    assembly_id = _submit_assembly(request, db, member)

    created = []
    for t in texts:
        r = PrayerRequest(
            assembly_id=assembly_id, member_id=member_id, is_anonymous=is_anonymous,
            name=name, phone=phone, email=email, request_text=t, status="new",
        )
        db.add(r)
        created.append(r)
    db.commit()

    try:
        _notify_recipients(db, assembly_id, created)
    except Exception as e:
        try:
            print(f"Prayer-request notify failed: {e}")
        except Exception:
            pass

    return {"submitted": len(created)}


@router.get("/prayer-requests/mine")
def my_prayer_requests(request: Request, db: Session = Depends(get_db)):
    """The logged-in member's own recent (non-anonymous) requests + status."""
    from routers.pages import get_current_member
    member = get_current_member(request, db)
    if not member:
        return {"requests": []}
    rows = (
        db.query(PrayerRequest)
        .filter(PrayerRequest.member_id == member.id)
        .order_by(PrayerRequest.created_at.desc())
        .limit(5)
        .all()
    )
    return {"requests": [{
        "id": r.id,
        "request_text": r.request_text,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}


def _notify_recipients(db: Session, assembly_id: str | None, requests: list):
    """Email every assigned prayer coordinator that new requests came in.

    Coordinators are stored per-assembly. A guest submission may resolve to a
    different (or default) assembly than the one the admin assigned under, so we
    fall back to the default-assembly coordinators when the request's assembly
    has none — this guarantees someone is alerted."""
    if not requests:
        return
    ids = _recipient_ids(db, assembly_id)
    if not ids:
        default = _default_assembly(db)
        if default and str(default) != str(assembly_id or ""):
            ids = _recipient_ids(db, default)
    if not ids:
        try:
            print(f"[prayer-request] no coordinators assigned for assembly {assembly_id} — no alert sent")
        except Exception:
            pass
        return

    members = db.query(Member).filter(Member.id.in_(ids)).all()
    recipients = []
    for m in members:
        email = (getattr(m, "email", None) or "").strip()
        if email:
            recipients.append({"id": m.id, "email": email, "name": m.full_name, "phone": m.phone})
    if not recipients:
        try:
            print(f"[prayer-request] {len(members)} coordinator(s) assigned but none have an email on file")
        except Exception:
            pass
        return

    count = len(requests)
    first = requests[0]
    submitter = "Anonymous" if first.is_anonymous else (first.name or "A member")
    data = {
        "count": count,
        "submitter": submitter,
        "requests": [r.request_text for r in requests],
    }
    from notifications.dispatcher import dispatch_event
    from notifications.events import EventType
    dispatch_event(db, EventType.PRAYER_REQUEST_SUBMITTED, data, recipients)
    try:
        print(f"[prayer-request] alerted {len(recipients)} coordinator(s) for assembly {assembly_id}")
    except Exception:
        pass


# ── Inbox: admin + assigned recipients ────────────────────────────────────────

@router.get("/admin/prayer-requests")
def list_prayer_requests(request: Request, db: Session = Depends(get_db)):
    assembly_id, is_admin, _member = _manage_context(request, db)
    q = db.query(PrayerRequest)
    if assembly_id:
        q = q.filter(PrayerRequest.assembly_id == assembly_id)
    rows = q.order_by(PrayerRequest.created_at.desc()).all()

    ack_ids = {r.acknowledged_by_member_id for r in rows if r.acknowledged_by_member_id}
    ack_names: dict = {}
    if ack_ids:
        for m in db.query(Member).filter(Member.id.in_(ack_ids)).all():
            ack_names[m.id] = m.full_name

    return {
        "is_admin": is_admin,
        "new_count": sum(1 for r in rows if r.status == "new"),
        "requests": [_req_dict(r, ack_names) for r in rows],
    }


@router.post("/admin/prayer-requests/{req_id}/status")
def set_prayer_request_status(req_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    """Move a request through the flow: new → praying → answered / closed."""
    assembly_id, is_admin, member = _manage_context(request, db)
    status = (data.get("status") or "").strip()
    if status not in PRAYER_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    r = db.query(PrayerRequest).filter(PrayerRequest.id == req_id).first()
    if not r or (assembly_id and r.assembly_id and r.assembly_id != assembly_id):
        raise HTTPException(status_code=404, detail="Request not found")

    r.status = status
    # Stamp who last actioned it (first time it leaves "new" records receipt).
    handler_id = member.id if member else None
    if handler_id is None and is_admin:
        try:
            from routers.pages import get_admin_identity
            handler_id = (get_admin_identity(request) or {}).get("member_id")
        except Exception:
            handler_id = None
    if status == "new":
        r.acknowledged_at = None
        r.acknowledged_by_member_id = None
        r.reminder_sent_at = None  # let the nudge fire again if it goes stale
    else:
        if not r.acknowledged_at:
            r.acknowledged_at = datetime.now(timezone.utc)
        r.acknowledged_by_member_id = handler_id
    db.commit()
    return {"status": r.status, "id": r.id}


@router.delete("/admin/prayer-requests/{req_id}")
def delete_prayer_request(req_id: int, request: Request, db: Session = Depends(get_db)):
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    r = db.query(PrayerRequest).filter(PrayerRequest.id == req_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Request not found")
    db.delete(r)
    db.commit()
    return {"deleted": req_id}


# ── Recipient assignment (admin only) ─────────────────────────────────────────

def _require_admin(request: Request):
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin access required")


def _admin_assembly(request: Request, db: Session) -> str | None:
    from routers.pages import get_admin_identity
    ident = get_admin_identity(request) or {}
    mid = ident.get("member_id")
    if mid:
        m = db.query(Member).filter(Member.id == mid).first()
        if m and m.external_assembly_id:
            return str(m.external_assembly_id)
    assembly = getattr(request.state, "assembly", {}) or {}
    return str(assembly.get("id") or _default_assembly(db) or "") or None


@router.get("/admin/prayer-requests/recipients")
def get_recipients(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    assembly_id = _admin_assembly(request, db)
    ids = _recipient_ids(db, assembly_id)
    out = []
    if ids:
        for m in db.query(Member).filter(Member.id.in_(ids)).all():
            out.append({"member_id": m.id, "name": m.full_name, "phone": m.phone,
                        "email": getattr(m, "email", None)})
    return {"recipients": out}


@router.put("/admin/prayer-requests/recipients")
def set_recipients(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_admin(request)
    assembly_id = _admin_assembly(request, db)
    ids = data.get("member_ids") or []
    try:
        ids = [int(x) for x in ids]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid member ids")
    _set_recipient_ids(db, assembly_id, ids)
    db.commit()
    return {"count": len(set(ids))}


@router.get("/admin/prayer-requests/recipient-search")
def search_recipients(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Search local members (who have an email) to assign as recipients."""
    _require_admin(request)
    term = (q or "").strip().lower()
    if len(term) < 2:
        return {"results": []}
    rows = (
        db.query(Member)
        .filter(Member.full_name.ilike(f"%{term}%"))
        .order_by(Member.full_name)
        .limit(20)
        .all()
    )
    return {"results": [
        {"member_id": m.id, "name": m.full_name, "phone": m.phone,
         "email": getattr(m, "email", None)}
        for m in rows if (getattr(m, "email", None) or "").strip()
    ]}
