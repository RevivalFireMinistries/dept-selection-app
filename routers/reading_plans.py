"""Bible reading plans.

Authorization
  - "Reading plan team": admin OR member with an approved department whose
    name contains "bible" and "reading" (case-insensitive). Allowed to CRUD
    plans, publish/archive, clone, view metrics.
  - Any logged-in member can list published plans, follow/unfollow,
    mark days complete.
"""
from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import (
    Department,
    Member,
    MemberDepartment,
    ReadingPlan,
    ReadingPlanDay,
    PlanFollower,
    DayCompletion,
)


router = APIRouter()


VALID_PLAN_TYPES = {"BOOK", "PERSON", "THEME", "CHRONOLOGICAL", "DEVOTIONAL", "CUSTOM"}
VALID_CADENCES = {"DAILY", "WEEKDAYS_ONLY", "WEEKLY", "MEMBER_PACED"}
VALID_AUDIENCES = {"ADULTS", "NEW_BELIEVERS", "FAMILY", "YOUTH"}
VALID_VISIBILITIES = {"PUBLIC", "INTERNAL"}


# ---------------------------------------------------------------------------
# Auth + helpers
# ---------------------------------------------------------------------------

def _is_reading_plan_team(db: Session, member: Member) -> bool:
    """Member is on the Bible Reading Plan team (matched by department name)."""
    if not member:
        return False
    dept_ids = [
        d.id for d in db.query(Department).all()
        if "bible" in (d.name or "").lower() and "reading" in (d.name or "").lower()
    ]
    if not dept_ids:
        return False
    return db.query(MemberDepartment).filter(
        MemberDepartment.member_id == member.id,
        MemberDepartment.department_id.in_(dept_ids),
        MemberDepartment.status == "approved",
    ).count() > 0


def _is_admin(request: Request) -> bool:
    from routers.pages import is_authenticated
    return is_authenticated(request)


def _current_member(request: Request, db: Session) -> Optional[Member]:
    """Best-effort: returns the acting Member regardless of admin vs member session."""
    from routers.pages import get_current_member, get_admin_identity, is_authenticated
    if is_authenticated(request):
        identity = get_admin_identity(request)
        if identity and identity.get("member_id"):
            m = db.query(Member).filter(Member.id == identity["member_id"]).first()
            if m:
                return m
    return get_current_member(request, db)


def _require_team(request: Request, db: Session) -> Optional[Member]:
    """Allow admin (always) or a Bible Reading Plan team member."""
    if _is_admin(request):
        return _current_member(request, db)
    member = _current_member(request, db)
    if not member or not _is_reading_plan_team(db, member):
        raise HTTPException(status_code=403, detail="Bible Reading Plan team access required")
    return member


def _require_member(request: Request, db: Session) -> Member:
    member = _current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")
    return member


def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:60] or "plan"


def _ensure_unique_slug(db: Session, base: str) -> str:
    """Append a short random suffix if the slug collides."""
    candidate = base
    if not db.query(ReadingPlan).filter(ReadingPlan.slug == candidate).first():
        return candidate
    for _ in range(5):
        suffix = secrets.token_hex(2)
        candidate = f"{base}-{suffix}"[:80]
        if not db.query(ReadingPlan).filter(ReadingPlan.slug == candidate).first():
            return candidate
    return f"{base}-{uuid.uuid4().hex[:8]}"[:80]


def _tags_to_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except (ValueError, TypeError):
            pass
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _serialize_day(d: ReadingPlanDay) -> dict:
    return {
        "id": d.id,
        "day_number": d.day_number,
        "passages": d.passages or "",
        "theme": d.theme or "",
    }


