"""
Prayer Groups — admin tool to shuffle members into balanced prayer groups.

The pool comes from rfm-database (all active assembly members). Each member
gets a "commitment score" from the admin-selected criteria:

  - attendance   — % of recent Sundays attended      (rfm-database)
  - titles       — pastor/elder/deacon weighting      (portal leadership_roles)
  - departments  — serves in ≥1 department/ministry  (rfm-database)

Members are then snake-drafted across N groups so every group gets a balanced
spread of committed / less-committed people. Admin can regenerate (re-shuffle),
rename groups, set leaders (auto = elders/pastors, or manual), download an
Excel workbook (one sheet per group), and publish — after which members see
their group on the portal dashboard.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Member,
    PrayerGroupSet, PrayerGroup, PrayerGroupMember,
)
import rfm_api_client as _rfm

router = APIRouter()

VALID_CRITERIA = {"attendance", "titles", "departments"}
TITLE_SCORES = {"pastor": 1.0, "elder": 1.0, "deacon": 0.6}
LEADER_ROLES = {"pastor", "elder"}


# ── Auth ────────────────────────────────────────────────────────────────────

def _require_admin(request: Request):
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin access required")


def _assembly_id(request: Request, db: Session) -> str:
    assembly = getattr(request.state, "assembly", {}) or {}
    aid = assembly.get("id")
    if not aid:
        # Fall back to the deployment default
        from routers.api import _resolve_default_assembly_id
        aid = _resolve_default_assembly_id(db)
    if not aid:
        raise HTTPException(status_code=422, detail="No assembly context found.")
    return str(aid)


# ── Signal gathering ─────────────────────────────────────────────────────────

def _fetch_pool(assembly_id: str, db: Session) -> list[dict]:
    """All active assembly members from rfm-database.

    Pages by page-fill, not by meta: the portal's rfm_api_client unwraps the
    {data, meta} envelope and returns only the data list, so the pagination
    metadata isn't available here. Instead we keep fetching while a page comes
    back full (== PAGE_SIZE) and stop on the first short/empty page.
    """
    PAGE_SIZE = 100
    pool: list[dict] = []
    page = 1
    while page <= 200:  # hard safety cap (20k members)
        r = _rfm.search_members(assembly_id=assembly_id, page=page, size=PAGE_SIZE, db=db)
        if not r.ok or not r.data:
            break
        items = r.data if isinstance(r.data, list) else (r.data.get("data") or [])
        if not items:
            break
        for m in items:
            status = (m.get("membership_status") or "ACTIVE").upper()
            if status != "ACTIVE":
                continue
            pool.append({
                "external_member_id": m.get("id"),
                "full_name": _rfm.fullname_from_member(m),
                "phone": m.get("phone") or "",
                # Department signal straight from rfm-database (members carry
                # their departments + ministries) — no local lookup needed.
                "has_dept": bool(m.get("departments") or m.get("ministries")),
            })
        if len(items) < PAGE_SIZE:
            break  # last page
        page += 1
    return pool


def _attendance_rates(assembly_id: str, db: Session) -> dict[str, float]:
    """external_member_id → attendance rate (0..1) over the last 3 months."""
    r = _rfm.assembly_attendance_by_member(assembly_id, months=3, service_type="Sunday", db=db)
    rates: dict[str, float] = {}
    if r.ok and isinstance(r.data, dict):
        total = r.data.get("total_services") or 0
        if total > 0:
            for row in r.data.get("members", []):
                mid = row.get("member_id")
                if mid:
                    rates[str(mid)] = min(1.0, (row.get("attended") or 0) / total)
    return rates


def _local_roles(external_ids: list[str], db: Session) -> dict[str, list]:
    """external_member_id → leadership_roles. Titles (elder/deacon/pastor)
    live only in the portal, so this is the one signal we still read locally;
    rfm-database has no per-member office field."""
    out: dict[str, list] = {}
    if not external_ids:
        return out
    rows = (
        db.query(Member)
        .filter(Member.external_member_id.in_(external_ids))
        .all()
    )
    for m in rows:
        roles = m.leadership_roles or []
        if isinstance(roles, str):
            try:
                roles = json.loads(roles)
            except (ValueError, TypeError):
                roles = []
        out[str(m.external_member_id)] = [str(x).lower() for x in (roles or [])]
    return out


def _score(member: dict, criteria: list[str], att: dict, roles_map: dict) -> tuple[float, list[str]]:
    """Return (commitment_score 0..1, roles) for a member under the chosen criteria.

    Sources: attendance + departments from rfm-database (on the member dict /
    attendance map), titles from the portal's leadership_roles."""
    ext = str(member["external_member_id"])
    roles = roles_map.get(ext, [])
    parts: list[float] = []
    if "attendance" in criteria:
        parts.append(att.get(ext, 0.0))
    if "titles" in criteria:
        parts.append(max((TITLE_SCORES.get(r, 0.0) for r in roles), default=0.0))
    if "departments" in criteria:
        parts.append(1.0 if member.get("has_dept") else 0.0)
    score = sum(parts) / len(parts) if parts else 0.0
    return score, roles


