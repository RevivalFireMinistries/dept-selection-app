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
    Member, Settings,
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


def _require_password(db: Session, password: str | None):
    """Re-confirm the admin password before a disruptive action on a PUBLISHED
    (running) set. Guards accidental deletes / member reshuffles."""
    setting = db.query(Settings).filter(Settings.key == "adminPassword").first()
    correct = setting.value if setting else "admin123"
    if (password or "") != correct:
        raise HTTPException(status_code=403, detail="Incorrect admin password.")


def _assembly_id(request: Request, db: Session) -> str:
    """The assembly to scope members to — the LOGGED-IN ADMIN's own assembly.

    Resolved from the admin's member record (external_assembly_id) so prayer
    groups only ever contain members of that admin's branch. Falls back to
    request.state / the deployment default only when the admin has no central
    assembly link."""
    from routers.pages import get_admin_identity
    ident = get_admin_identity(request) or {}
    mid = ident.get("member_id")
    if mid:
        m = db.query(Member).filter(Member.id == mid).first()
        if m and m.external_assembly_id:
            return str(m.external_assembly_id)

    assembly = getattr(request.state, "assembly", {}) or {}
    aid = assembly.get("id")
    if not aid:
        from routers.api import _resolve_default_assembly_id
        aid = _resolve_default_assembly_id(db)
    if not aid:
        raise HTTPException(status_code=422, detail="No assembly link found for your admin account. Ask for Member Sync to be run.")
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
                # For the keep-apart rules (same surname / same family).
                "surname": (m.get("last_name") or "").strip().lower(),
                "family_id": str(m.get("family_id")) if m.get("family_id") else None,
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
    surnames_by_group: list[set] = [set() for _ in range(n)]
    families_by_group: list[set] = [set() for _ in range(n)]

    sep_sur = bool(getattr(set_obj, "separate_surnames", True))
    sep_fam = bool(getattr(set_obj, "separate_family", True))

    def _conflicts(gi: int, m: dict) -> bool:
        if sep_sur and m["surname"] and m["surname"] in surnames_by_group[gi]:
            return True
        if sep_fam and m["family_id"] and m["family_id"] in families_by_group[gi]:
            return True
        return False

    def _place(m: dict) -> int:
        """Pick the smallest group with no surname/family clash; if every
        group clashes (unavoidable — more same-surname/family people than
        groups), fall back to the smallest group (best effort)."""
        candidates = [i for i in range(n) if not _conflicts(i, m)]
        choices = candidates or list(range(n))
        gi = min(choices, key=lambda i: (len(buckets[i]), i))
        buckets[gi].append(m)
        if m["surname"]:   surnames_by_group[gi].add(m["surname"])
        if m["family_id"]: families_by_group[gi].add(m["family_id"])
        return gi

    def _snake(members):
        for m in sorted(members, key=lambda x: x["_sortkey"], reverse=True):
            _place(m)

    if set_obj.leader_mode == "auto":
        # EVERY elder/pastor becomes a leader, spread across groups (respecting
        # the keep-apart rules) so all are used and groups stay balanced.
        elders = [m for m in scored if set(m["roles"]) & LEADER_ROLES]
        rest   = [m for m in scored if not (set(m["roles"]) & LEADER_ROLES)]
        random.shuffle(elders)
        for m in elders:
            gi = _place(m)
            leader_ids_by_group[gi].add(str(m["external_member_id"]))
        _snake(rest)
    else:
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
                surname=m.get("surname"),
                family_id=m.get("family_id"),
            ))
    set_obj.updated_at = datetime.now(timezone.utc)
    db.commit()


# ── Chain prayer scheduling ──────────────────────────────────────────────────

