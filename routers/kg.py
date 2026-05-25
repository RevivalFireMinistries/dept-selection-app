"""Kingdom Gateway routes for the portal.

Three audiences:

* **Members** (`/portal/kg/*`) — see their cycle, attend classes, take the
  exam, download their certificate.
* **Facilitators** (`/desk/kg/*`) — mark attendance live during class,
  enter onsite exam marks. Uses the existing desk session cookie.
* **Admin** (`/admin/kg`) — read-only summary that deep-links to the KG
  admin UI for editing course content.

Every page degrades gracefully when KG is unreachable / not configured —
the kill switch in `kingdom_gateway_client.is_enabled()` keeps the portal
working even if KG is down.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import (
    HTMLResponse, RedirectResponse, Response, StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import kingdom_gateway_client as kg
from database import get_db
from models import Member
from routers.pages import (
    get_current_member, is_authenticated, is_desk_authenticated,
)


router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kg_disabled_page(request: Request, audience: str = "member") -> HTMLResponse:
    """Friendly "not available right now" page shown when KG is off."""
    return templates.TemplateResponse(
        request, "kg/disabled.html",
        {"audience": audience},
        status_code=503,
    )


def _require_member(request: Request, db: Session) -> tuple[Member | None, Optional[RedirectResponse]]:
    """Convenience: return (member, redirect) where redirect is set if no
    member is logged in. Caller bails out by returning the redirect."""
    member = get_current_member(request, db)
    if not member:
        return None, RedirectResponse(url="/?next=/portal/kg", status_code=302)
    return member, None


def _member_external_id(member: Member) -> str | None:
    """KG identifies members by their rfm-database UUID. The portal stores
    it on `Member.external_member_id` when the central-DB integration is
    on. Without it, we can't talk to KG for this member."""
    return (member.external_member_id or "").strip() or None


# ---------------------------------------------------------------------------
# MEMBER — landing
# ---------------------------------------------------------------------------

