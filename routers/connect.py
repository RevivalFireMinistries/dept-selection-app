"""Public 'Connect' visitor card.

  POST /api/connect/submit            — public, no auth
  GET  /api/admin/connect-submissions — admin: list everyone who's connected
  PUT  /api/admin/connect-submissions/{id}  — update status / follow-up note
  DELETE /api/admin/connect-submissions/{id}

On every submission every admin (member with leadership_role 'admin' or
'pastor') gets a push notification with the visitor's name + phone."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import ConnectFormSubmission, Member


router = APIRouter()


VALID_GENDER = {"Male", "Female"}
VALID_HEARD = {"Friend/Family", "Social Media", "Walk-in", "Other"}
VALID_GROUP = {"Singles & Widowed", "Young Unmarried Adults", "Teens", "Ladies", "Men", "Couples"}
VALID_NEXT_STEPS = {
    "Give my life to Christ",
    "Learn more about the church",
    "Be allocated into a home-church",
    "Learn more about Christ",
    "Speak to a leader",
}
VALID_YES_NO = {"Yes", "No"}
VALID_CONSENT = {"I agree", "I do not agree"}


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _serialize(s: ConnectFormSubmission) -> dict:
    next_steps = []
    if s.next_steps:
        try:
            next_steps = json.loads(s.next_steps)
        except (ValueError, TypeError):
            next_steps = []
    return {
        "id": s.id,
        "full_name": s.full_name,
        "phone": s.phone,
        "email": s.email or "",
        "date_of_visit": s.date_of_visit.isoformat() if s.date_of_visit else None,
        "gender": s.gender or "",
        "first_time_visiting": s.first_time_visiting or "",
        "heard_about_us": s.heard_about_us or "",
        "ministry_group": s.ministry_group or "",
        "next_steps": next_steps,
        "prayer_requests": s.prayer_requests or "",
        "experience_rating": s.experience_rating,
        "preferred_contact_time": s.preferred_contact_time or "",
        "wants_updates": s.wants_updates or "",
        "contact_consent": s.contact_consent or "",
        "testimony": s.testimony or "",
        "status": s.status or "new",
        "follow_up_note": s.follow_up_note or "",
        "followed_up_by_name": s.followed_up_by.full_name if s.followed_up_by else None,
        "followed_up_at": s.followed_up_at.isoformat() if s.followed_up_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _notify_admins(db: Session, submission: ConnectFormSubmission) -> None:
    """Push every admin / pastor on a new connect-form submission. Best-effort."""
    try:
        import push_service
        if not push_service.is_configured(db):
            return
        # Find members whose leadership_roles include admin/pastor
        all_members = db.query(Member).filter(
            Member.is_active == True,
            Member.leadership_roles.isnot(None),
        ).all()
        target_ids: List[int] = []
        for m in all_members:
            try:
                roles = json.loads(m.leadership_roles) if isinstance(m.leadership_roles, str) else (m.leadership_roles or [])
            except (ValueError, TypeError):
                roles = []
            if isinstance(roles, list):
                roles_lower = [str(r).lower() for r in roles]
                if "admin" in roles_lower or "pastor" in roles_lower:
                    target_ids.append(m.id)
        if not target_ids:
            return
        title = "New connect card"
        body_parts = [submission.full_name]
        if submission.phone:
            body_parts.append(submission.phone)
        if submission.first_time_visiting == "Yes":
            body_parts.append("first visit")
        body = " · ".join(body_parts)
        for mid in target_ids:
            push_service.send_to_member(
                db, mid,
                title=title, body=body,
                url="/admin/connect-submissions",
                tag=f"connect-{submission.id}",
            )
    except Exception:
        pass  # never let push failure block the submission


# ---- Public endpoint ----

@router.post("/connect/submit")
def submit_connect_form(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Anyone can call this — no auth required."""
    full_name = (payload.get("full_name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    gender = (payload.get("gender") or "").strip()
    if gender and gender not in VALID_GENDER:
        gender = ""
    heard = (payload.get("heard_about_us") or "").strip()
    if heard and heard not in VALID_HEARD:
        heard = ""
    group = (payload.get("ministry_group") or "").strip()
    if group and group not in VALID_GROUP:
        group = ""
    consent = (payload.get("contact_consent") or "").strip()
    if consent and consent not in VALID_CONSENT:
        consent = ""

    next_steps_in = payload.get("next_steps") or []
    if not isinstance(next_steps_in, list):
        next_steps_in = []
    next_steps = [s for s in (str(x).strip() for x in next_steps_in) if s in VALID_NEXT_STEPS]

    rating = payload.get("experience_rating")
    try:
        rating = int(rating) if rating is not None and str(rating).strip() != "" else None
        if rating is not None and (rating < 1 or rating > 10):
            rating = None
    except (TypeError, ValueError):
        rating = None

    first_time = (payload.get("first_time_visiting") or "").strip()
    if first_time and first_time not in VALID_YES_NO:
        first_time = ""
    wants_updates = (payload.get("wants_updates") or "").strip()
    if wants_updates and wants_updates not in VALID_YES_NO:
        wants_updates = ""

    submission = ConnectFormSubmission(
        full_name=full_name[:200],
        phone=phone[:50],
        email=(payload.get("email") or "").strip()[:200] or None,
        date_of_visit=_parse_date(payload.get("date_of_visit")),
        gender=gender or None,
        first_time_visiting=first_time or None,
        heard_about_us=heard or None,
        ministry_group=group or None,
        next_steps=json.dumps(next_steps) if next_steps else None,
        prayer_requests=(payload.get("prayer_requests") or "").strip() or None,
        experience_rating=rating,
        preferred_contact_time=(payload.get("preferred_contact_time") or "").strip()[:20] or None,
        wants_updates=wants_updates or None,
        contact_consent=consent or None,
        testimony=(payload.get("testimony") or "").strip() or None,
        status="new",
        ip_address=(request.client.host if request and request.client else "")[:60],
        user_agent=(request.headers.get("user-agent") if request else "")[:400],
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    _notify_admins(db, submission)
    return {"success": True, "id": submission.id}


# ---- Admin endpoints ----

def _require_admin(request: Request) -> None:
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/admin/connect-submissions")
def admin_list_submissions(
    status: Optional[str] = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    _require_admin(request)
    q = db.query(ConnectFormSubmission).options(joinedload(ConnectFormSubmission.followed_up_by))
    if status:
        q = q.filter(ConnectFormSubmission.status == status)
    rows = q.order_by(ConnectFormSubmission.created_at.desc()).all()
    return [_serialize(r) for r in rows]


@router.put("/admin/connect-submissions/{sub_id}")
def admin_update_submission(
    sub_id: int,
    payload: dict = Body(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    _require_admin(request)
    s = db.query(ConnectFormSubmission).filter(ConnectFormSubmission.id == sub_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Submission not found")

    if "status" in payload:
        new_status = (payload.get("status") or "").strip().lower()
        if new_status not in {"new", "followed_up", "archived"}:
            raise HTTPException(status_code=400, detail="Invalid status")
        was_unactioned = s.status == "new"
        s.status = new_status
        if new_status == "followed_up" and was_unactioned:
            from routers.pages import get_admin_identity
            identity = get_admin_identity(request) or {}
            s.followed_up_by_id = identity.get("member_id")
            s.followed_up_at = datetime.utcnow()
    if "follow_up_note" in payload:
        s.follow_up_note = (payload.get("follow_up_note") or "").strip() or None
    db.commit()
    db.refresh(s)
    return _serialize(s)


@router.delete("/admin/connect-submissions/{sub_id}")
def admin_delete_submission(sub_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    s = db.query(ConnectFormSubmission).filter(ConnectFormSubmission.id == sub_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Submission not found")
    db.delete(s)
    db.commit()
    return {"success": True}