def _expire_chain_if_past(s: PrayerGroupSet, db: Session) -> bool:
    """Clear the chain-prayer schedule once its event date has passed, so the
    admin form goes blank and they can schedule a fresh one. Returns True if it
    was cleared. A schedule with no date never auto-expires."""
    d = getattr(s, "chain_date", None)
    if not d:
        return False
    try:
        event = datetime.strptime(str(d), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    if event >= datetime.now(timezone.utc).date():
        return False  # today or future — keep it
    s.chain_enabled = False
    s.chain_label = None
    s.chain_date = None
    s.chain_start = None
    s.chain_end = None
    s.chain_slot_minutes = None
    s.chain_slots = None
    db.commit()
    return True


def _chain_slots_list(s: PrayerGroupSet) -> list:
    """The stored per-slot group-id assignment (empty if unset/invalid)."""
    try:
        v = json.loads(s.chain_slots) if getattr(s, "chain_slots", None) else []
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _chain_duration_label(s: PrayerGroupSet) -> str:
    """Total window length as a friendly label: '2-hour', '90-minute', '2h 30m'."""
    def to_min(t):
        try:
            hh, mm = str(t).split(":")
            return int(hh) * 60 + int(mm)
        except Exception:
            return None
    start, end = to_min(s.chain_start), to_min(s.chain_end)
    if start is None or end is None:
        return ""
    if end <= start:
        end += 24 * 60
    mins = end - start
    if mins <= 0:
        return ""
    if mins % 60 == 0:
        return f"{mins // 60}-hour"
    if mins < 60:
        return f"{mins}-minute"
    return f"{mins // 60}h {mins % 60}m"


def _long_date(iso) -> str:
    """ISO date -> '27 June 2026'."""
    try:
        d = datetime.strptime(str(iso), "%Y-%m-%d")
        return f"{d.day} {d.strftime('%B')} {d.year}"
    except (ValueError, TypeError):
        return str(iso or "")


def _chain_slot_times(s: PrayerGroupSet) -> list:
    """The ordered (start, end) time slots for the chain-prayer window."""
    def to_min(t):
        try:
            hh, mm = str(t).split(":")
            return int(hh) * 60 + int(mm)
        except Exception:
            return None

    def to_str(m):
        return f"{(m // 60) % 24:02d}:{m % 60:02d}"

    if not (getattr(s, "chain_enabled", False) and s.chain_start and s.chain_end and s.chain_slot_minutes):
        return []
    start, end = to_min(s.chain_start), to_min(s.chain_end)
    if start is None or end is None:
        return []
    if end <= start:
        end += 24 * 60  # window crosses midnight
    slot = max(1, int(s.chain_slot_minutes))

    out, cur, i = [], start, 0
    while cur + slot <= end and i < 1000:  # cap defensively
        out.append((to_str(cur), to_str(cur + slot)))
        cur += slot
        i += 1
    return out


def _despace(s: str) -> str:
    """Undo 'letter-spacing' in pasted prayer points (e.g. text copied from a
    graphic where every character is separated by a space: "D I V I N E").
    Only kicks in when the text has double-space word gaps — the letter-spacing
    signature — so normal single-spaced text is never touched."""
    import re
    if not s or "  " not in s:
        return s
    SENT = "\x00"
    t = re.sub(r" {2,}", SENT, s)  # protect word gaps

    def fix_seg(seg: str) -> str:
        out, run = [], []
        for tk in seg.split(" "):
            if len(tk) == 1:
                run.append(tk)
            else:
                if len(run) >= 2:
                    out.append("".join(run))
                elif run:
                    out.extend(run)
                run = []
                out.append(tk)
        if len(run) >= 2:
            out.append("".join(run))
        elif run:
            out.extend(run)
        return " ".join(out)

    joined = " ".join(fix_seg(p) for p in t.split(SENT))
    return re.sub(r" {2,}", " ", joined).strip()


# Smart punctuation / other odd chars → plain ASCII the PDF font can render.
_CLEAN_TRANSLATE = {
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'",          # single quotes
    0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"',          # double quotes
    0x2013: "-", 0x2014: "-", 0x2015: "-", 0x2212: "-",          # dashes / minus
    0x2026: "...",                                                # ellipsis
    0x00A0: " ", 0x2022: "-",                                     # nbsp, bullet
}


def _clean_text(s: str) -> str:
    """Clean pasted text (users copy from graphics / Word / PDFs). Folds
    fancy-font unicode, strips zero-width + control chars, normalises smart
    quotes/dashes, undoes letter-spacing, and collapses whitespace."""
    import re, unicodedata
    if not s:
        return s
    s = unicodedata.normalize("NFKC", s)  # fold fancy-font / compatibility unicode
    # Classify every char by its Unicode category so we catch ALL exotic
    # whitespace/format chars (NBSP, U+2028 LINE SEPARATOR, U+2029, zero-width,
    # BOM, soft hyphen, control chars) without hardcoding a list.
    out = []
    for ch in s:
        if ch == chr(10):          # keep real newlines
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat[0] == "Z" or ch == chr(9):   # separators (incl. U+2028/2029) + tab -> space
            out.append(" ")
        elif cat[0] == "C":                 # control & format (zero-width, BOM, soft hyphen)
            continue
        else:
            out.append(ch)
    s = "".join(out).translate(_CLEAN_TRANSLATE)
    s = _despace(s)                # undo letter-spacing
    s = re.sub("  +", " ", s)      # collapse runs of 2+ spaces
    return s.strip()


def _chain_prayer_points_list(s: PrayerGroupSet) -> list:
    """Stored prayer points (one per round), cleaned; empty if unset/invalid."""
    try:
        v = json.loads(s.chain_prayer_points) if getattr(s, "chain_prayer_points", None) else []
        return [_clean_text(str(x or "")) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _chain_rounds_count(s: PrayerGroupSet) -> int:
    """How many times each group prays = ceil(slots / groups). Each round is one
    full pass through all groups and shares a single prayer point."""
    n = len(_chain_slot_times(s))
    g = len([_ for _ in s.groups])
    if n == 0 or g == 0:
        return 0
    return -(-n // g)  # ceil


def _chain_schedule(s: PrayerGroupSet) -> list:
    """Assign groups to the chain-prayer time slots. Uses the admin's explicit
    (randomised / hand-edited) assignment in `chain_slots` when it's valid for
    the current slot count + groups; otherwise falls back to round-robin order.
    Each slot also carries its round index + the round's shared prayer point.
    Returns ordered
    [{start, end, group_id, group_name, sort_order, round, prayer_point}]."""
    times = _chain_slot_times(s)
    if not times:
        return []
    groups = sorted(s.groups, key=lambda g: g.sort_order)
    if not groups:
        return []
    gmap = {g.id: g for g in groups}
    points = _chain_prayer_points_list(s)

    slot_ids = []
    try:
        slot_ids = json.loads(s.chain_slots) if s.chain_slots else []
    except (ValueError, TypeError):
        slot_ids = []
    use_explicit = (
        isinstance(slot_ids, list)
        and len(slot_ids) == len(times)
        and all(gid in gmap for gid in slot_ids)
    )

    out = []
    for i, (st, en) in enumerate(times):
        g = gmap[slot_ids[i]] if use_explicit else groups[i % len(groups)]
        rnd = i // len(groups)  # one full pass through the groups = one round
        out.append({
            "start": st, "end": en,
            "group_id": g.id, "group_name": g.name, "sort_order": g.sort_order,
            "round": rnd,
            "prayer_point": points[rnd] if rnd < len(points) else "",
        })
    return out


# ── Serialization ────────────────────────────────────────────────────────────

def _set_dict(s: PrayerGroupSet) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "num_groups": s.num_groups,
        "leaders_per_group": getattr(s, "leaders_per_group", 1),
        "criteria": json.loads(s.criteria or "[]"),
        "leader_mode": s.leader_mode,
        "separate_surnames": bool(getattr(s, "separate_surnames", True)),
        "separate_family": bool(getattr(s, "separate_family", True)),
        "status": s.status,
        "chain": {
            "enabled": bool(getattr(s, "chain_enabled", False)),
            "label": s.chain_label,
            "date": getattr(s, "chain_date", None),
            "start": s.chain_start,
            "end": s.chain_end,
            "slot_minutes": s.chain_slot_minutes,
            "slots": _chain_slots_list(s),
            "prayer_points": _chain_prayer_points_list(s),
            "rounds": _chain_rounds_count(s),
            "schedule": _chain_schedule(s),
        },
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


# ── Couple co-location ───────────────────────────────────────────────────────
#
# After a set is running, couples sometimes ask to be in the same group as their
# spouse. This ONLY moves couple members (shared family_id) — nobody else is
# touched. Each split couple is consolidated into one of the groups a spouse is
# already in, so only the other spouse actually moves. To keep groups as even as
# possible we send each couple into the smaller of their current groups; because
# we won't disturb singles, group sizes may shift a little as couples merge.

def _plan_pair_couples(s: PrayerGroupSet) -> dict:
    """Work out the moves to reunite couples, touching couple members only.
    Pure — reads the set and returns a plan; never mutates the database."""
    from collections import Counter, defaultdict

    groups = sorted(s.groups, key=lambda g: g.sort_order)
    gname = {g.id: g.name for g in groups}

    M: dict = {}
    for g in groups:
        for m in g.members:
            M[m.external_member_id] = {
                "ext": m.external_member_id,
                "name": m.full_name or "",
                "gid": g.id,
                "is_leader": bool(m.is_leader),
                "family_id": str(m.family_id) if m.family_id else None,
                "surname": (m.surname or ""),
                "score": m.score or 0,
            }
    members = list(M.values())
    orig_gid = {e: d["gid"] for e, d in M.items()}
    orig_size = {g.id: sum(1 for d in members if d["gid"] == g.id) for g in groups}
    cur_size = dict(orig_size)  # running sizes as couples merge

    fam_counts = Counter(d["family_id"] for d in members if d["family_id"])
    def is_couple(d): return bool(d["family_id"]) and fam_counts[d["family_id"]] >= 2

    fam_members = defaultdict(list)
    for d in members:
        if is_couple(d):
            fam_members[d["family_id"]].append(d)

    warnings = []
    split = 0
    # Bigger splits first so the tighter constraints are placed while there's
    # still room to balance.
    for fid, fam in sorted(fam_members.items(),
                           key=lambda kv: (-len({m["gid"] for m in kv[1]}), kv[0])):
        present = {d["gid"] for d in fam}
        if len(present) == 1:
            continue  # already together
        split += 1
        leader_gids = {d["gid"] for d in fam if d["is_leader"]}
        if len(leader_gids) >= 2:
            label = (fam[0]["surname"] or (fam[0]["name"].split()[-1] if fam[0]["name"] else "")) or "This family"
            warnings.append(f"“{label}” has leaders in different groups — please place this family manually.")
            continue
        if leader_gids:
            target = next(iter(leader_gids))          # never move a leader
        else:
            # Consolidate where most of the family already is (fewest moves);
            # break ties toward the smaller group to keep sizes even.
            cnt = Counter(d["gid"] for d in fam)
            target = sorted(present, key=lambda gid: (-cnt[gid], cur_size[gid], gid))[0]

        for mover in [d for d in fam if d["gid"] != target]:
            cur_size[mover["gid"]] -= 1
            cur_size[target] += 1
            mover["gid"] = target

    moves = []
    for e, d in M.items():
        if d["gid"] != orig_gid[e]:
            # By construction only couple members ever change group.
            moves.append({
                "ext": e, "name": d["name"],
                "from_group": gname[orig_gid[e]],
                "to_group": gname[d["gid"]],
                "reason": "couple",
            })

    reunited = []
    for fid, fam in sorted(fam_members.items()):
        if len({orig_gid[d["ext"]] for d in fam}) > 1 and len({d["gid"] for d in fam}) == 1:
            reunited.append({"group": gname[fam[0]["gid"]], "members": [d["name"] for d in fam]})

    sizes_before = {gname[g.id]: orig_size[g.id] for g in groups}
    sizes_after = {gname[g.id]: cur_size[g.id] for g in groups}
    return {
        "detected_couples": len(fam_members),
        "split_couples": split,
        "moves": moves,
        "reunited": reunited,
        "warnings": warnings,
        "sizes_before": sizes_before,
        "sizes_after": sizes_after,
        "sizes_changed": sizes_before != sizes_after,
        "final_gid": {e: d["gid"] for e, d in M.items()},
        "has_family_data": bool(fam_counts),
    }


@router.get("/admin/prayer-groups/{set_id}/couples-preview")
def preview_pair_couples(set_id: int, request: Request, db: Session = Depends(get_db)):
    """Dry-run: show which couples would be reunited and the moves involved."""
    _require_admin(request)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    plan = _plan_pair_couples(s)
    plan.pop("final_gid", None)
    return plan


@router.post("/admin/prayer-groups/{set_id}/pair-couples")
def pair_couples(set_id: int, request: Request, db: Session = Depends(get_db)):
    """Apply the couple co-location plan to a running set, in place."""
    _require_admin(request)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")

    plan = _plan_pair_couples(s)
    final = plan["final_gid"]
    groups_by_id = {g.id: g for g in s.groups}
    moved = 0
    for g in list(s.groups):
        for m in list(g.members):
            dest = final.get(m.external_member_id)
            if dest and dest != m.group_id:
                m.group_id = dest
                tg = groups_by_id.get(dest)
                m.is_leader = bool(tg and tg.leader_external_member_id == m.external_member_id)
                moved += 1

    if moved:
        s.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(s)

    return {
        "success": True,
        "moved": moved,
        "reunited": plan["reunited"],
        "warnings": plan["warnings"],
        "sizes_after": plan["sizes_after"],
        "set": _set_dict(s),
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


def _reconcile_set(set_obj: PrayerGroupSet, db: Session, valid_ids: set) -> int:
    """Remove group members who are no longer in the central roster (deleted
    in rfm-database, or moved to another assembly). `valid_ids` is the set of
    external_member_ids currently in the assembly. Returns the count removed."""
    removed = 0
    for g in set_obj.groups:
        for m in list(g.members):
            if str(m.external_member_id) not in valid_ids:
                db.delete(m)
                removed += 1
    if removed:
        set_obj.updated_at = datetime.now(timezone.utc)
        db.commit()
    return removed


@router.get("/admin/prayer-groups/{set_id}")
def get_set(set_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    assembly_id = _assembly_id(request, db)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    # Drop anyone deleted from the central roster before returning.
    valid_ids = {str(m["external_member_id"]) for m in _fetch_pool(assembly_id, db)}
    if valid_ids:  # guard against an empty/failed fetch wiping everyone
        _reconcile_set(s, db, valid_ids)
        db.refresh(s)
    _expire_chain_if_past(s, db)
    return _set_dict(s)


def _set_member_ids(set_obj: PrayerGroupSet) -> set:
    ids = set()
    for g in set_obj.groups:
        for m in g.members:
            ids.add(str(m.external_member_id))
    return ids


def _unallocated(set_obj: PrayerGroupSet, db: Session, assembly_id: str) -> list[dict]:
    """Scored members who are in rfm-database but not yet in any group of
    this set — i.e. people added after the set was generated. Also reconciles
    the set first (drops members deleted from the central roster)."""
    pool = _fetch_pool(assembly_id, db)
    valid_ids = {str(m["external_member_id"]) for m in pool}
    if valid_ids:
        _reconcile_set(set_obj, db, valid_ids)
    existing = _set_member_ids(set_obj)
    new_pool = [m for m in pool if str(m["external_member_id"]) not in existing]
    if not new_pool:
        return []
    att = _attendance_rates(assembly_id, db)
    roles_map = _local_roles([str(m["external_member_id"]) for m in new_pool], db)
    criteria = [c for c in json.loads(set_obj.criteria or "[]") if c in VALID_CRITERIA]
    out = []
    for m in new_pool:
        s, roles = _score(m, criteria, att, roles_map)
        out.append({**m, "score": s, "roles": roles})
    out.sort(key=lambda x: (-x["score"], (x["full_name"] or "").lower()))
    return out


@router.get("/admin/prayer-groups/{set_id}/unallocated")
def list_unallocated(set_id: int, request: Request, db: Session = Depends(get_db)):
    """Members not yet placed in this set (added since it was generated)."""
    _require_admin(request)
    assembly_id = _assembly_id(request, db)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    return [{
        "external_member_id": x["external_member_id"],
        "full_name": x["full_name"],
        "phone": x["phone"],
        "score": int(round(x["score"] * 100)),
        "is_leader_eligible": bool(set(x["roles"]) & LEADER_ROLES),
    } for x in _unallocated(s, db, assembly_id)]


@router.post("/admin/prayer-groups/{set_id}/allocate")
def allocate(set_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    """Place new members into the set.

    Body:
      {"mode": "auto"}                       — distribute ALL unallocated into
                                               the smallest groups (balanced)
      {"external_member_id": x, "group_id": g} — place one member manually
    """
    _require_admin(request)
    assembly_id = _assembly_id(request, db)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")

    items = _unallocated(s, db, assembly_id)
    by_id = {str(x["external_member_id"]): x for x in items}
    groups = sorted(s.groups, key=lambda g: g.sort_order)
    if not groups:
        raise HTTPException(status_code=400, detail="This set has no groups.")

    def _add(x, grp):
        is_leader = (s.leader_mode == "auto" and bool(set(x["roles"]) & LEADER_ROLES))
        db.add(PrayerGroupMember(
            group_id=grp.id,
            external_member_id=str(x["external_member_id"]),
            full_name=x["full_name"], phone=x["phone"],
            is_leader=is_leader, score=int(round(x["score"] * 100)),
            surname=x.get("surname"), family_id=x.get("family_id"),
        ))

    if data.get("mode") == "auto":
        # Respect the keep-apart rules, seeded from existing members.
        sep_sur = bool(getattr(s, "separate_surnames", True))
        sep_fam = bool(getattr(s, "separate_family", True))
        sizes = {g.id: len(g.members) for g in groups}
        surn = {g.id: {(m.surname or "").lower() for m in g.members if m.surname} for g in groups}
        fam  = {g.id: {m.family_id for m in g.members if m.family_id} for g in groups}
        gmap = {g.id: g for g in groups}
        for x in items:
            sname = (x.get("surname") or "").lower()
            fid = x.get("family_id")
            def _clash(gid):
                if sep_sur and sname and sname in surn[gid]:
                    return True
                if sep_fam and fid and fid in fam[gid]:
                    return True
                return False
            ok = [gid for gid in sizes if not _clash(gid)]
            choices = ok or list(sizes.keys())
            gid = min(choices, key=lambda k: (sizes[k], k))
            _add(x, gmap[gid])
            sizes[gid] += 1
            if sname: surn[gid].add(sname)
            if fid:   fam[gid].add(fid)
        added = len(items)
    else:
        ext = str(data.get("external_member_id") or "")
        gid = data.get("group_id")
        x = by_id.get(ext)
        grp = next((g for g in groups if g.id == gid), None)
        if not x:
            raise HTTPException(status_code=404, detail="Member is already allocated or not found.")
        if not grp:
            raise HTTPException(status_code=404, detail="Group not found in this set.")
        _add(x, grp)
        added = 1

    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(s)
    return {"added": added, "set": _set_dict(s)}


@router.post("/admin/prayer-groups/generate")
def generate_set(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_admin(request)
    assembly_id = _assembly_id(request, db)

    name = (data.get("name") or "").strip() or f"Prayer Groups {datetime.now(timezone.utc):%b %Y}"
    num_groups = max(1, min(100, int(data.get("num_groups") or 2)))
    leaders_per_group = max(0, min(20, int(data.get("leaders_per_group") or 1)))
    criteria = [c for c in (data.get("criteria") or []) if c in VALID_CRITERIA]
    leader_mode = data.get("leader_mode") if data.get("leader_mode") in ("none", "auto", "manual") else "none"
    # Default ON — keep family / same-surname apart unless the admin opts out.
    separate_surnames = bool(data.get("separate_surnames", True))
    separate_family = bool(data.get("separate_family", True))

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
        separate_surnames=separate_surnames, separate_family=separate_family,
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
    # Reshuffling a PUBLISHED (running) set replaces everyone's group — require
    # the admin password so it can't happen by accident.
    if s.status == "published":
        _require_password(db, data.get("password"))
    # Allow updating params on regenerate
    if "num_groups" in data:
        s.num_groups = max(1, min(100, int(data["num_groups"] or 2)))
    if "leaders_per_group" in data:
        s.leaders_per_group = max(0, min(20, int(data["leaders_per_group"] or 1)))
    if "criteria" in data:
        s.criteria = json.dumps([c for c in (data["criteria"] or []) if c in VALID_CRITERIA])
    if data.get("leader_mode") in ("none", "auto", "manual"):
        s.leader_mode = data["leader_mode"]
    if "separate_surnames" in data:
        s.separate_surnames = bool(data["separate_surnames"])
    if "separate_family" in data:
        s.separate_family = bool(data["separate_family"])
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

    # Chain-prayer schedule: {enabled, label, start "HH:MM", end "HH:MM", slot_minutes}
    chain = data.get("chain")
    if isinstance(chain, dict):
        s.chain_enabled = bool(chain.get("enabled"))
        s.chain_label = _clean_text((chain.get("label") or "").strip()) or None
        s.chain_date = (chain.get("date") or "").strip() or None
        s.chain_start = (chain.get("start") or "").strip() or None
        s.chain_end = (chain.get("end") or "").strip() or None
        try:
            s.chain_slot_minutes = int(chain["slot_minutes"]) if chain.get("slot_minutes") else None
        except (ValueError, TypeError):
            s.chain_slot_minutes = None
        if "slots" in chain:
            slots = chain.get("slots")
            if isinstance(slots, list):
                # Keep only valid integers; the schedule builder validates them
                # against the current groups before use.
                clean = []
                for gid in slots:
                    try:
                        clean.append(int(gid))
                    except (ValueError, TypeError):
                        clean.append(None)
                s.chain_slots = json.dumps(clean)
            else:
                s.chain_slots = None
        if "prayer_points" in chain:
            pts = chain.get("prayer_points")
            if isinstance(pts, list):
                s.chain_prayer_points = json.dumps([_clean_text(str(p or "").strip()) for p in pts])
            else:
                s.chain_prayer_points = None

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


def _notify_leaders_of_schedule(s: PrayerGroupSet, db: Session) -> int:
    """On publish, email each group's leader(s) a summary of their own group's
    chain-prayer slots. Best-effort; returns the number of leaders notified.
    No-op when there's no chain schedule set."""
    schedule = _chain_schedule(s)
    if not schedule:
        return 0
    by_group: dict = {}
    for slot in schedule:
        by_group.setdefault(slot["group_id"], []).append(
            {"start": slot["start"], "end": slot["end"],
             "prayer_point": slot.get("prayer_point") or ""}
        )

    date_display = _long_date(s.chain_date) if getattr(s, "chain_date", None) else ""
    dur = _chain_duration_label(s)
    title = f"{dur} Chain Prayer" if dur else "Chain Prayer"
    from notifications.dispatcher import dispatch_event
    from notifications.events import EventType

    notified = 0
    for g in s.groups:
        slots = by_group.get(g.id)
        if not slots:
            continue
        leaders = [m for m in g.members if m.is_leader]
        if not leaders:
            continue
        recipients = _resolve_group_recipients(leaders, db)
        if not recipients:
            continue
        data = {
            "group_name": g.name,
            "set_name": s.name,
            "label": s.chain_label or "",
            "title": title,
            "date_display": date_display,
            "date_suffix": f" · {date_display}" if date_display else "",
            "slots": slots,
            "idem_scope": f"{s.id}:{g.id}",
        }
        try:
            dispatch_event(db, EventType.PRAYER_CHAIN_SCHEDULE, data, recipients)
            notified += len(recipients)
        except Exception as e:
            try:
                print(f"Prayer-chain publish notify failed for group {g.id}: {e}")
            except Exception:
                pass
    return notified


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

    # Auto-send each leader a summary of their group's prayer slots.
    leaders_notified = 0
    try:
        leaders_notified = _notify_leaders_of_schedule(s, db)
    except Exception as e:
        try:
            print(f"Prayer-chain publish notify error: {e}")
        except Exception:
            pass
    return {"status": "published", "id": s.id, "leaders_notified": leaders_notified}


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
def delete_set(set_id: int, request: Request, password: str = "", db: Session = Depends(get_db)):
    _require_admin(request)
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.id == set_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Set not found")
    # Deleting a PUBLISHED (running) set wipes groups members are already using —
    # require the admin password.
    if s.status == "published":
        _require_password(db, password)
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

    # Wipe a chain schedule whose date has passed so members stop seeing stale times.
    _expire_chain_if_past(s, db)

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
    you_are_leader = bool(pm.is_leader)

    # Ordinary members only see their group name + their prayer slots. The full
    # member list (with contact details) is for leaders only.
    members = []
    if you_are_leader:
        # Drop anyone deleted from the central roster before showing the list —
        # otherwise a member removed in rfm-database lingers here. (Only done on
        # leader views since that's the only place the member list is shown.)
        try:
            assembly_id = _assembly_id(request, db)
            valid_ids = {str(x["external_member_id"]) for x in _fetch_pool(assembly_id, db)}
            if valid_ids:  # guard against an empty/failed fetch wiping everyone
                _reconcile_set(s, db, valid_ids)
                db.refresh(g)
        except Exception as exc:
            print(f"[prayer-group] portal reconcile skipped: {exc}")
        members = [
            {"full_name": m.full_name, "is_leader": m.is_leader, "phone": m.phone}
            for m in sorted(g.members, key=lambda x: (not x.is_leader, (x.full_name or "").lower()))
        ]

    # The full chain flow in time order, with this member's slots flagged so the
    # UI can highlight where their group prays.
    full = _chain_schedule(s)
    flow = [
        {"start": x["start"], "end": x["end"], "group_name": x["group_name"],
         "mine": x["group_id"] == g.id}
        for x in full
    ]
    my_slots = [
        {"start": x["start"], "end": x["end"], "prayer_point": x.get("prayer_point") or ""}
        for x in full if x["group_id"] == g.id
    ]

    dur = _chain_duration_label(s)
    chain_enabled = bool(getattr(s, "chain_enabled", False))
    return {
        "published": True,
        "set_name": s.name,
        "you_are_leader": you_are_leader,
        "chain": {
            "enabled": chain_enabled,
            "label": s.chain_label,
            "date": getattr(s, "chain_date", None),
            "date_display": _long_date(s.chain_date) if getattr(s, "chain_date", None) else "",
            "start": s.chain_start,
            "end": s.chain_end,
            "title": (f"{dur} Chain Prayer" if dur else "Chain Prayer") if chain_enabled else "",
            "my_group_name": g.name,
            "my_slots": my_slots,
            "schedule": flow,
        },
        "group": {
            "name": g.name,
            "members": members,
        },
    }


def member_leads_published_group(external_member_id: str, db: Session) -> bool:
    """True if this member is a leader of a group in the currently published
    set — drives the leader-only 'My prayer group' portal menu item."""
    if not external_member_id:
        return False
    s = db.query(PrayerGroupSet).filter(PrayerGroupSet.status == "published").first()
    if not s:
        return False
    return (
        db.query(PrayerGroupMember)
        .join(PrayerGroup, PrayerGroupMember.group_id == PrayerGroup.id)
        .filter(PrayerGroup.set_id == s.id,
                PrayerGroupMember.external_member_id == str(external_member_id),
                PrayerGroupMember.is_leader.is_(True))
        .count() > 0
    )


def _fmt_event_date(iso) -> str:
    """ISO date -> 'Sat, 4 Jul' (cross-platform, no %-d)."""
    try:
        d = datetime.strptime(str(iso), "%Y-%m-%d")
        return f"{d.strftime('%a')}, {d.day} {d.strftime('%b')}"
    except (ValueError, TypeError):
        return str(iso or "")


def _resolve_group_recipients(members: list, db: Session) -> list:
    """Build dispatch recipients ({id,email,phone,name}) for prayer-group
    members. Email comes from the local Member where present, else from
    rfm-database (we rely on central for anyone not yet in the portal)."""
    ext_ids = [str(m.external_member_id) for m in members if m.external_member_id]
    local: dict = {}
    if ext_ids:
        for lm in db.query(Member).filter(Member.external_member_id.in_(ext_ids)).all():
            local[str(lm.external_member_id)] = lm

    recipients = []
    for m in members:
        ext = str(m.external_member_id)
        lm = local.get(ext)
        email = (getattr(lm, "email", None) or "").strip() if lm else ""
        if not email:  # fall back to the central roster
            try:
                r = _rfm.get_member(ext, db=db)
                if r.ok and isinstance(r.data, dict):
                    email = (r.data.get("email") or "").strip()
            except Exception:
                email = ""
        if not email:
            continue
        recipients.append({
            "id": lm.id if lm else None,
            "email": email,
            "phone": (getattr(lm, "phone", None) if lm else None) or m.phone or "",
            "name": (getattr(lm, "full_name", None) if lm else None) or m.full_name or "",
        })
    return recipients