@router.get("/portal/kg", response_class=HTMLResponse)
async def portal_kg_home(request: Request, db: Session = Depends(get_db)):
    member, redirect = _require_member(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    external_id = _member_external_id(member)
    enrollments: list = []
    error: str | None = None

    if not external_id:
        error = (
            "We can't find you in the central member database yet. "
            "Please contact the church office so they can link your record."
        )
    else:
        r = kg.list_my_enrollments(external_member_id=external_id, db=db)
        if r.ok:
            data = r.data
            if isinstance(data, dict):
                data = data.get("data") or []
            enrollments = data or []
        else:
            error = r.error or "Could not reach Kingdom Gateway right now."

    return templates.TemplateResponse(
        request, "kg/portal_home.html",
        {
            "member": member,
            "enrollments": enrollments,
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# MEMBER — cycle detail (schedule + status)
# ---------------------------------------------------------------------------

@router.get("/portal/kg/cycle/{cycle_id}", response_class=HTMLResponse)
async def portal_kg_cycle(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    member, redirect = _require_member(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    external_id = _member_external_id(member)
    if not external_id:
        return RedirectResponse(url="/portal/kg", status_code=302)

    cycle_r = kg.get_cycle(cycle_id, db=db)
    classes_r = kg.list_classes(cycle_id, db=db)
    enrol_r = kg.list_my_enrollments(external_member_id=external_id, db=db)
    cycle_milestones_r = kg.list_cycle_milestones(cycle_id, db=db)

    cycle = cycle_r.data if cycle_r.ok else None
    classes = classes_r.data if classes_r.ok else []
    if isinstance(classes, dict):
        classes = classes.get("data") or []
    enrollments = enrol_r.data if enrol_r.ok else []
    if isinstance(enrollments, dict):
        enrollments = enrollments.get("data") or []
    my_enrollment = next(
        (e for e in enrollments if e.get("cycle_id") == cycle_id), None,
    )

    # Milestones for this cycle + which ones this member has achieved.
    cycle_milestones = cycle_milestones_r.data if cycle_milestones_r.ok else []
    if isinstance(cycle_milestones, dict):
        cycle_milestones = cycle_milestones.get("data") or []
    achieved_ids: set[str] = set()
    if my_enrollment:
        ach_r = kg.list_enrollment_milestones(my_enrollment["id"], db=db)
        if ach_r.ok:
            rows = (
                ach_r.data if isinstance(ach_r.data, list)
                else (ach_r.data or {}).get("data") or []
            )
            achieved_ids = {r["milestone_id"] for r in rows if r.get("milestone_id")}

    exam_id = (cycle or {}).get("exam_id")

    return templates.TemplateResponse(
        request, "kg/portal_cycle.html",
        {
            "member": member,
            "cycle": cycle,
            "classes": classes,
            "enrollment": my_enrollment,
            "cycle_milestones": cycle_milestones,
            "achieved_ids": achieved_ids,
            "exam_id": exam_id,
            "error": (None if cycle_r.ok else cycle_r.error),
        },
    )


# ---------------------------------------------------------------------------
# MEMBER — accept invite link
# ---------------------------------------------------------------------------

@router.get("/portal/kg/accept", response_class=HTMLResponse)
async def portal_kg_accept(
    request: Request, token: str = "", db: Session = Depends(get_db),
):
    """Lands a member on /portal/kg/accept?token=… (built by KG when the
    invite email goes out). We forward the token to KG which flips their
    enrollment to ENROLLED."""
    member, redirect = _require_member(request, db)
    if redirect:
        # Preserve the token through the login flow.
        return RedirectResponse(url=f"/?next=/portal/kg/accept?token={token}", status_code=302)
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    result = kg.accept_invite(token=token, db=db) if token else None
    if result is None or not result.ok:
        return templates.TemplateResponse(
            request, "kg/portal_accept.html",
            {
                "member": member,
                "ok": False,
                "error": (result.error if result else "Missing or invalid invite token."),
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        request, "kg/portal_accept.html",
        {
            "member": member,
            "ok": True,
            "enrollment": result.data,
        },
    )


# ---------------------------------------------------------------------------
# MEMBER — take exam
# ---------------------------------------------------------------------------

@router.get("/portal/kg/exam/{exam_id}/start", response_class=HTMLResponse)
async def portal_kg_exam_start(
    exam_id: str, request: Request, db: Session = Depends(get_db),
):
    member, redirect = _require_member(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    external_id = _member_external_id(member)
    if not external_id:
        return RedirectResponse(url="/portal/kg", status_code=302)

    r = kg.start_attempt(exam_id=exam_id, external_member_id=external_id, db=db)
    if not r.ok:
        return templates.TemplateResponse(
            request, "kg/portal_exam_error.html",
            {"member": member, "error": r.error},
            status_code=400,
        )
    return templates.TemplateResponse(
        request, "kg/portal_exam.html",
        {"member": member, "attempt": r.data},
    )


@router.post("/portal/kg/exam/submit")
async def portal_kg_exam_submit(
    request: Request, db: Session = Depends(get_db),
):
    """Submit an exam attempt. We accept multipart form data so the page
    can be a plain `<form>` — answers are encoded as `q-<question_id>` per
    question. Each field's value is the answer in stringified form (the
    template knows how to serialise per question type)."""
    member, redirect = _require_member(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    form = await request.form()
    attempt_id = (form.get("attempt_id") or "").strip()
    if not attempt_id:
        return RedirectResponse(url="/portal/kg", status_code=303)

    # Reconstruct the answers list. Field naming convention:
    #   q-<question_id>-type    -> MCQ | TRUE_FALSE | FILL_IN_BLANK | SHORT_ANSWER
    #   q-<question_id>-value   -> primary value(s) for the question
    # FILL_IN_BLANK uses repeated `q-<id>-blank` fields, one per blank.
    answers = _build_answers_from_form(form)

    r = kg.submit_attempt(attempt_id=attempt_id, answers=answers, db=db)
    if not r.ok:
        return templates.TemplateResponse(
            request, "kg/portal_exam_error.html",
            {"member": member, "error": r.error},
            status_code=400,
        )
    return templates.TemplateResponse(
        request, "kg/portal_results.html",
        {"member": member, "result": r.data},
    )


def _build_answers_from_form(form) -> list[dict]:
    """Walk the multipart form into the answers shape KG expects.

    Each question writes one or more form fields. We identify them by the
    `q-<uuid>-type` field and combine related fields by uuid.
    """
    # Group field names by question id
    by_qid: dict[str, dict] = {}
    for key in form.keys():
        if not key.startswith("q-"):
            continue
        # e.g. "q-7c8f...-type", "q-7c8f...-value", "q-7c8f...-blank"
        parts = key.split("-")
        if len(parts) < 3:
            continue
        qid = "-".join(parts[1:-1])
        field = parts[-1]
        bucket = by_qid.setdefault(qid, {"_blanks": []})
        if field == "blank":
            # `form.getlist` keeps repeats — we collected multiple fields
            # named "q-<id>-blank" for multi-blank fill-ins.
            try:
                values = form.getlist(key)
            except AttributeError:
                values = [form[key]]
            bucket["_blanks"].extend(v for v in values if v is not None)
        else:
            bucket[field] = form[key]

    answers: list[dict] = []
    for qid, b in by_qid.items():
        qtype = (b.get("type") or "").upper()
        raw = b.get("value")
        ans: dict = {}
        if qtype == "MCQ":
            try:
                ans["choice"] = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                ans["choice"] = None
        elif qtype == "TRUE_FALSE":
            ans["value"] = (str(raw).lower() == "true")
        elif qtype == "FILL_IN_BLANK":
            ans["answers"] = [str(v) for v in b["_blanks"]]
        else:
            ans["text"] = str(raw or "")
        answers.append({"question_id": qid, "answer": ans})
    return answers


# ---------------------------------------------------------------------------
# MEMBER — certificate
# ---------------------------------------------------------------------------

@router.get("/portal/kg/certificate/{enrollment_id}")
async def portal_kg_certificate(
    enrollment_id: str, request: Request, db: Session = Depends(get_db),
):
    member, redirect = _require_member(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    ok, body, err = kg.get_certificate_pdf_bytes(enrollment_id, db=db)
    if not ok or not body:
        return templates.TemplateResponse(
            request, "kg/portal_exam_error.html",
            {"member": member, "error": err or "Certificate not available yet."},
            status_code=404,
        )
    headers = {
        "Content-Disposition": f'attachment; filename="kingdom-gateway-{enrollment_id}.pdf"',
    }
    return Response(content=body, media_type="application/pdf", headers=headers)


# ---------------------------------------------------------------------------
# FACILITATOR (Info Desk) — cycle list + attendance + onsite marks
# ---------------------------------------------------------------------------

def _require_desk(request: Request) -> Optional[RedirectResponse]:
    if not is_desk_authenticated(request):
        return RedirectResponse(url="/desk/login?next=/desk/kg", status_code=302)
    return None


@router.get("/desk/kg", response_class=HTMLResponse)
async def desk_kg_home(request: Request, db: Session = Depends(get_db)):
    redirect = _require_desk(request)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request, audience="facilitator")

    # Facilitators see active cycles for the whole assembly the desk key
    # is scoped to. KG enforces tenant scope server-side.
    r = kg.health_check(db=db)   # sanity probe so the page can show "KG down"
    cycles_resp = kg._request("GET", "/api/v1/cycles", db=db, params={"status": "ACTIVE"})
    cycles = cycles_resp.data if cycles_resp.ok else []
    if isinstance(cycles, dict):
        cycles = cycles.get("data") or []

    return templates.TemplateResponse(
        request, "kg/desk_home.html",
        {"cycles": cycles, "kg_health_ok": r.ok, "error": cycles_resp.error if not cycles_resp.ok else None},
    )


@router.get("/desk/kg/cycle/{cycle_id}/attendance", response_class=HTMLResponse)
async def desk_kg_attendance_page(
    cycle_id: str, request: Request, class_id: str = "",
    db: Session = Depends(get_db),
):
    """Live attendance roster for one class. ?class_id= pre-selects the
    class — we keep it as a query param so the facilitator can switch
    classes without losing their place."""
    redirect = _require_desk(request)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request, audience="facilitator")

    cycle_r = kg.get_cycle(cycle_id, db=db)
    classes_r = kg.list_classes(cycle_id, db=db)
    enrol_r = kg.list_enrollments_for_cycle(cycle_id, db=db)

    classes = classes_r.data if classes_r.ok else []
    if isinstance(classes, dict):
        classes = classes.get("data") or []
    enrollments = enrol_r.data if enrol_r.ok else []
    if isinstance(enrollments, dict):
        enrollments = enrollments.get("data") or []

    selected_class = None
    if class_id:
        selected_class = next((c for c in classes if c.get("id") == class_id), None)
    if not selected_class and classes:
        selected_class = classes[0]

    return templates.TemplateResponse(
        request, "kg/desk_attendance.html",
        {
            "cycle": cycle_r.data if cycle_r.ok else None,
            "classes": classes,
            "selected_class": selected_class,
            "enrollments": enrollments,
            "error": (cycle_r.error if not cycle_r.ok else None),
        },
    )


@router.post("/desk/kg/cycle/{cycle_id}/attendance")
async def desk_kg_attendance_submit(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    redirect = _require_desk(request)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request, audience="facilitator")

    form = await request.form()
    class_id = (form.get("class_id") or "").strip()
    if not class_id:
        return RedirectResponse(url=f"/desk/kg/cycle/{cycle_id}/attendance", status_code=303)

    # Each row: status-<enrollment_id> = PRESENT|LATE|ABSENT|EXCUSED
    entries = []
    for key, value in form.multi_items() if hasattr(form, "multi_items") else form.items():
        if not key.startswith("status-"):
            continue
        enrollment_id = key[len("status-"):]
        status = (value or "").strip().upper()
        if status not in ("PRESENT", "LATE", "ABSENT", "EXCUSED"):
            continue
        entries.append({"enrollment_id": enrollment_id, "status": status, "method": "MANUAL"})

    if entries:
        kg.bulk_mark_attendance(class_session_id=class_id, entries=entries, db=db)

    return RedirectResponse(
        url=f"/desk/kg/cycle/{cycle_id}/attendance?class_id={class_id}&saved=1",
        status_code=303,
    )


@router.get("/desk/kg/cycle/{cycle_id}/onsite-exam", response_class=HTMLResponse)
async def desk_kg_onsite_exam_page(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    redirect = _require_desk(request)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request, audience="facilitator")

    cycle_r = kg.get_cycle(cycle_id, db=db)
    enrol_r = kg.list_enrollments_for_cycle(cycle_id, db=db)
    enrollments = enrol_r.data if enrol_r.ok else []
    if isinstance(enrollments, dict):
        enrollments = enrollments.get("data") or []
    cycle = cycle_r.data if cycle_r.ok else None
    exam_id = (cycle or {}).get("exam_id")

    return templates.TemplateResponse(
        request, "kg/desk_onsite_exam.html",
        {
            "cycle": cycle,
            "exam_id": exam_id,
            "enrollments": enrollments,
            "error": cycle_r.error if not cycle_r.ok else None,
        },
    )


@router.post("/desk/kg/cycle/{cycle_id}/onsite-exam")
async def desk_kg_onsite_exam_submit(
    cycle_id: str, request: Request,
    exam_id: str = Form(...),
    db: Session = Depends(get_db),
):
    redirect = _require_desk(request)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request, audience="facilitator")

    form = await request.form()
    # Field naming: score-<enrollment_id>, max-<enrollment_id>
    enrollments: dict[str, dict] = {}
    for key in form.keys():
        if "-" not in key:
            continue
        prefix, eid = key.split("-", 1)
        if prefix not in ("score", "max", "notes"):
            continue
        enrollments.setdefault(eid, {})[prefix] = form[key]

    recorded = 0
    errors = []
    for eid, payload in enrollments.items():
        try:
            raw = int(payload.get("score") or 0)
            mx = int(payload.get("max") or 0)
        except (TypeError, ValueError):
            errors.append(eid)
            continue
        if mx <= 0:
            continue   # skip blank rows
        r = kg.record_onsite_mark(
            enrollment_id=eid, exam_id=exam_id,
            raw_score=raw, max_score=mx,
            grader_notes=(payload.get("notes") or None), db=db,
        )
        if r.ok:
            recorded += 1
        else:
            errors.append(eid)

    return RedirectResponse(
        url=f"/desk/kg/cycle/{cycle_id}/onsite-exam?recorded={recorded}&errors={len(errors)}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# ADMIN — read-only overview + deep link to KG admin
# ---------------------------------------------------------------------------

@router.get("/admin/kg", response_class=HTMLResponse)
async def admin_kg_overview(request: Request, db: Session = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login?next=/admin/kg", status_code=302)

    kg_url = kg.get_api_url(db)
    configured = kg.is_configured(db)
    enabled = kg.is_enabled(db)
    health = kg.health_check(db=db) if enabled else None

    # Portal actions run in the admin's own assembly context — never
    # global. Surface a clear banner if the admin isn't linked to one yet.
    asm_id, asm_err = _resolve_portal_assembly(request, db)
    assembly_name = _lookup_assembly_name(asm_id, db) if asm_id else None

    cycles: list = []
    if enabled and configured and asm_id:
        r = kg._request(
            "GET", "/api/v1/cycles", db=db,
            params={"assembly_id": asm_id, "size": 50},
        )
        if r.ok:
            data = r.data
            if isinstance(data, dict):
                cycles = data.get("data") or []
            else:
                cycles = data or []

    return templates.TemplateResponse(
        request, "kg/admin_overview.html",
        {
            "kg_url": kg_url,
            "configured": configured,
            "enabled": enabled,
            "health_ok": (health.ok if health else None),
            "health_error": (health.error if health and not health.ok else None),
            "cycles": cycles,
            "assembly_id": asm_id,
            "assembly_name": assembly_name,
            "assembly_error": asm_err,
        },
    )


# ---------------------------------------------------------------------------
# FACILITATOR (Info Desk) — milestone marking
# ---------------------------------------------------------------------------

@router.get("/desk/kg/cycle/{cycle_id}/milestones", response_class=HTMLResponse)
async def desk_kg_milestones_page(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    redirect = _require_desk(request)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request, audience="facilitator")

    cycle_r = kg.get_cycle(cycle_id, db=db)
    cm_r = kg.list_cycle_milestones(cycle_id, db=db)
    enr_r = kg.list_enrollments_for_cycle(cycle_id, db=db)

    cycle = cycle_r.data if cycle_r.ok else None
    cycle_milestones = cm_r.data if cm_r.ok else []
    if isinstance(cycle_milestones, dict):
        cycle_milestones = cycle_milestones.get("data") or []
    enrollments = enr_r.data if enr_r.ok else []
    if isinstance(enrollments, dict):
        enrollments = enrollments.get("data") or []

    # Achievement lookup: { (enrollment_id, milestone_id): true } so the
    # template can render a tick instantly without N round trips.
    achieved: dict[tuple[str, str], bool] = {}
    for e in enrollments:
        ach_r = kg.list_enrollment_milestones(e["id"], db=db)
        if ach_r.ok:
            rows = (
                ach_r.data if isinstance(ach_r.data, list)
                else (ach_r.data or {}).get("data") or []
            )
            for row in rows:
                achieved[(e["id"], row["milestone_id"])] = True

    return templates.TemplateResponse(
        request, "kg/desk_milestones.html",
        {
            "cycle": cycle,
            "cycle_milestones": cycle_milestones,
            "enrollments": enrollments,
            "achieved": achieved,
            "error": cycle_r.error if not cycle_r.ok else None,
        },
    )


@router.post("/desk/kg/cycle/{cycle_id}/milestones")
async def desk_kg_milestones_mark(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    """Toggle one (enrollment, milestone) achievement. Form fields:
    enrollment_id, milestone_id, action (achieve|revoke)."""
    redirect = _require_desk(request)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request, audience="facilitator")

    form = await request.form()
    enrollment_id = (form.get("enrollment_id") or "").strip()
    milestone_id = (form.get("milestone_id") or "").strip()
    action = (form.get("action") or "achieve").lower()
    if not enrollment_id or not milestone_id:
        return RedirectResponse(
            url=f"/desk/kg/cycle/{cycle_id}/milestones", status_code=303,
        )

    if action == "revoke":
        kg.revoke_milestone(
            enrollment_id=enrollment_id, milestone_id=milestone_id, db=db,
        )
    else:
        kg.mark_milestone_achieved(
            enrollment_id=enrollment_id, milestone_id=milestone_id, db=db,
        )
    return RedirectResponse(
        url=f"/desk/kg/cycle/{cycle_id}/milestones", status_code=303,
    )


# ===========================================================================
# PORTAL ADMIN — full cycle management (admin OR member with kg_manager flag)
# ===========================================================================
#
# Portal admins (admin_session) always pass. Members granted the kg_manager
# flag from /admin/kg/team also pass — they reach these pages via their
# normal member session.
#
# This is intentionally a separate auth path from the existing /admin/*
# pages so we don't have to broaden the admin_session check; KG management
# stays a per-member toggle the portal admin controls.


def _kg_can_manage(request: Request, db: Session) -> tuple[bool, Optional[Member]]:
    """Returns (authorised, acting_member). Acting member is the logged-in
    portal member when authorisation came via the kg_manager flag; None
    when the caller is a portal admin via admin_session."""
    if is_authenticated(request):
        return True, None
    member = get_current_member(request, db)
    if member and getattr(member, "kg_manager", False):
        return True, member
    return False, None


def _require_kg_manage(request: Request, db: Session) -> Optional[RedirectResponse]:
    ok, _ = _kg_can_manage(request, db)
    if not ok:
        return RedirectResponse(url="/admin/login?next=/admin/kg", status_code=302)
    return None


def _portal_session_member(request: Request, db: Session) -> Optional[Member]:
    """Resolve the Member behind the current portal session — works for
    both portal-admin (admin_session + signed identity cookie pointing at
    a Member) and kg_manager member sessions."""
    if is_authenticated(request):
        from routers.pages import get_admin_identity
        identity = get_admin_identity(request)
        if identity and identity.get("member_id"):
            try:
                mid = int(identity["member_id"])
            except (TypeError, ValueError):
                return None
            return db.query(Member).filter(Member.id == mid).first()
        return None
    return get_current_member(request, db)


def _lookup_assembly_name(assembly_id: Optional[str], db: Session) -> Optional[str]:
    """Resolve an assembly UUID to its human name via rfm-database. Best
    effort — returns None if the central API is disabled or unreachable.
    We use this purely for display so the admin knows which church their
    actions are landing on; failure is non-fatal."""
    if not assembly_id:
        return None
    try:
        import rfm_api_client as rfm
        r = rfm.get_assembly(assembly_id, db=db)
        if r.ok and isinstance(r.data, dict):
            return r.data.get("name")
    except Exception:
        pass
    return None


def _resolve_portal_assembly(
    request: Request, db: Session,
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(assembly_id, error)`` for the current portal session.

    Every portal action against KG runs in the context of the logged-in
    user's assembly — never global. Only KG SUPERUSER (who logs into KG
    directly, not through this portal) has cross-assembly powers.

    The assembly id comes from ``Member.external_assembly_id`` — the
    rfm-database UUID. If that's not set the user can't act yet, and we
    return a clear error rather than silently falling through to a
    global / default scope.
    """
    member = _portal_session_member(request, db)
    if member is None:
        return None, "Could not resolve your portal identity."
    asm = (member.external_assembly_id or "").strip()
    if not asm:
        return None, (
            "Your account isn't linked to an assembly in the central member "
            "directory yet — ask the church office to link it before "
            "managing Kingdom Gateway cycles."
        )
    return asm, None


# ---------------------------------------------------------------------------
# ADMIN — team management (portal admin ONLY — they decide who's KG team)
# ---------------------------------------------------------------------------

@router.get("/admin/kg/team", response_class=HTMLResponse)
async def admin_kg_team(request: Request, db: Session = Depends(get_db)):
    """Lists members in the 'Kingdom Gateway' department by default so
    the portal admin can flip the kg_manager flag on/off. There's also
    a `?show=all` query to list every member when the admin needs to
    grant access to someone not in that department (e.g., a visiting
    facilitator)."""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login?next=/admin/kg/team", status_code=302)

    from models import Department, MemberDepartment

    show_all = request.query_params.get("show") == "all"

    # Find the Kingdom Gateway department (case-insensitive). If it doesn't
    # exist we surface a helpful note rather than crashing — admin can
    # create it in /admin/departments first, or just flip to "show all".
    kg_dept = (
        db.query(Department)
        .filter(Department.name.ilike("Kingdom Gateway"))
        .first()
    )

    if show_all or kg_dept is None:
        members = (
            db.query(Member)
            .filter(Member.is_active == True)  # noqa: E712
            .order_by(Member.full_name)
            .all()
        )
    else:
        members = (
            db.query(Member)
            .join(MemberDepartment, MemberDepartment.member_id == Member.id)
            .filter(
                MemberDepartment.department_id == kg_dept.id,
                MemberDepartment.status == "approved",
                Member.is_active == True,  # noqa: E712
            )
            .order_by(Member.full_name)
            .all()
        )

    return templates.TemplateResponse(
        request, "kg/admin_team.html",
        {
            "members": members,
            "kg_dept": kg_dept,
            "show_all": show_all,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/admin/kg/team/toggle")
async def admin_kg_team_toggle(
    request: Request, db: Session = Depends(get_db),
):
    """Single-member toggle from the team page. Idempotent — re-posting
    with the same `enable` value is a no-op."""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login?next=/admin/kg/team", status_code=302)

    form = await request.form()
    try:
        member_id = int(form.get("member_id") or 0)
    except (TypeError, ValueError):
        member_id = 0
    if not member_id:
        return RedirectResponse(url="/admin/kg/team", status_code=303)

    enable = (form.get("enable") or "").lower() in ("true", "on", "1")

    member = db.query(Member).filter(Member.id == member_id).first()
    if member:
        member.kg_manager = enable
        db.commit()

    # Preserve ?show=all so the admin doesn't lose their filter state.
    show_all = (form.get("show") == "all")
    qs = "?show=all&saved=1" if show_all else "?saved=1"
    return RedirectResponse(url=f"/admin/kg/team{qs}", status_code=303)


# ---------------------------------------------------------------------------
# ADMIN — cycle CRUD (portal admin OR kg_manager member)
# ---------------------------------------------------------------------------

@router.get("/admin/kg/cycles/new", response_class=HTMLResponse)
async def admin_kg_cycle_new(
    request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    # Resolve which assembly this admin is acting in. If they're not
    # linked to one yet we surface a friendly error instead of letting
    # them fill out a form that will fail at submit time.
    asm_id, asm_err = _resolve_portal_assembly(request, db)
    assembly_name = _lookup_assembly_name(asm_id, db) if asm_id else None

    # Pull the catalog from KG: courses + modules + milestones.
    courses_r = kg.list_courses(db=db)
    courses = (
        courses_r.data if isinstance(courses_r.data, list)
        else (courses_r.data or {}).get("data") or []
    ) if courses_r.ok else []

    default_course = courses[0] if courses else None
    modules: list = []
    if default_course:
        m_r = kg.list_modules(default_course["id"], db=db)
        modules = (
            m_r.data if isinstance(m_r.data, list)
            else (m_r.data or {}).get("data") or []
        ) if m_r.ok else []

    ms_r = kg.list_kg_milestones(db=db)
    milestones = (
        ms_r.data if isinstance(ms_r.data, list)
        else (ms_r.data or {}).get("data") or []
    ) if ms_r.ok else []

    return templates.TemplateResponse(
        request, "kg/admin_cycle_new.html",
        {
            "courses": courses,
            "default_course": default_course,
            "modules": modules,
            "milestones": milestones,
            "assembly_id": asm_id,
            "assembly_name": assembly_name,
            "assembly_error": asm_err,
            "error": request.session.pop("flash_cycle_error", None) if hasattr(request, "session") else None,
        },
    )


@router.post("/admin/kg/cycles/new")
async def admin_kg_cycle_create(
    request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    form = await request.form()

    # Collect module + milestone selections from the form.
    if hasattr(form, "getlist"):
        module_ids = [m for m in form.getlist("module_ids") if m]
    else:
        module_ids = [form.get("module_ids")] if form.get("module_ids") else []

    milestones: list[dict] = []
    for key in list(form.keys()):
        if key.startswith("milestone_") and form.get(key):
            ms_id = key[len("milestone_"):]
            milestones.append({
                "milestone_id": ms_id,
                "is_mandatory": bool(form.get(f"mandatory_{ms_id}")),
            })

    # Force the assembly from the portal session — portal callers
    # never set this themselves. Cross-assembly creation is a KG
    # SUPERUSER-only operation done from KG admin directly.
    asm_id, asm_err = _resolve_portal_assembly(request, db)
    if not asm_id:
        if hasattr(request, "session"):
            request.session["flash_cycle_error"] = asm_err
        return RedirectResponse(url="/admin/kg/cycles/new", status_code=303)

    payload = {
        "course_id": (form.get("course_id") or "").strip(),
        "assembly_id": asm_id,
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or None),
        "start_date": (form.get("start_date") or "").strip(),
        "cadence_days": int(form.get("cadence_days") or 7),
        "class_start_time": (form.get("class_start_time") or "10:00").strip(),
        "class_duration_minutes": int(form.get("class_duration_minutes") or 90),
        "venue": (form.get("venue") or None),
        "module_ids": module_ids,
        "milestones": milestones,
        "pass_mark_percent": int(form.get("pass_mark_percent") or 50),
        "min_attendance_percent": int(form.get("min_attendance_percent") or 80),
        "max_exam_attempts": int(form.get("max_exam_attempts") or 0),
        "exam_time_limit_minutes": int(form.get("exam_time_limit_minutes") or 50),
    }

    r = kg.plan_cycle(payload=payload, db=db)
    if not r.ok:
        if hasattr(request, "session"):
            request.session["flash_cycle_error"] = r.error or "Could not create cycle."
        return RedirectResponse(url="/admin/kg/cycles/new", status_code=303)

    cycle_id = (r.data or {}).get("id") if isinstance(r.data, dict) else None
    if cycle_id:
        return RedirectResponse(url=f"/admin/kg/cycles/{cycle_id}", status_code=303)
    return RedirectResponse(url="/admin/kg", status_code=303)


@router.get("/admin/kg/cycles/{cycle_id}", response_class=HTMLResponse)
async def admin_kg_cycle_detail(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    asm_id, _ = _resolve_portal_assembly(request, db)

    cycle_r = kg.get_cycle(cycle_id, db=db)
    cycle = cycle_r.data if cycle_r.ok else None

    # Refuse cross-assembly access — quietly redirect back to /admin/kg
    # so URL-poking can't reveal whether a cycle in another assembly
    # exists. KG SUPERUSER (who can see across) uses KG admin directly.
    if cycle and asm_id and isinstance(cycle, dict) and cycle.get("assembly_id") != asm_id:
        return RedirectResponse(url="/admin/kg", status_code=303)

    classes_r = kg.list_classes(cycle_id, db=db)
    cm_r = kg.list_cycle_milestones(cycle_id, db=db)
    enr_r = kg.list_enrollments_for_cycle(cycle_id, db=db)

    classes = classes_r.data if classes_r.ok else []
    if isinstance(classes, dict):
        classes = classes.get("data") or []
    cycle_milestones = cm_r.data if cm_r.ok else []
    if isinstance(cycle_milestones, dict):
        cycle_milestones = cycle_milestones.get("data") or []
    enrollments = enr_r.data if enr_r.ok else []
    if isinstance(enrollments, dict):
        enrollments = enrollments.get("data") or []

    return templates.TemplateResponse(
        request, "kg/admin_cycle_detail.html",
        {
            "cycle": cycle,
            "classes": classes,
            "cycle_milestones": cycle_milestones,
            "enrollments": enrollments,
            "error": cycle_r.error if not cycle_r.ok else None,
        },
    )
