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
from datetime import date as _date, datetime, timezone
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

    # Where to land them depends on how far they've got:
    #
    #   - Already attempted an exam? → /portal/kg/results/{id}. Once
    #     they've written, the score and what-they-got-wrong is the
    #     thing they actually want to see; the cycle's class schedule
    #     is one click away on that page if they need it.
    #   - Accepted an invite (any non-INVITED status) → cycle page.
    #   - Only INVITED / no enrollments → stay on the listing so the
    #     accept-invite UI is reachable.
    if external_id:
        try:
            ar = kg.list_exam_attempts(
                external_member_id=external_id, page=1, size=1, db=db,
            )
            if ar.ok:
                arows = (
                    ar.data if isinstance(ar.data, list)
                    else (ar.data or {}).get("data") or []
                )
                if arows and arows[0].get("id"):
                    return RedirectResponse(
                        url=f"/portal/kg/results/{arows[0]['id']}",
                        status_code=303,
                    )
        except Exception:
            # Best effort — fall through to the cycle redirect below.
            pass

    accepted = next(
        (e for e in enrollments if e.get("status") and e.get("status") != "INVITED"),
        None,
    )
    if accepted and accepted.get("cycle_id"):
        return RedirectResponse(
            url=f"/portal/kg/cycle/{accepted['cycle_id']}", status_code=303,
        )

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
    attendance_by_class: dict[str, dict] = {}
    if my_enrollment:
        ach_r = kg.list_enrollment_milestones(my_enrollment["id"], db=db)
        if ach_r.ok:
            rows = (
                ach_r.data if isinstance(ach_r.data, list)
                else (ach_r.data or {}).get("data") or []
            )
            achieved_ids = {r["milestone_id"] for r in rows if r.get("milestone_id")}

        # Attendance per class — render ✓/late/✗ markers in the schedule.
        att_r = kg.list_attendance_by_enrollment(my_enrollment["id"], db=db)
        if att_r.ok:
            rows = (
                att_r.data if isinstance(att_r.data, list)
                else (att_r.data or {}).get("data") or []
            )
            attendance_by_class = {
                r["class_session_id"]: r for r in rows if r.get("class_session_id")
            }

    exam_id = (cycle or {}).get("exam_id")

    # If they've already attempted the exam at least once, surface the
    # most recent attempt id so the cycle page can link to the detailed
    # results view. Best-effort — the page renders fine without it.
    latest_attempt_id: str | None = None
    if my_enrollment and my_enrollment.get("status") in (
        "EXAM_READY", "FAILED", "COMPLETED",
    ):
        att_r = kg.list_exam_attempts(
            external_member_id=external_id,
            cycle_id=cycle_id, page=1, size=1, db=db,
        )
        if att_r.ok:
            attempts = (
                att_r.data if isinstance(att_r.data, list)
                else (att_r.data or {}).get("data") or []
            )
            if attempts:
                latest_attempt_id = attempts[0].get("id")

    # Gate the "Take the exam" button on exam_available_at — admins can
    # schedule the opening for a future moment without flipping any
    # background job. If the timestamp is in the past (or absent — meaning
    # the legacy attendance-only flow set EXAM_READY), the exam is open.
    exam_is_open = False
    if my_enrollment:
        avail = (my_enrollment.get("exam_available_at") or "").strip()
        if not avail:
            exam_is_open = True
        else:
            from datetime import datetime, timezone
            try:
                # Strip trailing Z if rfm-database returned ISO Z form.
                when = datetime.fromisoformat(avail.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                exam_is_open = when <= datetime.now(timezone.utc)
            except ValueError:
                # Bad value — fail open so a stray format doesn't block the member.
                exam_is_open = True

    # Deep-link bounce: the EXAM_UNLOCKED email points the member at
    # /portal/kg/cycle/{id}?start_exam=1 so they land one click from
    # writing. When the exam is actually open and the cycle has an
    # exam attached, jump straight into it. If the exam isn't open yet
    # (scheduled future, or no exam at all) we just render the cycle
    # page — the member sees the "scheduled for X" badge there.
    want_start = (request.query_params.get("start_exam") or "").strip() in ("1", "true", "yes")
    if (
        want_start
        and exam_is_open
        and exam_id
        and my_enrollment
        and my_enrollment.get("status") in ("EXAM_READY", "FAILED")
    ):
        return RedirectResponse(
            url=f"/portal/kg/exam/{exam_id}/start", status_code=303,
        )

    return templates.TemplateResponse(
        request, "kg/portal_cycle.html",
        {
            "member": member,
            "cycle": cycle,
            "classes": classes,
            "enrollment": my_enrollment,
            "cycle_milestones": cycle_milestones,
            "achieved_ids": achieved_ids,
            "attendance_by_class": attendance_by_class,
            "exam_id": exam_id,
            "exam_is_open": exam_is_open,
            "latest_attempt_id": latest_attempt_id,
            "error": (None if cycle_r.ok else cycle_r.error),
        },
    )


# ---------------------------------------------------------------------------
# MEMBER — accept invite link
# ---------------------------------------------------------------------------

@router.post("/portal/kg/request-to-join")
async def portal_kg_request_to_join(
    request: Request, db: Session = Depends(get_db),
):
    """Self-service flag: the member tells the team they'd like to join
    a future cycle. The team picks them up from the dashboard's
    Interested filter and bulk-invites when a suitable cycle is open."""
    member, redirect = _require_member(request, db)
    if redirect:
        return redirect
    member.kg_interested = True
    member.kg_interested_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url="/portal", status_code=303)


@router.post("/portal/kg/withdraw-interest")
async def portal_kg_withdraw_interest(
    request: Request, db: Session = Depends(get_db),
):
    member, redirect = _require_member(request, db)
    if redirect:
        return redirect
    member.kg_interested = False
    member.kg_interested_at = None
    db.commit()
    return RedirectResponse(url="/portal", status_code=303)