def _serialize_plan(p: ReadingPlan, *, include_days: bool = False) -> dict:
    out = {
        "id": p.id,
        "title": p.title,
        "slug": p.slug,
        "description": p.description or "",
        "cover_emoji": p.cover_emoji or "",
        "plan_type": p.plan_type,
        "cadence": p.cadence,
        "duration_days": p.duration_days,
        "audience": p.audience or "",
        "tags": _tags_to_list(p.tags),
        "status": p.status,
        "visibility": p.visibility,
        "featured": bool(p.featured),
        "is_default": bool(p.is_default),
        "created_by": (
            {"id": p.created_by.id, "full_name": p.created_by.full_name}
            if p.created_by else None
        ),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
    }
    if include_days:
        out["days"] = [_serialize_day(d) for d in sorted(p.days, key=lambda x: x.day_number)]
    return out


def _validate_day_payload(d: dict) -> Tuple[Optional[dict], Optional[str]]:
    """Returns (clean, error). day_number is auto-assigned by position later."""
    passages = (d.get("passages") or "").strip()
    if not passages:
        return None, "Each day needs at least one passage"
    return {
        "passages": passages,
        "theme": (d.get("theme") or "").strip()[:200] or None,
    }, None


def _replace_days(plan: ReadingPlan, days_payload: List[dict], db: Session) -> None:
    """Wipe and recreate the day rows. Day numbers come from position, 1..N."""
    cleaned: List[dict] = []
    for entry in (days_payload or []):
        clean, err = _validate_day_payload(entry)
        if err:
            raise HTTPException(status_code=400, detail=err)
        cleaned.append(clean)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Add at least one day")
    db.query(ReadingPlanDay).filter(ReadingPlanDay.plan_id == plan.id).delete()
    db.flush()
    for idx, c in enumerate(cleaned, start=1):
        db.add(ReadingPlanDay(
            plan_id=plan.id,
            day_number=idx,
            passages=c["passages"],
            theme=c["theme"],
        ))
    plan.duration_days = len(cleaned)


def _follower_progress(follower: PlanFollower, plan: ReadingPlan) -> dict:
    completed_days = sorted({c.day_number for c in follower.completions})
    completed_count = len(completed_days)
    duration = plan.duration_days or 1
    return {
        "follower_id": follower.id,
        "started_on": follower.started_on.isoformat() if follower.started_on else None,
        "current_day": follower.current_day,
        "completed_count": completed_count,
        "completed_days": completed_days,
        "completion_pct": int(round((completed_count / duration) * 100)),
        "completed_at": follower.completed_at.isoformat() if follower.completed_at else None,
        "last_active_at": follower.last_active_at.isoformat() if follower.last_active_at else None,
    }


def _streak_for(follower: PlanFollower) -> int:
    """Consecutive days with completions, ending today (calendar day count)."""
    if not follower.completions:
        return 0
    days_seen = sorted({c.completed_at.date() for c in follower.completions if c.completed_at}, reverse=True)
    if not days_seen:
        return 0
    today = date.today()
    streak = 0
    cursor = today
    # Allow a 1-day grace if they read yesterday but not today yet
    if days_seen[0] not in (today, today - timedelta(days=1)):
        return 0
    for d in days_seen:
        if d == cursor:
            streak += 1
            cursor -= timedelta(days=1)
        elif d == cursor - timedelta(days=1) and streak == 0:
            # rare: skip today, start at yesterday
            streak += 1
            cursor = d - timedelta(days=1)
        elif d < cursor:
            break
    return streak


# ---------------------------------------------------------------------------
# Plan CRUD (team-only writes)
# ---------------------------------------------------------------------------

