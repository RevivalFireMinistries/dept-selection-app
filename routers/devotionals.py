"""Daily devotionals — admin / devotionals-team posts, members read.

Authorization
  - "Devotionals team": admin OR member with an approved department whose
    name contains the word 'devotional' (case-insensitive). Mirrors the
    Bible Reading Plan and Home Church Committee permission patterns.
  - Any logged-in member can fetch today's published devotional.

  GET  /api/portal/devotional/today           — public to logged-in members
  GET  /api/admin/devotionals                  — team: list (filter by status / month)
  POST /api/admin/devotionals                  — team: create
  GET  /api/admin/devotionals/{id}             — team: detail
  PUT  /api/admin/devotionals/{id}             — team: update
  DELETE /api/admin/devotionals/{id}           — team: delete
  POST /api/admin/devotionals/{id}/publish     — team: publish (sets published_at)
  POST /api/admin/devotionals/{id}/unpublish   — team: revert to draft
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Department, Devotional, Member, MemberDepartment


router = APIRouter()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _is_devotionals_team(db: Session, member: Member) -> bool:
    if not member:
        return False
    dept_ids = [
        d.id for d in db.query(Department).all()
        if "devotional" in (d.name or "").lower()
    ]
    if not dept_ids:
        return False
    return db.query(MemberDepartment).filter(
        MemberDepartment.member_id == member.id,
        MemberDepartment.department_id.in_(dept_ids),
        MemberDepartment.status == "approved",
    ).count() > 0


def _current_member(request: Request, db: Session) -> Optional[Member]:
    from routers.pages import get_current_member, get_admin_identity, is_authenticated
    if is_authenticated(request):
        identity = get_admin_identity(request)
        if identity and identity.get("member_id"):
            m = db.query(Member).filter(Member.id == identity["member_id"]).first()
            if m:
                return m
    return get_current_member(request, db)


def _require_team(request: Request, db: Session) -> Optional[Member]:
    from routers.pages import is_authenticated
    if is_authenticated(request):
        return _current_member(request, db)
    member = _current_member(request, db)
    if not member or not _is_devotionals_team(db, member):
        raise HTTPException(status_code=403, detail="Devotionals team access required")
    return member


def _require_member(request: Request, db: Session) -> Member:
    member = _current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")
    return member


def _serialize(d: Devotional) -> dict:
    return {
        "id": d.id,
        "scheduled_date": d.scheduled_date.isoformat() if d.scheduled_date else None,
        "title": d.title,
        "scripture_reference": d.scripture_reference or "",
        "scripture_text": d.scripture_text or "",
        "body": d.body or "",
        "author_name": d.author_name or (d.created_by.full_name if d.created_by else ""),
        "status": d.status,
        "published_at": d.published_at.isoformat() if d.published_at else None,
        "created_by": (
            {"id": d.created_by.id, "full_name": d.created_by.full_name}
            if d.created_by else None
        ),
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Member-facing
# ---------------------------------------------------------------------------

@router.get("/portal/devotional/today")
def portal_devotional_today(request: Request, db: Session = Depends(get_db)):
    """Today's published devotional, or null if none is scheduled."""
    _require_member(request, db)
    today = date.today()
    d = (
        db.query(Devotional)
        .options(joinedload(Devotional.created_by))
        .filter(Devotional.scheduled_date == today, Devotional.status == "published")
        .first()
    )
    if not d:
        return {"devotional": None, "date": today.isoformat()}
    return {"devotional": _serialize(d), "date": today.isoformat()}


# ---------------------------------------------------------------------------
# Team-facing
# ---------------------------------------------------------------------------