@router.post("/portal/kg/accept-direct")
async def portal_kg_accept_direct(
    request: Request, db: Session = Depends(get_db),
):
    """Member accepts an invitation in-portal (no token needed — the
    portal session identifies them and we verify the enrollment is
    theirs before forwarding to KG)."""
    member, redirect = _require_member(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    form = await request.form()
    enrollment_id = (form.get("enrollment_id") or "").strip()
    if not enrollment_id:
        return RedirectResponse(url="/portal/kg", status_code=303)

    # Tenant-and-identity safety: verify the enrollment is for THIS
    # member's external_member_id before forwarding to KG. Stops a
    # member from accepting someone else's invite by guessing the id.
    external_id = _member_external_id(member)
    if not external_id:
        return RedirectResponse(url="/portal/kg", status_code=303)

    er = kg._request("GET", f"/api/v1/enrollments/{enrollment_id}", db=db)
    if not er.ok or not isinstance(er.data, dict):
        return RedirectResponse(url="/portal/kg", status_code=303)
    if er.data.get("external_member_id") != external_id:
        return RedirectResponse(url="/portal/kg", status_code=303)

    kg.accept_enrollment_direct(enrollment_id, db=db)
    # Wherever they clicked from — default back to /portal/kg.
    back = (form.get("back") or "/portal/kg").strip()
    return RedirectResponse(url=back, status_code=303)


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


@router.post("/admin/kg/attempts/{attempt_id}/regrade")
async def admin_kg_regrade_attempt(
    attempt_id: str, request: Request, db: Session = Depends(get_db),
):
    """Re-run KG's auto-grader against an existing attempt. Surfaced as
    a button on the results detail page for KG team / portal admins —
    useful after we tighten the lenient rules (e.g. "breathe" should
    now match "breath" via stem-aware comparison)."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    form = await request.form()
    back = (form.get("back") or f"/portal/kg/results/{attempt_id}").strip()
    r = kg.regrade_exam_attempt(attempt_id, db=db)
    if not r.ok:
        return _flash_redirect(back, r.error or "Could not regrade.")
    new_pct = None
    if isinstance(r.data, dict):
        new_pct = r.data.get("percent")
    msg = f"Attempt regraded — new score: {new_pct}%." if new_pct is not None else "Attempt regraded."
    return _flash_redirect(back, msg)


@router.get("/portal/kg/results/{attempt_id}", response_class=HTMLResponse)
async def portal_kg_results_detail(
    attempt_id: str, request: Request, db: Session = Depends(get_db),
):
    """Per-question breakdown of one attempt — the page the
    EXAM_RESULTS email links to. Members can re-view their own
    attempts here long after submission."""
    member, redirect = _require_member(request, db)
    if redirect:
        # Preserve the deep link through login so the email click works
        # from a logged-out browser too.
        return RedirectResponse(
            url=f"/?next=/portal/kg/results/{attempt_id}", status_code=302,
        )
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    r = kg.get_exam_attempt_detail(attempt_id, db=db)
    if not r.ok:
        return templates.TemplateResponse(
            request, "kg/portal_exam_error.html",
            {"member": member, "error": r.error or "Results not found."},
            status_code=404,
        )
    data = r.data or {}
    # Only the KG team / portal admins see the "Regrade" button — used
    # after grading-rule improvements (e.g. stem-aware fill-in-blank
    # matching) to refresh a member's score without forcing a retake.
    can_regrade, _ = _kg_can_manage(request, db)
    flash, had_flash = _consume_flash(request)
    response = templates.TemplateResponse(
        request, "kg/portal_results_detail.html",
        {
            "member": member,
            "attempt": data.get("attempt") or {},
            "answers": data.get("answers") or [],
            "can_regrade": can_regrade,
            "flash": flash,
        },
    )
    if had_flash:
        _clear_flash(response)
    return response


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
    # Land the member straight on their per-question breakdown — score,
    # what they got wrong, the correct answers, any facilitator notes.
    # The summary-only page used to live here but the user-research
    # signal was clear: the moment after submitting is exactly when
    # they want to see the detail.
    result_attempt_id = (r.data or {}).get("attempt_id") if isinstance(r.data, dict) else None
    target = (
        f"/portal/kg/results/{result_attempt_id}" if result_attempt_id
        else "/portal/kg"
    )
    return RedirectResponse(url=target, status_code=303)


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
    _decorate_enrollments_with_names(enrollments, db)

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
    _decorate_enrollments_with_names(enrollments, db)
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
    _decorate_enrollments_with_names(enrollments, db)

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


# ---------------------------------------------------------------------------
# Flash messages
#
# The portal doesn't run SessionMiddleware, so we can't lean on
# `request.session`. A tiny HMAC-signed cookie carries one-shot messages
# between POST handlers (which redirect) and the next GET. Same signing
# secret as the existing portal sessions in routers/pages.py.
# ---------------------------------------------------------------------------

_FLASH_COOKIE = "kg_flash"
_FLASH_MAX_AGE = 60  # the next GET should consume it within seconds


def _flash_sign(value: str) -> str:
    import hmac, hashlib, os
    secret = os.environ.get("SESSION_SECRET", "rfm-stellenbosch-portal-2026")
    sig = hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{value}.{sig}"


def _flash_verify(token: Optional[str]) -> Optional[str]:
    import hmac, hashlib, os
    if not token:
        return None
    try:
        value, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    secret = os.environ.get("SESSION_SECRET", "rfm-stellenbosch-portal-2026")
    expected = hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected):
        return None
    return value


def _set_flash(response, message: str) -> None:
    """Stash a one-shot message on the response cookie."""
    import urllib.parse
    encoded = urllib.parse.quote(message)[:500]  # cap to keep cookies small
    response.set_cookie(
        key=_FLASH_COOKIE, value=_flash_sign(encoded),
        max_age=_FLASH_MAX_AGE, httponly=True, samesite="lax",
    )


def _consume_flash(request: Request) -> tuple[Optional[str], bool]:
    """Return (message, was_present). Caller is responsible for clearing
    the cookie on the response (helpers below)."""
    import urllib.parse
    raw = _flash_verify(request.cookies.get(_FLASH_COOKIE))
    if not raw:
        return None, False
    try:
        return urllib.parse.unquote(raw), True
    except Exception:
        return None, True  # was present but garbled — still clear it


def _clear_flash(response) -> None:
    response.delete_cookie(_FLASH_COOKIE)


def _flash_redirect(url: str, message: Optional[str] = None) -> RedirectResponse:
    """Build a redirect response with a flash cookie attached."""
    resp = RedirectResponse(url=url, status_code=303)
    if message:
        _set_flash(resp, message)
    return resp


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
    """Single-cell toggle from the team page. Supports two roles:
    ``role=manager`` flips kg_manager, ``role=teacher`` flips kg_teacher.
    Idempotent — re-posting with the same enable value is a no-op."""
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
    role = (form.get("role") or "manager").lower()

    member = db.query(Member).filter(Member.id == member_id).first()
    if member:
        if role == "teacher":
            member.kg_teacher = enable
        else:
            member.kg_manager = enable
        db.commit()

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

    flash, had_flash = _consume_flash(request)
    response = templates.TemplateResponse(
        request, "kg/admin_cycle_new.html",
        {
            "courses": courses,
            "default_course": default_course,
            "modules": modules,
            "milestones": milestones,
            "assembly_id": asm_id,
            "assembly_name": assembly_name,
            "assembly_error": asm_err,
            "error": flash,
        },
    )
    if had_flash:
        _clear_flash(response)
    return response


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
        return _flash_redirect("/admin/kg/cycles/new", asm_err)

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
        return _flash_redirect(
            "/admin/kg/cycles/new",
            r.error or "Could not create cycle.",
        )

    cycle_id = (r.data or {}).get("id") if isinstance(r.data, dict) else None
    if cycle_id:
        return RedirectResponse(url=f"/admin/kg/cycles/{cycle_id}", status_code=303)
    return RedirectResponse(url="/admin/kg", status_code=303)


# ---------------------------------------------------------------------------
# ADMIN — class session editor (move dates, change teacher / topic)
# ---------------------------------------------------------------------------

def _combine_datetime(date_str: str | None, time_str: str | None) -> str | None:
    """Combine YYYY-MM-DD + HH:MM form fields into an ISO datetime KG
    accepts. Returns None when the date is missing."""
    if not date_str:
        return None
    time_part = (time_str or "10:00").strip()
    if len(time_part) == 5:
        time_part += ":00"
    return f"{date_str}T{time_part}+00:00"


def _teacher_pool(db: Session, asm_id: str) -> list[Member]:
    """Members in the assembly with the kg_teacher flag."""
    return (
        db.query(Member)
        .filter(
            Member.kg_teacher == True,  # noqa: E712
            Member.external_assembly_id == asm_id,
            Member.is_active == True,  # noqa: E712
        )
        .order_by(Member.full_name)
        .all()
    )


def _member_name_map(db: Session, external_member_ids) -> dict[str, str]:
    """Resolve external_member_id → full_name from the local Member
    mirror in one query. Anything not found falls back to a UUID
    short-prefix so the UI never breaks. Used to label enrollment
    rosters, attendance lists, milestone check-offs etc. with human
    names instead of raw UUIDs."""
    ids = {x for x in external_member_ids if x}
    if not ids:
        return {}
    rows = (
        db.query(Member.external_member_id, Member.full_name)
        .filter(Member.external_member_id.in_(list(ids)))
        .all()
    )
    found = {r[0]: (r[1] or "").strip() for r in rows if r[0]}
    # Fallback so the template never has to special-case missing rows.
    for ext_id in ids:
        if not found.get(ext_id):
            found[ext_id] = f"{ext_id[:8]}…"
    return found


def _decorate_enrollments_with_names(
    enrollments: list[dict], db: Session,
) -> None:
    """Mutate enrollment dicts in place, adding a `_member_name` field
    for templates that display a roster."""
    names = _member_name_map(db, (e.get("external_member_id") for e in enrollments))
    for e in enrollments:
        e["_member_name"] = names.get(e.get("external_member_id"), "")


@router.get(
    "/admin/kg/cycles/{cycle_id}/classes/{class_id}/edit",
    response_class=HTMLResponse,
)
async def admin_kg_class_edit(
    cycle_id: str, class_id: str,
    request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    asm_id, asm_err = _resolve_portal_assembly(request, db)
    if not asm_id:
        return _flash_redirect("/admin/kg", asm_err or "No assembly resolved.")

    cycle_r = kg.get_cycle(cycle_id, db=db)
    cycle = cycle_r.data if cycle_r.ok else None
    if cycle and cycle.get("assembly_id") != asm_id:
        return _flash_redirect("/admin/kg", "Not in your assembly.")

    classes_r = kg.list_classes(cycle_id, db=db)
    cls = None
    if classes_r.ok:
        rows = classes_r.data if isinstance(classes_r.data, list) else (classes_r.data or {}).get("data") or []
        cls = next((c for c in rows if c.get("id") == class_id), None)
    if not cls:
        return _flash_redirect(f"/admin/kg/cycles/{cycle_id}", "Class not found.")

    modules: list = []
    if cycle and cycle.get("course_id"):
        mr = kg.list_modules(cycle["course_id"], db=db)
        if mr.ok:
            modules = mr.data if isinstance(mr.data, list) else (mr.data or {}).get("data") or []

    ms_r = kg.list_kg_milestones(db=db)
    milestones = []
    if ms_r.ok:
        milestones = ms_r.data if isinstance(ms_r.data, list) else (ms_r.data or {}).get("data") or []

    attached_r = kg.list_class_session_milestones(class_id, db=db)
    attached_ids: set = set()
    if attached_r.ok:
        rows = attached_r.data if isinstance(attached_r.data, list) else (attached_r.data or {}).get("data") or []
        attached_ids = {r.get("milestone_id") for r in rows}

    teachers = _teacher_pool(db, asm_id)
    flash, had = _consume_flash(request)
    response = templates.TemplateResponse(
        request, "kg/admin_class_edit.html",
        {
            "cycle": cycle, "cls": cls,
            "modules": modules, "milestones": milestones,
            "attached_milestone_ids": attached_ids,
            "teachers": teachers, "flash": flash,
        },
    )
    if had:
        _clear_flash(response)
    return response


@router.post("/admin/kg/cycles/{cycle_id}/classes/{class_id}/edit")
async def admin_kg_class_edit_submit(
    cycle_id: str, class_id: str,
    request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    form = await request.form()
    payload: dict = {}
    title = (form.get("title") or "").strip()
    if title:
        payload["title"] = title
    starts_at = _combine_datetime(form.get("starts_at_date"), form.get("starts_at_time"))
    if starts_at:
        payload["starts_at"] = starts_at
    ends_at = _combine_datetime(form.get("ends_at_date"), form.get("ends_at_time"))
    if ends_at:
        payload["ends_at"] = ends_at
    venue = form.get("venue")
    if venue is not None:
        payload["venue"] = venue or None
    payload["module_id"] = (form.get("module_id") or "").strip() or None
    payload["facilitator_external_member_id"] = (
        (form.get("facilitator_external_member_id") or "").strip() or None
    )

    r = kg.update_class_session(class_id, payload, db=db)
    if not r.ok:
        return _flash_redirect(
            f"/admin/kg/cycles/{cycle_id}/classes/{class_id}/edit",
            r.error or "Could not update class.",
        )

    milestone_ids = [
        key[len("milestone_"):]
        for key in form.keys()
        if key.startswith("milestone_") and form.get(key)
    ]
    kg.replace_class_session_milestones(class_id, milestone_ids, db=db)

    return _flash_redirect(f"/admin/kg/cycles/{cycle_id}", "Class updated.")


@router.post("/admin/kg/cycles/{cycle_id}/classes/{class_id}/delete")
async def admin_kg_class_delete(
    cycle_id: str, class_id: str,
    request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    kg.delete_class_session(class_id, db=db)
    return _flash_redirect(f"/admin/kg/cycles/{cycle_id}", "Class removed.")


@router.post("/admin/kg/cycles/{cycle_id}/classes/add")
async def admin_kg_class_add(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    form = await request.form()
    starts_date = form.get("starts_at_date")
    # End date defaults to start date — the common case is a same-day
    # class. The dedicated edit form lets KG team pick a different end
    # date when needed (e.g. an overnight retreat).
    ends_date = form.get("ends_at_date") or starts_date
    starts_at = _combine_datetime(starts_date, form.get("starts_at_time")) or ""
    ends_at = _combine_datetime(ends_date, form.get("ends_at_time")) or ""

    # KG requires sequence_no; compute next.
    classes_r = kg.list_classes(cycle_id, db=db)
    existing = []
    if classes_r.ok:
        existing = classes_r.data if isinstance(classes_r.data, list) else (classes_r.data or {}).get("data") or []
    next_seq = (max((c.get("sequence_no") or 0) for c in existing) + 1) if existing else 1

    payload = {
        "cycle_id": cycle_id,
        "sequence_no": next_seq,
        "title": (form.get("title") or f"Session {next_seq}").strip(),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "venue": (form.get("venue") or None),
        "module_id": (form.get("module_id") or "").strip() or None,
        "facilitator_external_member_id": (form.get("facilitator_external_member_id") or "").strip() or None,
    }
    r = kg.create_class_session(payload, db=db)
    if not r.ok:
        return _flash_redirect(
            f"/admin/kg/cycles/{cycle_id}",
            r.error or "Could not add class.",
        )
    return _flash_redirect(f"/admin/kg/cycles/{cycle_id}", "Class added.")


# ---------------------------------------------------------------------------
# ADMIN — edit cycle details (always allowed, even after the cycle has
# started — KG team can still fix a typo in the name, push the end
# date, lower the pass mark etc. The KG service itself imposes no
# status guard on PATCH /cycles/{id}.)
# ---------------------------------------------------------------------------

@router.get(
    "/admin/kg/cycles/{cycle_id}/edit",
    response_class=HTMLResponse,
)
async def admin_kg_cycle_edit(
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

    # Same cross-assembly guard as the detail page.
    if cycle and asm_id and isinstance(cycle, dict) and cycle.get("assembly_id") != asm_id:
        return RedirectResponse(url="/admin/kg", status_code=303)

    flash, had_flash = _consume_flash(request)
    response = templates.TemplateResponse(
        request, "kg/admin_cycle_edit.html",
        {
            "cycle": cycle,
            "error": cycle_r.error if not cycle_r.ok else None,
            "flash": flash,
        },
    )
    if had_flash:
        _clear_flash(response)
    return response


@router.post("/admin/kg/cycles/{cycle_id}/edit")
async def admin_kg_cycle_edit_submit(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    form = await request.form()

    def _opt_int(key: str) -> int | None:
        raw = (form.get(key) or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    fields: dict = {}
    name = (form.get("name") or "").strip()
    if name:
        fields["name"] = name

    description = form.get("description")
    if description is not None:
        # Allow clearing it by submitting empty.
        fields["description"] = (description or None)

    end_date = (form.get("end_date") or "").strip()
    if end_date:
        fields["end_date"] = end_date

    status = (form.get("status") or "").strip().upper()
    if status:
        fields["status"] = status

    for key in (
        "pass_mark_percent",
        "min_attendance_percent",
        "max_exam_attempts",
        "exam_time_limit_minutes",
    ):
        v = _opt_int(key)
        if v is not None:
            fields[key] = v

    if not fields:
        return _flash_redirect(
            f"/admin/kg/cycles/{cycle_id}/edit", "Nothing to update.",
        )

    r = kg.update_cycle(cycle_id, fields, db=db)
    if not r.ok:
        return _flash_redirect(
            f"/admin/kg/cycles/{cycle_id}/edit",
            r.error or "Could not update cycle.",
        )
    return _flash_redirect(
        f"/admin/kg/cycles/{cycle_id}", "Cycle updated.",
    )


# ---------------------------------------------------------------------------
# ADMIN — open exam (single member / whole cycle)
# ---------------------------------------------------------------------------

def _parse_unlock_when(form) -> str | None:
    """Turn the unlock form's when=now|schedule + date/time pickers into
    an ISO datetime string (or None for "open immediately")."""
    when = (form.get("when") or "now").strip().lower()
    if when != "schedule":
        return None
    date_str = (form.get("schedule_date") or "").strip()
    time_str = (form.get("schedule_time") or "").strip() or "10:00"
    if not date_str:
        return None
    if len(time_str) == 5:
        time_str = time_str + ":00"
    return f"{date_str}T{time_str}+00:00"


@router.post("/admin/kg/enrollments/{enrollment_id}/unlock-exam")
async def admin_kg_unlock_exam_member(
    enrollment_id: str, request: Request,
    db: Session = Depends(get_db),
):
    """Open the exam for one member — KG team or portal admin only.

    Form fields:
      when:           "now" | "schedule"
      schedule_date:  YYYY-MM-DD     (when when=schedule)
      schedule_time:  HH:MM           (when when=schedule, defaults 10:00)
      back:           URL to redirect to (defaults to the cycle page)
    """
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    form = await request.form()
    available_at = _parse_unlock_when(form)
    back = (form.get("back") or "/admin/kg").strip()

    r = kg.unlock_exam_for_enrollment(
        enrollment_id, available_at=available_at, db=db,
    )
    if not r.ok:
        return _flash_redirect(back, r.error or "Could not open the exam.")

    if available_at:
        msg = f"Exam scheduled for {available_at.replace('T', ' ')[:16]}."
    else:
        msg = "Exam opened — member can take it now."
    return _flash_redirect(back, msg)


@router.post("/admin/kg/cycles/{cycle_id}/unlock-exam")
async def admin_kg_unlock_exam_cycle(
    cycle_id: str, request: Request,
    db: Session = Depends(get_db),
):
    """Move every non-terminal enrollment in a cycle into exam mode."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    form = await request.form()
    available_at = _parse_unlock_when(form)

    r = kg.unlock_exam_for_cycle(
        cycle_id, available_at=available_at, db=db,
    )
    if not r.ok:
        return _flash_redirect(
            f"/admin/kg/cycles/{cycle_id}",
            r.error or "Could not open the cycle's exam.",
        )

    count = 0
    if isinstance(r.data, dict):
        count = int(r.data.get("unlocked") or 0)
    if available_at:
        msg = f"Exam scheduled for {count} member(s) — opens {available_at.replace('T', ' ')[:16]}."
    else:
        msg = f"Exam opened for {count} member(s)."
    return _flash_redirect(f"/admin/kg/cycles/{cycle_id}", msg)


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
    _decorate_enrollments_with_names(enrollments, db)

    # Resolve teacher names for each class so the table doesn't show raw
    # UUIDs. Cheap — one local query covering the whole assembly.
    teacher_lookup: dict[str, str] = {}
    if asm_id:
        for t in _teacher_pool(db, asm_id):
            if t.external_member_id:
                teacher_lookup[t.external_member_id] = t.full_name
    for c in classes:
        fac = c.get("facilitator_external_member_id")
        c["_teacher_name"] = teacher_lookup.get(fac) if fac else None

    # Modules + teachers needed by the "Add class" form embedded on the
    # detail page.
    modules = []
    if cycle and cycle.get("course_id"):
        mr = kg.list_modules(cycle["course_id"], db=db)
        if mr.ok:
            modules = mr.data if isinstance(mr.data, list) else (mr.data or {}).get("data") or []
    teachers = _teacher_pool(db, asm_id) if asm_id else []

    flash, had_flash = _consume_flash(request)
    response = templates.TemplateResponse(
        request, "kg/admin_cycle_detail.html",
        {
            "cycle": cycle,
            "classes": classes,
            "cycle_milestones": cycle_milestones,
            "enrollments": enrollments,
            "modules": modules,
            "teachers": teachers,
            "error": cycle_r.error if not cycle_r.ok else None,
            "flash": flash,
        },
    )
    if had_flash:
        _clear_flash(response)
    return response


# ---------------------------------------------------------------------------
# ADMIN — legacy completions (mark members who finished KG before this system)
# ---------------------------------------------------------------------------

@router.get("/admin/kg/legacy", response_class=HTMLResponse)
async def admin_kg_legacy_page(request: Request, db: Session = Depends(get_db)):
    """Searchable picker for marking members who finished Kingdom Gateway
    before this system existed. Writes `kingdom_gateway_status=COMPLETED`
    + the completion date back to rfm-database — they then carry a badge
    everywhere their profile renders."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    asm_id, asm_err = _resolve_portal_assembly(request, db)
    if not asm_id:
        return _flash_redirect("/admin/kg", asm_err or "No assembly resolved.")

    search = (request.query_params.get("q") or "").strip()
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1

    import rfm_api_client as rfm
    r = rfm.search_members(
        search=search or None, assembly_id=asm_id,
        page=page, size=25, db=db,
    )
    members: list[dict] = []
    meta: dict = {}
    if r.ok:
        if isinstance(r.data, list):
            members = r.data
        elif isinstance(r.data, dict):
            members = r.data.get("data") or []
            meta = r.data.get("meta") or {}

    flash, had_flash = _consume_flash(request)
    response = templates.TemplateResponse(
        request, "kg/admin_legacy.html",
        {
            "members": members,
            "meta": meta,
            "search": search,
            "page": page,
            "flash": flash,
            "rfm_error": r.error if not r.ok else None,
            "today": _date.today().isoformat(),
        },
    )
    if had_flash:
        _clear_flash(response)
    return response


@router.post("/admin/kg/legacy/mark")
async def admin_kg_legacy_mark(request: Request, db: Session = Depends(get_db)):
    """Mark one member as having completed Kingdom Gateway pre-system.
    Form fields: external_member_id, completed_at (YYYY-MM-DD, optional —
    defaults to today)."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    asm_id, asm_err = _resolve_portal_assembly(request, db)
    if not asm_id:
        return _flash_redirect("/admin/kg", asm_err or "No assembly resolved.")

    form = await request.form()
    member_id = (form.get("external_member_id") or "").strip()
    if not member_id:
        return _flash_redirect("/admin/kg/legacy", "Missing member id.")

    completed_at = (form.get("completed_at") or "").strip()
    if not completed_at:
        completed_at = _date.today().isoformat()

    import rfm_api_client as rfm
    payload = {
        "kingdom_gateway_status": "COMPLETED",
        "kingdom_gateway_completed_at": completed_at,
    }
    r = rfm.update_member(member_id, payload, db=db)
    if not r.ok:
        return _flash_redirect(
            f"/admin/kg/legacy?q={form.get('q') or ''}",
            r.error or "Could not update member.",
        )

    return _flash_redirect(
        f"/admin/kg/legacy?q={form.get('q') or ''}",
        f"Marked complete (effective {completed_at}).",
    )


@router.post("/admin/kg/legacy/clear")
async def admin_kg_legacy_clear(request: Request, db: Session = Depends(get_db)):
    """Undo a legacy completion (admin recorded by mistake)."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    form = await request.form()
    member_id = (form.get("external_member_id") or "").strip()
    if not member_id:
        return _flash_redirect("/admin/kg/legacy", "Missing member id.")

    import rfm_api_client as rfm
    r = rfm.update_member(
        member_id,
        {
            "kingdom_gateway_status": "NOT_STARTED",
            "kingdom_gateway_completed_at": None,
        },
        db=db,
    )
    if not r.ok:
        return _flash_redirect(
            f"/admin/kg/legacy?q={form.get('q') or ''}",
            r.error or "Could not revert.",
        )
    return _flash_redirect(
        f"/admin/kg/legacy?q={form.get('q') or ''}",
        "Reverted.",
    )


# ---------------------------------------------------------------------------
# ADMIN — invite members to a cycle
# ---------------------------------------------------------------------------

@router.get("/admin/kg/cycles/{cycle_id}/invite", response_class=HTMLResponse)
async def admin_kg_cycle_invite_page(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    """Searchable picker for members in the current assembly. Members
    already enrolled in this cycle are shown disabled so the admin can't
    accidentally double-invite. Search is server-side — the portal
    delegates ILIKE matching to rfm-database."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    asm_id, asm_err = _resolve_portal_assembly(request, db)
    if not asm_id:
        # Surface the error on the cycle detail page where they came from.
        return RedirectResponse(url="/admin/kg", status_code=303)

    # Cycle metadata for the header.
    cycle_r = kg.get_cycle(cycle_id, db=db)
    cycle = cycle_r.data if cycle_r.ok else None
    if cycle and cycle.get("assembly_id") != asm_id:
        return RedirectResponse(url="/admin/kg", status_code=303)

    # Search query (passed through to rfm-database). Default to "" so the
    # first load shows the first page.
    search = (request.query_params.get("q") or "").strip()
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1

    # Pull the assembly's members from rfm-database.
    import rfm_api_client as rfm
    members_r = rfm.search_members(
        search=search or None, assembly_id=asm_id,
        page=page, size=25, db=db,
    )
    members: list[dict] = []
    meta: dict = {}
    if members_r.ok:
        if isinstance(members_r.data, list):
            members = members_r.data
        elif isinstance(members_r.data, dict):
            members = members_r.data.get("data") or []
            meta = members_r.data.get("meta") or {}

    # Already-enrolled set for this cycle so we can disable those rows.
    enr_r = kg.list_enrollments_for_cycle(cycle_id, db=db)
    enrolled_ids: set[str] = set()
    if enr_r.ok:
        rows = enr_r.data if isinstance(enr_r.data, list) else (enr_r.data or {}).get("data") or []
        enrolled_ids = {r.get("external_member_id") for r in rows if r.get("external_member_id")}

    result_msg = request.query_params.get("result")
    invited_count = request.query_params.get("invited")
    skipped_count = request.query_params.get("skipped")

    return templates.TemplateResponse(
        request, "kg/admin_cycle_invite.html",
        {
            "cycle": cycle,
            "cycle_id": cycle_id,
            "members": members,
            "meta": meta,
            "search": search,
            "page": page,
            "enrolled_ids": enrolled_ids,
            "result_msg": result_msg,
            "invited_count": invited_count,
            "skipped_count": skipped_count,
            "rfm_error": members_r.error if not members_r.ok else None,
        },
    )


@router.post("/admin/kg/cycles/{cycle_id}/invite")
async def admin_kg_cycle_invite_submit(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect
    if not kg.is_enabled(db):
        return _kg_disabled_page(request)

    asm_id, asm_err = _resolve_portal_assembly(request, db)
    if not asm_id:
        return RedirectResponse(url="/admin/kg", status_code=303)

    cycle_r = kg.get_cycle(cycle_id, db=db)
    cycle = cycle_r.data if cycle_r.ok else None
    if not cycle or cycle.get("assembly_id") != asm_id:
        return RedirectResponse(url="/admin/kg", status_code=303)

    form = await request.form()

    # Picked member rows arrive as multiple `member_id` fields, each
    # value being the rfm-database UUID. We also accept paired fields
    # `email_<uuid>` so the picker can carry the email without an
    # extra round trip — saves N HTTP calls when bulk-inviting.
    if hasattr(form, "getlist"):
        ids = [v for v in form.getlist("member_id") if v]
    else:
        ids = [form.get("member_id")] if form.get("member_id") else []

    # No-email members get an in-portal invite — they'll see it on
    # their dashboard the next time they log in and accept in-app, no
    # email round-trip required.
    skipped: list[str] = []
    members_payload: list[dict] = []
    for mid in ids:
        email = (form.get(f"email_{mid}") or "").strip()
        members_payload.append({
            "external_member_id": mid,
            "email": email or None,
        })

    invited_count = 0
    if members_payload:
        r = kg.bulk_invite(cycle_id=cycle_id, members=members_payload, db=db)
        if r.ok and isinstance(r.data, dict):
            invited_count = r.data.get("invited", 0)
            # Clear the self-request flag for any of these members that
            # had it set — they're enrolled now, the team responded.
            invited_ext_ids = [m["external_member_id"] for m in members_payload]
            db.query(Member).filter(
                Member.external_member_id.in_(invited_ext_ids),
                Member.kg_interested == True,  # noqa: E712
            ).update({"kg_interested": False, "kg_interested_at": None},
                     synchronize_session=False)
            db.commit()
        elif not r.ok:
            # Send the user back with the error.
            return RedirectResponse(
                url=(
                    f"/admin/kg/cycles/{cycle_id}/invite?result=error&"
                    f"invited=0&skipped={len(skipped)}"
                ),
                status_code=303,
            )

    return RedirectResponse(
        url=(
            f"/admin/kg/cycles/{cycle_id}/invite?result=ok&"
            f"invited={invited_count}&skipped={len(skipped)}"
        ),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# ADMIN — mark an INVITED enrollment as accepted manually
# ---------------------------------------------------------------------------

@router.post("/admin/kg/enrollments/{enrollment_id}/mark-accepted")
async def admin_kg_mark_accepted(
    enrollment_id: str, request: Request, db: Session = Depends(get_db),
):
    """Manual mark-accepted — used when a member without email was
    invited verbally and the team needs to flip them to ENROLLED so
    attendance / exam / completion code paths kick in."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    r = kg.accept_enrollment_direct(enrollment_id, db=db)
    form = await request.form()
    # The form sends "back" so we can return the user to wherever they
    # clicked from (cycle detail by default).
    back_url = (form.get("back") or "/admin/kg").strip()
    if not r.ok:
        return _flash_redirect(back_url, r.error or "Could not mark accepted.")
    return _flash_redirect(back_url, "Marked accepted.")


# ===========================================================================
# DASHBOARD — the team's at-a-glance view of who's done what
# ===========================================================================

# Map central kingdom_gateway_status values to the dashboard's UI buckets.
# Keeping it small and explicit means we can re-colour or re-label at one
# spot rather than search every template for the right pill style.
_KG_STATUS_BUCKETS = {
    "COMPLETED":   ("done",      "Completed",       "emerald"),
    "EXEMPTED":    ("done",      "Exempted",        "emerald"),
    "INVITED":     ("active",    "Invited",         "amber"),
    "ENROLLED":    ("active",    "Enrolled",        "blue"),
    "EXAM_READY":  ("active",    "Exam ready",      "blue"),
    "FAILED":      ("failed",    "Failed",          "red"),
    "WITHDRAWN":   ("notyet",    "Withdrew",        "gray"),
    "NOT_STARTED": ("notyet",    "Not started",     "gray"),
}


def _bucket_for(status: str | None) -> tuple[str, str, str]:
    return _KG_STATUS_BUCKETS.get(status or "NOT_STARTED", ("notyet", "Not started", "gray"))


@router.get("/admin/kg/dashboard", response_class=HTMLResponse)
async def admin_kg_dashboard(request: Request, db: Session = Depends(get_db)):
    """One-page command-centre for the team. Three views (people /
    results / cycles) selected via ?tab=. People view supports
    ?status=<bucket> and ?q=<search>, both passed to rfm-database so
    server-side filtering scales as the church grows.

    Everything renders server-side — no client framework needed, fast
    even on a slow phone connection."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    asm_id, asm_err = _resolve_portal_assembly(request, db)
    if not asm_id:
        return _flash_redirect("/admin/kg", asm_err or "No assembly resolved.")

    assembly_name = _lookup_assembly_name(asm_id, db)

    tab = (request.query_params.get("tab") or "people").lower()
    if tab not in ("people", "results", "cycles"):
        tab = "people"

    # Pull stats. We base the buckets on the central member directory's
    # kingdom_gateway_status — that's the canonical "what state is this
    # member in" field, and it's set by both KG completion + legacy
    # marking. The portal-side dashboard therefore stays consistent
    # whether the completion came through KG or pre-dates it.
    import rfm_api_client as rfm

    # Stat-card totals — one paginated fetch per bucket to get the
    # total count from the meta envelope, no data needed.
    stats = {"total": 0, "done": 0, "active": 0, "notyet": 0, "failed": 0, "interested": 0}

    # Interested count is portal-local; one extra query.
    stats["interested"] = (
        db.query(Member)
        .filter(
            Member.kg_interested == True,  # noqa: E712
            Member.external_assembly_id == asm_id,
            Member.is_active == True,  # noqa: E712
        )
        .count()
    )
    counts_query = [
        ("total",  None),
        ("done",   ["COMPLETED", "EXEMPTED"]),
        ("active", ["INVITED", "ENROLLED", "EXAM_READY"]),
        ("notyet", ["NOT_STARTED", "WITHDRAWN"]),
        ("failed", ["FAILED"]),
    ]
    for key, statuses in counts_query:
        if statuses is None:
            r = rfm.search_members(assembly_id=asm_id, page=1, size=1, db=db)
            if r.ok and isinstance(r.data, dict):
                stats[key] = r.data.get("meta", {}).get("total", 0)
            continue
        bucket_total = 0
        for s in statuses:
            r = rfm._request(
                "GET", "/api/v1/members", db=db,
                params={
                    "assembly_id": asm_id,
                    "kingdom_gateway_status": s,
                    "page": 1, "size": 1,
                },
            )
            if r.ok and isinstance(r.data, dict):
                bucket_total += r.data.get("meta", {}).get("total", 0)
        stats[key] = bucket_total

    # Tab-specific data
    people: list[dict] = []
    people_meta: dict = {}
    people_status_filter = ""
    search = ""
    page = 1
    results: list[dict] = []
    results_meta: dict = {}
    selected_cycle_id = ""
    cycles_for_filter: list[dict] = []

    if tab == "people":
        search = (request.query_params.get("q") or "").strip()
        people_status_filter = (request.query_params.get("status") or "").strip()
        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except (TypeError, ValueError):
            page = 1

        # The "Interested" bucket lives entirely portal-side
        # (Member.kg_interested) — bypass rfm-database and read from
        # the local table joined to the assembly.
        if people_status_filter == "interested":
            q = (
                db.query(Member)
                .filter(
                    Member.kg_interested == True,  # noqa: E712
                    Member.external_assembly_id == asm_id,
                    Member.is_active == True,  # noqa: E712
                )
            )
            if search:
                like = f"%{search}%"
                q = q.filter(
                    (Member.full_name.ilike(like)) |
                    (Member.phone.ilike(like)) |
                    (Member.email.ilike(like))
                )
            total = q.count()
            local_rows = (
                q.order_by(Member.kg_interested_at.desc())
                .offset((page - 1) * 25)
                .limit(25)
                .all()
            )
            # Reshape to the same dict shape the rest of the template expects.
            people = []
            for m in local_rows:
                first, _, last = (m.full_name or "").partition(" ")
                people.append({
                    "id": m.external_member_id or str(m.id),
                    "first_name": first or m.full_name,
                    "last_name": last,
                    "phone": m.phone,
                    "email": m.email,
                    "kingdom_gateway_status": "NOT_STARTED",
                    "kingdom_gateway_completed_at": None,
                    "_bucket": "interested",
                    "_status_label": "Interested",
                    "_status_colour": "violet",
                })
            people_meta = {
                "total": total,
                "page": page, "size": 25,
                "pages": (total + 24) // 25,
            }
            # Render and bail before the central-status code path.
            flash, had_flash = _consume_flash(request)
            response = templates.TemplateResponse(
                request, "kg/admin_dashboard.html",
                {
                    "tab": tab, "asm_id": asm_id, "assembly_name": assembly_name,
                    "stats": stats, "people": people, "people_meta": people_meta,
                    "people_status_filter": people_status_filter,
                    "search": search, "page": page,
                    "results": [], "results_meta": {},
                    "selected_cycle_id": "", "cycles_for_filter": [],
                    "flash": flash,
                },
            )
            if had_flash:
                _clear_flash(response)
            return response

        # Translate the dashboard bucket → list of central statuses.
        bucket_to_statuses = {
            "done": ["COMPLETED", "EXEMPTED"],
            "active": ["INVITED", "ENROLLED", "EXAM_READY"],
            "notyet": ["NOT_STARTED", "WITHDRAWN"],
            "failed": ["FAILED"],
        }
        # rfm-database's /members endpoint accepts a single status filter
        # at a time. When the user picks a bucket that maps to multiple
        # central statuses we fall back to listing all members and
        # filtering client-side. For "All" we just list paginated.
        statuses = bucket_to_statuses.get(people_status_filter)
        if statuses and len(statuses) == 1:
            r = rfm._request(
                "GET", "/api/v1/members", db=db,
                params={
                    "assembly_id": asm_id,
                    "kingdom_gateway_status": statuses[0],
                    "search": search or None,
                    "page": page, "size": 25,
                },
            )
        else:
            r = rfm.search_members(
                search=search or None, assembly_id=asm_id,
                page=page, size=25, db=db,
            )
        if r.ok:
            if isinstance(r.data, list):
                people = r.data
            elif isinstance(r.data, dict):
                people = r.data.get("data") or []
                people_meta = r.data.get("meta") or {}

        # Multi-status buckets: filter client-side. Less efficient but
        # honest — the totals on the stat cards are still right because
        # we used the per-status counts above.
        if statuses and len(statuses) > 1:
            people = [m for m in people if (m.get("kingdom_gateway_status") or "NOT_STARTED") in statuses]

        # Decorate each row with the bucket + label + colour the template
        # uses for the pill, so the template stays readable.
        for m in people:
            bucket, label, colour = _bucket_for(m.get("kingdom_gateway_status"))
            m["_bucket"] = bucket
            m["_status_label"] = label
            m["_status_colour"] = colour

    elif tab == "results":
        selected_cycle_id = (request.query_params.get("cycle_id") or "").strip()
        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        # Cycle filter dropdown options
        cycles_r = kg.list_cycles(assembly_id=asm_id, db=db)
        if cycles_r.ok:
            data = cycles_r.data
            if isinstance(data, list):
                cycles_for_filter = data
            elif isinstance(data, dict):
                cycles_for_filter = data.get("data") or []
        # Pull attempts
        attempts_r = kg.list_exam_attempts(
            assembly_id=asm_id,
            cycle_id=selected_cycle_id or None,
            page=page, size=50, db=db,
        )
        if attempts_r.ok and isinstance(attempts_r.data, list):
            results = attempts_r.data
        elif attempts_r.ok and isinstance(attempts_r.data, dict):
            # Some envelopes come pre-unwrapped to data, others as full envelope.
            results = attempts_r.data.get("data") or []
            results_meta = attempts_r.data.get("meta") or {}

        # Decorate results with member names + cycle names for display —
        # one lookup each, cached in dicts so we don't hammer rfm-db.
        member_names: dict[str, str] = {}
        cycle_names: dict[str, str] = {c["id"]: c.get("name") for c in cycles_for_filter}
        for row in results:
            ext_id = row.get("external_member_id")
            if ext_id and ext_id not in member_names:
                mr = rfm.get_member(ext_id, db=db)
                if mr.ok and isinstance(mr.data, dict):
                    member_names[ext_id] = f"{mr.data.get('first_name', '')} {mr.data.get('last_name', '')}".strip()
                else:
                    member_names[ext_id] = ext_id[:8] + "…"
            row["_member_name"] = member_names.get(ext_id, "")
            row["_cycle_name"] = cycle_names.get(row.get("cycle_id"), "")

    return templates.TemplateResponse(
        request, "kg/admin_dashboard.html",
        {
            "tab": tab,
            "asm_id": asm_id,
            "assembly_name": assembly_name,
            "stats": stats,
            "people": people,
            "people_meta": people_meta,
            "people_status_filter": people_status_filter,
            "search": search,
            "page": page,
            "results": results,
            "results_meta": results_meta,
            "selected_cycle_id": selected_cycle_id,
            "cycles_for_filter": cycles_for_filter,
        },
    )


@router.get("/admin/kg/members/{external_member_id}", response_class=HTMLResponse)
async def admin_kg_member_detail(
    external_member_id: str, request: Request, db: Session = Depends(get_db),
):
    """One member's KG history: enrollments, attempts, milestones,
    completion status. Reached from the dashboard people list."""
    redirect = _require_kg_manage(request, db)
    if redirect:
        return redirect

    asm_id, asm_err = _resolve_portal_assembly(request, db)
    if not asm_id:
        return _flash_redirect("/admin/kg", asm_err or "No assembly resolved.")

    import rfm_api_client as rfm
    mr = rfm.get_member(external_member_id, db=db)
    central = mr.data if mr.ok and isinstance(mr.data, dict) else None

    # Tenant guard — only show members in the admin's assembly.
    if central and central.get("assembly_id") != asm_id:
        return _flash_redirect("/admin/kg/dashboard", "Not in your assembly.")

    enrollments: list[dict] = []
    er = kg._request(
        "GET", "/api/v1/enrollments", db=db,
        params={"external_member_id": external_member_id},
    )
    if er.ok:
        enrollments = (
            er.data if isinstance(er.data, list)
            else (er.data or {}).get("data") or []
        )

    # All exam attempts for this member, across all cycles.
    attempts: list[dict] = []
    ar = kg.list_exam_attempts(
        assembly_id=asm_id, external_member_id=external_member_id,
        page=1, size=100, db=db,
    )
    if ar.ok:
        attempts = (
            ar.data if isinstance(ar.data, list)
            else (ar.data or {}).get("data") or []
        )

    # Cycle name lookup for the attempts table.
    cycle_names: dict[str, str] = {}
    for e in enrollments:
        cid = e.get("cycle_id")
        if cid:
            cr = kg.get_cycle(cid, db=db)
            if cr.ok and isinstance(cr.data, dict):
                cycle_names[cid] = cr.data.get("name", "")
    for a in attempts:
        a["_cycle_name"] = cycle_names.get(a.get("cycle_id"), "")

    return templates.TemplateResponse(
        request, "kg/admin_member_detail.html",
        {
            "central": central,
            "external_member_id": external_member_id,
            "enrollments": enrollments,
            "attempts": attempts,
            "status_bucket": _bucket_for(central.get("kingdom_gateway_status") if central else None),
        },
    )