def _generate(set_obj: PrayerGroupSet, db: Session, assembly_id: str):
    """(Re)build the groups + members for a set. Wipes existing groups first."""
    criteria = [c for c in (json.loads(set_obj.criteria or "[]")) if c in VALID_CRITERIA]
    n = max(1, int(set_obj.num_groups or 1))

    pool = _fetch_pool(assembly_id, db)
    if not pool:
        raise HTTPException(status_code=400, detail="No active members found in the central database.")

    att = _attendance_rates(assembly_id, db)
    roles_map = _local_roles([str(m["external_member_id"]) for m in pool], db)

    # Score every member
    scored = []
    for m in pool:
        s, roles = _score(m, criteria, att, roles_map)
        scored.append({**m, "score": s, "roles": roles})

    # Jitter within score tiers so each regenerate differs.
    for m in scored:
        m["_sortkey"] = round(m["score"], 2) + random.uniform(-0.04, 0.04)

    buckets: list[list[dict]] = [[] for _ in range(n)]
    leader_ids_by_group: dict[int, set] = {gi: set() for gi in range(n)}

    def _snake(members, start_sizes_balance=True):
        """Snake-draft members across buckets, dealing into the smallest
        group first each pass so total sizes stay balanced even after
        leaders were pre-seeded."""
        members = sorted(members, key=lambda m: m["_sortkey"], reverse=True)
        for m in members:
            gi = min(range(n), key=lambda i: (len(buckets[i]), i))
            buckets[gi].append(m)

    if set_obj.leader_mode == "auto":
        # EVERY elder/pastor becomes a leader, distributed evenly (round-robin)
        # across groups so all of them are used and groups are balanced.
        elders = [m for m in scored if set(m["roles"]) & LEADER_ROLES]
        rest   = [m for m in scored if not (set(m["roles"]) & LEADER_ROLES)]
        random.shuffle(elders)
        for i, m in enumerate(elders):
            gi = i % n
            buckets[gi].append(m)
            leader_ids_by_group[gi].add(str(m["external_member_id"]))
        # Fill the remaining members, balancing total group sizes.
        _snake(rest)
    else:
        # No auto leaders — balanced snake-draft of everyone.
        _snake(scored)

    # Persist — wipe old groups, write new
    for g in list(set_obj.groups):
        db.delete(g)
    db.flush()

    for gi in range(n):
        leaders = leader_ids_by_group.get(gi, set())
        grp = PrayerGroup(
            set_id=set_obj.id,
            name=f"Group {gi + 1}",
            leader_external_member_id=next(iter(leaders), None),  # legacy/first
            sort_order=gi,
        )
        db.add(grp)
        db.flush()
        for m in buckets[gi]:
            db.add(PrayerGroupMember(
                group_id=grp.id,
                external_member_id=str(m["external_member_id"]),
                full_name=m["full_name"],
                phone=m["phone"],
                is_leader=(str(m["external_member_id"]) in leaders),
                score=int(round(m["score"] * 100)),
            ))
    set_obj.updated_at = datetime.now(timezone.utc)
    db.commit()


# ── Serialization ────────────────────────────────────────────────────────────