@router.get("/admin/devotionals")
def admin_list_devotionals(
    status: Optional[str] = Query(None),
    month: Optional[str] = Query(None, description="YYYY-MM"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    _require_team(request, db)
    q = db.query(Devotional).options(joinedload(Devotional.created_by))
    if status:
        q = q.filter(Devotional.status == status)
    if month:
        try:
            year_s, mon_s = month.split("-")
            yr, mo = int(year_s), int(mon_s)
            from calendar import monthrange
            first = date(yr, mo, 1)
            last = date(yr, mo, monthrange(yr, mo)[1])
            q = q.filter(Devotional.scheduled_date >= first, Devotional.scheduled_date <= last)
        except (ValueError, TypeError):
            pass
    rows = q.order_by(Devotional.scheduled_date.desc()).all()
    return [_serialize(r) for r in rows]


@router.post("/admin/devotionals")
def admin_create_devotional(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    actor = _require_team(request, db)

    scheduled_date = _parse_date(payload.get("scheduled_date"))
    if not scheduled_date:
        raise HTTPException(status_code=400, detail="scheduled_date is required (YYYY-MM-DD)")
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    body = (payload.get("body") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body (reflection) is required")

    if db.query(Devotional).filter(Devotional.scheduled_date == scheduled_date).first():
        raise HTTPException(status_code=400, detail="A devotional is already scheduled for that date — edit it instead.")

    d = Devotional(
        scheduled_date=scheduled_date,
        title=title[:200],
        scripture_reference=(payload.get("scripture_reference") or "").strip()[:120] or None,
        scripture_text=(payload.get("scripture_text") or "").strip() or None,
        body=body,
        author_name=(payload.get("author_name") or "").strip()[:120] or None,
        status="draft",
        created_by_member_id=actor.id if actor else None,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.get("/admin/devotionals/{dev_id}")
def admin_get_devotional(dev_id: int, request: Request, db: Session = Depends(get_db)):
    _require_team(request, db)
    d = db.query(Devotional).options(joinedload(Devotional.created_by)).filter(Devotional.id == dev_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Devotional not found")
    return _serialize(d)


@router.put("/admin/devotionals/{dev_id}")
def admin_update_devotional(dev_id: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    _require_team(request, db)
    d = db.query(Devotional).filter(Devotional.id == dev_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Devotional not found")

    if "scheduled_date" in payload:
        new_date = _parse_date(payload.get("scheduled_date"))
        if not new_date:
            raise HTTPException(status_code=400, detail="Invalid scheduled_date")
        if new_date != d.scheduled_date:
            clash = db.query(Devotional).filter(
                Devotional.scheduled_date == new_date,
                Devotional.id != d.id,
            ).first()
            if clash:
                raise HTTPException(status_code=400, detail="Another devotional is already scheduled for that date.")
        d.scheduled_date = new_date
    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        d.title = title[:200]
    if "scripture_reference" in payload:
        d.scripture_reference = (payload.get("scripture_reference") or "").strip()[:120] or None
    if "scripture_text" in payload:
        d.scripture_text = (payload.get("scripture_text") or "").strip() or None
    if "body" in payload:
        body = (payload.get("body") or "").strip()
        if not body:
            raise HTTPException(status_code=400, detail="body cannot be empty")
        d.body = body
    if "author_name" in payload:
        d.author_name = (payload.get("author_name") or "").strip()[:120] or None

    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.post("/admin/devotionals/{dev_id}/publish")
def admin_publish_devotional(dev_id: int, request: Request, db: Session = Depends(get_db)):
    _require_team(request, db)
    d = db.query(Devotional).filter(Devotional.id == dev_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Devotional not found")
    d.status = "published"
    d.published_at = datetime.utcnow()
    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.post("/admin/devotionals/{dev_id}/unpublish")
def admin_unpublish_devotional(dev_id: int, request: Request, db: Session = Depends(get_db)):
    _require_team(request, db)
    d = db.query(Devotional).filter(Devotional.id == dev_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Devotional not found")
    d.status = "draft"
    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.delete("/admin/devotionals/{dev_id}")
def admin_delete_devotional(dev_id: int, request: Request, db: Session = Depends(get_db)):
    _require_team(request, db)
    d = db.query(Devotional).filter(Devotional.id == dev_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Devotional not found")
    db.delete(d)
    db.commit()
    return {"success": True}