@router.get("/reading-plans")
def list_plans(
    request: Request,
    status: Optional[str] = Query(None),
    plan_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List plans. Team sees everything; members see published only."""
    is_team = False
    if _is_admin(request):
        is_team = True
    else:
        member = _current_member(request, db)
        if member and _is_reading_plan_team(db, member):
            is_team = True
    q = db.query(ReadingPlan).options(joinedload(ReadingPlan.created_by))
    if not is_team:
        q = q.filter(ReadingPlan.status == "published")
    elif status:
        q = q.filter(ReadingPlan.status == status)
    if plan_type:
        q = q.filter(ReadingPlan.plan_type == plan_type.upper())
    rows = q.order_by(ReadingPlan.featured.desc(), ReadingPlan.created_at.desc()).all()
    return {
        "scope": "team" if is_team else "member",
        "plans": [_serialize_plan(p) for p in rows],
    }


@router.post("/reading-plans")
def create_plan(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    actor = _require_team(request, db)
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    plan_type = (payload.get("plan_type") or "CUSTOM").upper()
    if plan_type not in VALID_PLAN_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown plan_type")
    cadence = (payload.get("cadence") or "MEMBER_PACED").upper()
    if cadence not in VALID_CADENCES:
        raise HTTPException(status_code=400, detail=f"Unknown cadence")
    audience = (payload.get("audience") or "").upper() or None
    if audience and audience not in VALID_AUDIENCES:
        raise HTTPException(status_code=400, detail=f"Unknown audience")
    visibility = (payload.get("visibility") or "INTERNAL").upper()
    if visibility not in VALID_VISIBILITIES:
        raise HTTPException(status_code=400, detail=f"Unknown visibility")

    days_payload = payload.get("days") or []
    if not isinstance(days_payload, list) or not days_payload:
        raise HTTPException(status_code=400, detail="Add at least one day")

    base_slug = _slugify(payload.get("slug") or title)
    slug = _ensure_unique_slug(db, base_slug)

    plan = ReadingPlan(
        title=title[:200],
        slug=slug,
        description=(payload.get("description") or "").strip() or None,
        cover_emoji=(payload.get("cover_emoji") or "").strip()[:10] or None,
        plan_type=plan_type,
        cadence=cadence,
        audience=audience,
        tags=json.dumps(_tags_to_list(payload.get("tags"))) if payload.get("tags") else None,
        visibility=visibility,
        featured=bool(payload.get("featured", False)),
        is_default=bool(payload.get("is_default", False)),
        status="draft",
        duration_days=0,  # set by _replace_days
        created_by_member_id=actor.id if actor else None,
    )
    db.add(plan)
    db.flush()
    _replace_days(plan, days_payload, db)
    db.commit()
    db.refresh(plan)
    return _serialize_plan(plan, include_days=True)


@router.get("/reading-plans/me")
def my_plans(request: Request, db: Session = Depends(get_db)):
    """The logged-in member's followed plans + today's recommended day for each."""
    member = _require_member(request, db)
    rows = (
        db.query(PlanFollower)
        .options(
            joinedload(PlanFollower.plan).joinedload(ReadingPlan.days),
            joinedload(PlanFollower.completions),
        )
        .filter(PlanFollower.member_id == member.id)
        .order_by(PlanFollower.created_at.desc())
        .all()
    )
    out = []
    for f in rows:
        if not f.plan or f.plan.status != "published":
            continue
        completed = sorted({c.day_number for c in f.completions})
        next_day_num = next(
            (n for n in range(1, f.plan.duration_days + 1) if n not in completed),
            None,
        )
        next_day = None
        if next_day_num:
            day = next((d for d in f.plan.days if d.day_number == next_day_num), None)
            if day:
                next_day = _serialize_day(day)
        out.append({
            "plan": _serialize_plan(f.plan),
            "progress": _follower_progress(f, f.plan),
            "streak": _streak_for(f),
            "next_day": next_day,
        })
    return {"plans": out}


@router.get("/reading-plans/{plan_id}")
def get_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    plan = (
        db.query(ReadingPlan)
        .options(joinedload(ReadingPlan.created_by), joinedload(ReadingPlan.days))
        .filter(ReadingPlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status != "published":
        # Only the team can see drafts/archived
        member = _current_member(request, db)
        if not (_is_admin(request) or (member and _is_reading_plan_team(db, member))):
            raise HTTPException(status_code=404, detail="Plan not found")
    out = _serialize_plan(plan, include_days=True)
    # If the caller is a follower, attach their progress
    member = _current_member(request, db)
    if member:
        f = db.query(PlanFollower).options(joinedload(PlanFollower.completions)).filter(
            PlanFollower.plan_id == plan.id,
            PlanFollower.member_id == member.id,
        ).first()
        if f:
            out["my_progress"] = _follower_progress(f, plan)
            out["my_streak"] = _streak_for(f)
            out["am_following"] = True
        else:
            out["am_following"] = False
    out["follower_count"] = db.query(PlanFollower).filter(PlanFollower.plan_id == plan.id).count()
    return out


@router.put("/reading-plans/{plan_id}")
def update_plan(plan_id: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    _require_team(request, db)
    plan = db.query(ReadingPlan).filter(ReadingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        plan.title = title[:200]
    if "description" in payload:
        plan.description = (payload.get("description") or "").strip() or None
    if "cover_emoji" in payload:
        plan.cover_emoji = (payload.get("cover_emoji") or "").strip()[:10] or None
    if "plan_type" in payload:
        v = (payload["plan_type"] or "").upper()
        if v not in VALID_PLAN_TYPES:
            raise HTTPException(status_code=400, detail="Unknown plan_type")
        plan.plan_type = v
    if "cadence" in payload:
        v = (payload["cadence"] or "").upper()
        if v not in VALID_CADENCES:
            raise HTTPException(status_code=400, detail="Unknown cadence")
        plan.cadence = v
    if "audience" in payload:
        v = (payload["audience"] or "").upper() or None
        if v and v not in VALID_AUDIENCES:
            raise HTTPException(status_code=400, detail="Unknown audience")
        plan.audience = v
    if "visibility" in payload:
        v = (payload["visibility"] or "").upper()
        if v not in VALID_VISIBILITIES:
            raise HTTPException(status_code=400, detail="Unknown visibility")
        plan.visibility = v
    if "tags" in payload:
        tag_list = _tags_to_list(payload.get("tags"))
        plan.tags = json.dumps(tag_list) if tag_list else None
    if "featured" in payload:
        plan.featured = bool(payload["featured"])
    if "is_default" in payload:
        plan.is_default = bool(payload["is_default"])

    if "days" in payload:
        # Replacing days while followers exist is allowed but may shift their progress.
        _replace_days(plan, payload["days"] or [], db)

    db.commit()
    db.refresh(plan)
    return _serialize_plan(plan, include_days=True)


@router.delete("/reading-plans/{plan_id}")
def delete_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    _require_team(request, db)
    plan = db.query(ReadingPlan).filter(ReadingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    db.delete(plan)
    db.commit()
    return {"success": True}


@router.post("/reading-plans/{plan_id}/publish")
def publish_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    _require_team(request, db)
    plan = db.query(ReadingPlan).filter(ReadingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not plan.duration_days:
        raise HTTPException(status_code=400, detail="Add days before publishing")
    plan.status = "published"
    plan.published_at = datetime.utcnow()
    plan.archived_at = None
    db.commit()
    return _serialize_plan(plan, include_days=True)


@router.post("/reading-plans/{plan_id}/archive")
def archive_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    _require_team(request, db)
    plan = db.query(ReadingPlan).filter(ReadingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan.status = "archived"
    plan.archived_at = datetime.utcnow()
    db.commit()
    return _serialize_plan(plan)


@router.post("/reading-plans/{plan_id}/clone")
def clone_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    actor = _require_team(request, db)
    src = (
        db.query(ReadingPlan)
        .options(joinedload(ReadingPlan.days))
        .filter(ReadingPlan.id == plan_id)
        .first()
    )
    if not src:
        raise HTTPException(status_code=404, detail="Plan not found")
    new_title = f"{src.title} (copy)"
    new_slug = _ensure_unique_slug(db, _slugify(new_title))
    cloned = ReadingPlan(
        title=new_title[:200],
        slug=new_slug,
        description=src.description,
        cover_emoji=src.cover_emoji,
        plan_type=src.plan_type,
        cadence=src.cadence,
        audience=src.audience,
        tags=src.tags,
        visibility=src.visibility,
        featured=False,
        is_default=False,
        status="draft",
        duration_days=src.duration_days,
        created_by_member_id=actor.id if actor else None,
    )
    db.add(cloned)
    db.flush()
    for d in src.days:
        db.add(ReadingPlanDay(
            plan_id=cloned.id,
            day_number=d.day_number,
            passages=d.passages,
            theme=d.theme,
        ))
    db.commit()
    db.refresh(cloned)
    return _serialize_plan(cloned, include_days=True)


# ---------------------------------------------------------------------------
# Member follow / mark-day-complete
# ---------------------------------------------------------------------------

def _get_or_create_follower(plan: ReadingPlan, member: Member, db: Session) -> PlanFollower:
    f = db.query(PlanFollower).filter(
        PlanFollower.plan_id == plan.id,
        PlanFollower.member_id == member.id,
    ).first()
    if f:
        return f
    f = PlanFollower(
        plan_id=plan.id,
        member_id=member.id,
        started_on=date.today(),
        current_day=1,
    )
    db.add(f)
    db.flush()
    return f


@router.post("/reading-plans/{plan_id}/follow")
def follow_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    member = _require_member(request, db)
    plan = db.query(ReadingPlan).filter(ReadingPlan.id == plan_id).first()
    if not plan or plan.status != "published":
        raise HTTPException(status_code=404, detail="Plan not available")
    f = _get_or_create_follower(plan, member, db)
    db.commit()
    return {"follower_id": f.id, "plan_id": plan.id, "started_on": f.started_on.isoformat()}


@router.delete("/reading-plans/{plan_id}/follow")
def unfollow_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    member = _require_member(request, db)
    f = db.query(PlanFollower).filter(
        PlanFollower.plan_id == plan_id,
        PlanFollower.member_id == member.id,
    ).first()
    if f:
        db.delete(f)
        db.commit()
    return {"success": True}


@router.post("/reading-plans/{plan_id}/days/{day_number}/complete")
def complete_day(plan_id: int, day_number: int, request: Request, db: Session = Depends(get_db)):
    member = _require_member(request, db)
    plan = db.query(ReadingPlan).filter(ReadingPlan.id == plan_id).first()
    if not plan or plan.status != "published":
        raise HTTPException(status_code=404, detail="Plan not available")
    if day_number < 1 or day_number > plan.duration_days:
        raise HTTPException(status_code=400, detail="Day out of range")
    f = _get_or_create_follower(plan, member, db)

    existing = db.query(DayCompletion).filter(
        DayCompletion.follower_id == f.id,
        DayCompletion.day_number == day_number,
    ).first()
    if not existing:
        db.add(DayCompletion(follower_id=f.id, day_number=day_number))
    f.last_active_at = datetime.utcnow()
    # Advance current_day to the smallest unfinished day
    db.flush()
    completed = {c.day_number for c in db.query(DayCompletion).filter(DayCompletion.follower_id == f.id).all()}
    next_day = next((n for n in range(1, plan.duration_days + 1) if n not in completed), None)
    if next_day is None:
        f.completed_at = datetime.utcnow()
        f.current_day = plan.duration_days
    else:
        f.current_day = next_day
        f.completed_at = None
    db.commit()
    db.refresh(f)
    return {
        "progress": _follower_progress(f, plan),
        "streak": _streak_for(f),
    }


@router.delete("/reading-plans/{plan_id}/days/{day_number}/complete")
def uncomplete_day(plan_id: int, day_number: int, request: Request, db: Session = Depends(get_db)):
    member = _require_member(request, db)
    f = db.query(PlanFollower).filter(
        PlanFollower.plan_id == plan_id,
        PlanFollower.member_id == member.id,
    ).first()
    if not f:
        raise HTTPException(status_code=404, detail="Not following this plan")
    db.query(DayCompletion).filter(
        DayCompletion.follower_id == f.id,
        DayCompletion.day_number == day_number,
    ).delete()
    plan = db.query(ReadingPlan).filter(ReadingPlan.id == plan_id).first()
    if plan:
        completed = {c.day_number for c in db.query(DayCompletion).filter(DayCompletion.follower_id == f.id).all()}
        next_day = next((n for n in range(1, plan.duration_days + 1) if n not in completed), None)
        if next_day is None:
            f.completed_at = datetime.utcnow()
            f.current_day = plan.duration_days
        else:
            f.completed_at = None
            f.current_day = next_day
    db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# Team metrics
# ---------------------------------------------------------------------------

@router.get("/reading-plans/{plan_id}/metrics")
def plan_metrics(plan_id: int, request: Request, db: Session = Depends(get_db)):
    """Per-day completion counts, follower roster with progress, drop-off curve,
    stale follower list (no activity in 7 days)."""
    _require_team(request, db)
    plan = (
        db.query(ReadingPlan)
        .options(joinedload(ReadingPlan.followers).joinedload(PlanFollower.member),
                 joinedload(ReadingPlan.followers).joinedload(PlanFollower.completions))
        .filter(ReadingPlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    duration = plan.duration_days or 1
    follower_count = len(plan.followers)
    per_day_counts = {n: 0 for n in range(1, duration + 1)}
    streaks: List[int] = []
    rows = []
    stale: List[dict] = []
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)

    for f in plan.followers:
        completed_days = sorted({c.day_number for c in f.completions})
        for d in completed_days:
            if d in per_day_counts:
                per_day_counts[d] += 1
        progress = _follower_progress(f, plan)
        streak = _streak_for(f)
        streaks.append(streak)
        row = {
            "follower_id": f.id,
            "member_id": f.member_id,
            "member_name": f.member.full_name if f.member else "—",
            "phone": f.member.phone if f.member else "",
            "started_on": f.started_on.isoformat() if f.started_on else None,
            "current_day": f.current_day,
            "completed_count": progress["completed_count"],
            "completion_pct": progress["completion_pct"],
            "completed_at": progress["completed_at"],
            "last_active_at": progress["last_active_at"],
            "streak": streak,
        }
        rows.append(row)
        last_active = f.last_active_at or f.created_at
        if not f.completed_at and last_active and last_active < seven_days_ago:
            stale.append(row)

    rows.sort(key=lambda r: (r["completion_pct"], r["completed_count"]), reverse=True)
    stale.sort(key=lambda r: r["last_active_at"] or "")

    # Drop-off curve: % of followers reaching day N
    drop_off = []
    for n in range(1, duration + 1):
        pct = int(round((per_day_counts[n] / follower_count) * 100)) if follower_count else 0
        drop_off.append({"day": n, "completed": per_day_counts[n], "pct": pct})

    # Streak histogram
    buckets = {"0": 0, "1-2": 0, "3-6": 0, "7-13": 0, "14+": 0}
    for s in streaks:
        if s == 0: buckets["0"] += 1
        elif s <= 2: buckets["1-2"] += 1
        elif s <= 6: buckets["3-6"] += 1
        elif s <= 13: buckets["7-13"] += 1
        else: buckets["14+"] += 1

    avg_completion = (
        sum(r["completion_pct"] for r in rows) / len(rows) if rows else 0
    )

    return {
        "plan": _serialize_plan(plan),
        "follower_count": follower_count,
        "average_completion_pct": int(round(avg_completion)),
        "completed_count": sum(1 for r in rows if r["completed_at"]),
        "drop_off": drop_off,
        "streak_buckets": buckets,
        "followers": rows,
        "stale_followers": stale,
    }