def _set_dict(s: PrayerGroupSet) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "num_groups": s.num_groups,
        "leaders_per_group": getattr(s, "leaders_per_group", 1),
        "criteria": json.loads(s.criteria or "[]"),
        "leader_mode": s.leader_mode,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "published_at": s.published_at.isoformat() if s.published_at else None,
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "leader_external_member_id": g.leader_external_member_id,
                "members": [
                    {
                        "external_member_id": m.external_member_id,
                        "full_name": m.full_name,
                        "phone": m.phone,
                        "is_leader": m.is_leader,
                        "score": m.score,
                    }
                    for m in sorted(g.members, key=lambda x: (not x.is_leader, -(x.score or 0), (x.full_name or "").lower()))
                ],
            }
            for g in sorted(s.groups, key=lambda x: x.sort_order)
        ],
    }


# ── Admin endpoints ──────────────────────────────────────────────────────────

@router.get("/admin/prayer-groups")
def list_sets(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    sets = db.query(PrayerGroupSet).order_by(PrayerGroupSet.created_at.desc()).all()
    # Resolve creator names in one query
    creator_ids = {s.created_by_member_id for s in sets if s.created_by_member_id}
    names: dict = {}
    if creator_ids:
        for m in db.query(Member).filter(Member.id.in_(creator_ids)).all():
            names[m.id] = m.full_name
    return [{
        "id": s.id, "name": s.name, "status": s.status,
        "num_groups": s.num_groups, "criteria": json.loads(s.criteria or "[]"),
        "leader_mode": s.leader_mode,
        "created_by": names.get(s.created_by_member_id),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "published_at": s.published_at.isoformat() if s.published_at else None,
    } for s in sets]


@router.get("/admin/prayer-groups/{set_id}")
def get_set(set_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    return _set_dict(s)


@router.post("/admin/prayer-groups/generate")
def generate_set(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_admin(request)
    assembly_id = _assembly_id(request, db)

    name = (data.get("name") or "").strip() or f"Prayer Groups {datetime.now(timezone.utc):%b %Y}"
    num_groups = max(1, min(100, int(data.get("num_groups") or 2)))
    leaders_per_group = max(0, min(20, int(data.get("leaders_per_group") or 1)))
    criteria = [c for c in (data.get("criteria") or []) if c in VALID_CRITERIA]
    leader_mode = data.get("leader_mode") if data.get("leader_mode") in ("none", "auto", "manual") else "none"

    # Stamp the creating admin (their local member id, from the signed
    # admin-identity cookie) so the saved-sets list can show who made it.
    created_by_id = None
    try:
        from routers.pages import get_admin_identity
        ident = get_admin_identity(request) or {}
        created_by_id = ident.get("member_id")
    except Exception:
        pass

    s = PrayerGroupSet(
        name=name, num_groups=num_groups, leaders_per_group=leaders_per_group,
        criteria=json.dumps(criteria), leader_mode=leader_mode, status="draft",
        created_by_member_id=created_by_id,
    )
    db.add(s)
    db.flush()
    _generate(s, db, assembly_id)
    db.refresh(s)
    return _set_dict(s)


@router.post("/admin/prayer-groups/{set_id}/regenerate")
def regenerate_set(set_id: int, request: Request, data: dict = Body(default={}), db: Session = Depends(get_db)):
    _require_admin(request)
    assembly_id = _assembly_id(request, db)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    if s.status == "published":
        raise HTTPException(status_code=400, detail="Unpublish before regenerating.")
    # Allow updating params on regenerate
    if "num_groups" in data:
        s.num_groups = max(1, min(100, int(data["num_groups"] or 2)))
    if "leaders_per_group" in data:
        s.leaders_per_group = max(0, min(20, int(data["leaders_per_group"] or 1)))
    if "criteria" in data:
        s.criteria = json.dumps([c for c in (data["criteria"] or []) if c in VALID_CRITERIA])
    if data.get("leader_mode") in ("none", "auto", "manual"):
        s.leader_mode = data["leader_mode"]
    db.flush()
    _generate(s, db, assembly_id)
    db.refresh(s)
    return _set_dict(s)


@router.put("/admin/prayer-groups/{set_id}")
def update_set(set_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    """Rename the set / groups, set leaders, and move members between groups."""
    _require_admin(request)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")

    if "name" in data:
        s.name = (data["name"] or "").strip() or s.name

    # Group renames + leader assignment: [{id, name, leader_external_member_id}]
    for g_in in (data.get("groups") or []):
        g = db.query(PrayerGroup).filter(
            PrayerGroup.id == g_in.get("id"), PrayerGroup.set_id == s.id).first()
        if not g:
            continue
        if "name" in g_in:
            g.name = (g_in["name"] or "").strip() or g.name
        if "leader_external_member_id" in g_in:
            g.leader_external_member_id = g_in["leader_external_member_id"] or None
            for m in g.members:
                m.is_leader = (m.external_member_id == g.leader_external_member_id)

    # Promote / demote a member: {set_leader: {external_member_id, is_leader}}
    sl = data.get("set_leader")
    if sl and sl.get("external_member_id") is not None:
        pm = (
            db.query(PrayerGroupMember)
            .join(PrayerGroup, PrayerGroupMember.group_id == PrayerGroup.id)
            .filter(PrayerGroup.set_id == s.id,
                    PrayerGroupMember.external_member_id == str(sl["external_member_id"]))
            .first()
        )
        if pm:
            pm.is_leader = bool(sl.get("is_leader"))

    # Move a member: {external_member_id, to_group_id}
    mv = data.get("move")
    if mv and mv.get("external_member_id") and mv.get("to_group_id"):
        pm = (
            db.query(PrayerGroupMember)
            .join(PrayerGroup, PrayerGroupMember.group_id == PrayerGroup.id)
            .filter(PrayerGroup.set_id == s.id,
                    PrayerGroupMember.external_member_id == str(mv["external_member_id"]))
            .first()
        )
        target = db.query(PrayerGroup).filter(
            PrayerGroup.id == mv["to_group_id"], PrayerGroup.set_id == s.id).first()
        if pm and target:
            pm.group_id = target.id
            pm.is_leader = (target.leader_external_member_id == pm.external_member_id)

    db.commit()
    db.refresh(s)
    return _set_dict(s)


@router.post("/admin/prayer-groups/{set_id}/publish")
def publish_set(set_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    # Only one published set at a time
    db.query(PrayerGroupSet).filter(
        PrayerGroupSet.status == "published", PrayerGroupSet.id != s.id
    ).update({"status": "draft"})
    s.status = "published"
    s.published_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "published", "id": s.id}


@router.post("/admin/prayer-groups/{set_id}/unpublish")
def unpublish_set(set_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    s.status = "draft"
    s.published_at = None
    db.commit()
    return {"status": "draft", "id": s.id}


@router.delete("/admin/prayer-groups/{set_id}")
def delete_set(set_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    db.delete(s)
    db.commit()
    return {"deleted": set_id}


# ── Member-facing ────────────────────────────────────────────────────────────

@router.get("/portal/prayer-group")
def my_prayer_group(request: Request, db: Session = Depends(get_db)):
    """The logged-in member's prayer group from the currently published set."""
    from routers.api import _require_logged_in_member
    member = _require_logged_in_member(request, db)
    ext = getattr(member, "external_member_id", None)
    if not ext:
        return {"published": False, "group": None}

    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.status == "published").first()
    if not s:
        return {"published": False, "group": None}

    pm = (
        db.query(PrayerGroupMember)
        .join(PrayerGroup, PrayerGroupMember.group_id == PrayerGroup.id)
        .filter(PrayerGroup.set_id == s.id,
                PrayerGroupMember.external_member_id == str(ext))
        .first()
    )
    if not pm:
        return {"published": True, "group": None}

    g = db.query(PrayerGroup).filter(PrayerGroup.id == pm.group_id).first()
    return {
        "published": True,
        "set_name": s.name,
        "group": {
            "name": g.name,
            "members": [
                {"full_name": m.full_name, "is_leader": m.is_leader, "phone": m.phone}
                # Members see leaders first, then alphabetical — no weight
                # ordering (we don't expose a commitment ranking to members).
                for m in sorted(g.members, key=lambda x: (not x.is_leader, (x.full_name or "").lower()))
            ],
        },
    }
