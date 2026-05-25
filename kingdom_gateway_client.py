"""Client for the kingdom-gateway service.

KG owns the new-members course (cycles, classes, attendance, exam,
certificates). The portal is its consumer surface for members + facilitators
— members take the exam in the portal, facilitators mark attendance from
their phone here. KG itself only has an internal admin UI.

Same resilient pattern as `rfm_api_client.py`:

  * Feature-flagged via KG_INTEGRATION_ENABLED. When disabled, every call
    short-circuits to a "disabled" result so the rest of the portal works
    without KG configured.
  * Stdlib HTTP (urllib) — no extra requirements.
  * Errors never crash the caller. Every function returns a well-typed
    `ApiResult` so a KG outage degrades gracefully into an empty card.
  * No global state besides config; safe to call from anywhere.

Configuration (Railway env vars — also fall back to Settings table):
    KG_API_URL                  e.g. https://kingdom-gateway.railway.app
    KG_API_KEY                  X-API-Key value with read+write scope
    KG_INTEGRATION_ENABLED      "true" / "false" (default false until ready)
    KG_API_TIMEOUT_SECONDS      HTTP timeout per request (default 8s)

KG's response envelope mirrors rfm-database:
    { "data": <payload>, "meta": {...} }
We unwrap that automatically so callers see the bare payload.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


DEFAULT_TIMEOUT = 8


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _from_settings(db, key: str) -> Optional[str]:
    """Read a fallback value from the portal's Settings table."""
    if db is None:
        return None
    try:
        from models import Settings  # local import to avoid circulars at import time
        row = db.query(Settings).filter(Settings.key == key).first()
        return row.value if row else None
    except Exception:
        return None


def get_api_url(db=None) -> str:
    return (os.getenv("KG_API_URL") or _from_settings(db, "kg_api_url") or "").rstrip("/")


def get_api_key(db=None) -> str:
    return os.getenv("KG_API_KEY") or _from_settings(db, "kg_api_key") or ""


def is_enabled(db=None) -> bool:
    """The kill switch. False by default — flip to true once a KG service
    URL + key are wired up. Setting back to false at any time reverts every
    KG-driven behaviour to a graceful "not available" state."""
    val = os.getenv("KG_INTEGRATION_ENABLED")
    if val is None:
        val = _from_settings(db, "kg_integration_enabled")
    return (val or "").strip().lower() == "true"


def get_timeout(db=None) -> int:
    raw = os.getenv("KG_API_TIMEOUT_SECONDS") or _from_settings(db, "kg_api_timeout_seconds")
    try:
        return int(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT


def is_configured(db=None) -> bool:
    """True when URL + key are both present. Independent of `is_enabled`
    so admin diagnostics can show "configured but disabled"."""
    return bool(get_api_url(db)) and bool(get_api_key(db))


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ApiResult:
    """Wraps every response so callers don't have to handle exceptions.
    `ok` means the call succeeded; `data` holds the unwrapped payload."""
    ok: bool
    data: Any = None
    status: int = 0
    error: Optional[str] = None
    disabled: bool = False  # True when the kill switch is off

    @classmethod
    def disabled_result(cls) -> "ApiResult":
        return cls(ok=False, disabled=True, error="kg integration disabled")

    @classmethod
    def err(cls, msg: str, status: int = 0) -> "ApiResult":
        return cls(ok=False, error=msg, status=status)


# ---------------------------------------------------------------------------
# Low-level HTTP
# ---------------------------------------------------------------------------

def _request(
    method: str,
    path: str,
    *,
    db=None,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
) -> ApiResult:
    if not is_enabled(db):
        return ApiResult.disabled_result()
    url = get_api_url(db)
    key = get_api_key(db)
    if not url or not key:
        return ApiResult.err("kg API not configured (KG_API_URL / KG_API_KEY missing)")

    full = f"{url}{path}"
    if params:
        cleaned = {k: v for k, v in params.items() if v is not None}
        if cleaned:
            full = f"{full}?{urllib.parse.urlencode(cleaned, doseq=True)}"

    data_bytes = None
    headers = {
        "X-API-Key": key,
        "Accept": "application/json",
        "User-Agent": "RFM-Stellenbosch-Portal/1.0 (kg-client)",
    }
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(full, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=get_timeout(db)) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        status = e.code
    except urllib.error.URLError as e:
        return ApiResult.err(f"network error: {e.reason}")
    except Exception as e:
        return ApiResult.err(f"request failed: {e}")

    payload = None
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None

    if 200 <= status < 300:
        if isinstance(payload, dict) and "data" in payload:
            return ApiResult(ok=True, data=payload["data"], status=status)
        return ApiResult(ok=True, data=payload, status=status)

    err_msg = None
    if isinstance(payload, dict):
        err = payload.get("error") or {}
        err_msg = err.get("message") or payload.get("detail") or json.dumps(payload)[:300]
    return ApiResult.err(err_msg or f"HTTP {status}", status=status)


# ---------------------------------------------------------------------------
# Public surface — only what the portal actually calls
# ---------------------------------------------------------------------------

def health_check(db=None) -> ApiResult:
    """Lightweight ping (admin diagnostics)."""
    return _request("GET", "/health", db=db)


# ---- Member-facing reads ----

def list_my_enrollments(*, external_member_id: str, db=None) -> ApiResult:
    """All cycles the member is invited to / enrolled in. The portal home
    uses this to decide whether to show the KG card."""
    return _request(
        "GET", "/api/v1/enrollments",
        db=db,
        params={"external_member_id": external_member_id},
    )


def get_cycle(cycle_id: str, *, db=None) -> ApiResult:
    return _request("GET", f"/api/v1/cycles/{cycle_id}", db=db)


def list_classes(cycle_id: str, *, db=None) -> ApiResult:
    """Class schedule for a cycle (ordered by sequence)."""
    return _request("GET", "/api/v1/classes", db=db, params={"cycle_id": cycle_id})


def list_exams(*, course_id: str | None = None, exam_type: str | None = None, db=None) -> ApiResult:
    return _request(
        "GET", "/api/v1/exams",
        db=db,
        params={"course_id": course_id, "exam_type": exam_type},
    )


def get_exam(exam_id: str, *, db=None) -> ApiResult:
    return _request("GET", f"/api/v1/exams/{exam_id}", db=db)


# ---- Invites ----

def accept_invite(token: str, *, db=None) -> ApiResult:
    """Member clicked their invite link — confirm to KG so the enrollment
    flips to ENROLLED."""
    return _request("POST", "/api/v1/invites/accept", db=db, body={"token": token})


# ---- Exam taking ----

def start_attempt(
    *, exam_id: str, external_member_id: str, mode: str = "ONLINE", db=None,
) -> ApiResult:
    """Start a new exam attempt. Returns the attempt id, time limit, and
    the (ordered) public-shape questions to render in the portal."""
    return _request(
        "POST", "/api/v1/exam-attempts/start",
        db=db,
        body={
            "exam_id": exam_id,
            "external_member_id": external_member_id,
            "mode": mode,
        },
    )


def submit_attempt(*, attempt_id: str, answers: list, db=None) -> ApiResult:
    """Submit answers; KG auto-grades MCQ / T/F / fill-in-the-blank and
    returns the score + pass/fail. Short answers may flag
    `requires_manual_grading=true`."""
    return _request(
        "POST", "/api/v1/exam-attempts/submit",
        db=db,
        body={"attempt_id": attempt_id, "answers": answers},
    )


# ---- Attendance (facilitator surface) ----

def list_enrollments_for_cycle(cycle_id: str, *, db=None) -> ApiResult:
    return _request(
        "GET", "/api/v1/enrollments", db=db, params={"cycle_id": cycle_id},
    )


def mark_attendance(
    *,
    class_session_id: str,
    enrollment_id: str,
    status: str,                                # PRESENT / LATE / ABSENT / EXCUSED
    method: str = "MANUAL",                     # MANUAL / QR / SELF
    recorded_by_external_member_id: str | None = None,
    notes: str | None = None,
    db=None,
) -> ApiResult:
    return _request(
        "POST", "/api/v1/attendance/mark",
        db=db,
        body={
            "class_session_id": class_session_id,
            "enrollment_id": enrollment_id,
            "status": status,
            "method": method,
            "recorded_by_external_member_id": recorded_by_external_member_id,
            "notes": notes,
        },
    )


def bulk_mark_attendance(
    *,
    class_session_id: str,
    entries: list[dict],                         # each: {enrollment_id, status, ...}
    db=None,
) -> ApiResult:
    """One round trip — preferred over calling mark_attendance in a loop."""
    return _request(
        "POST", "/api/v1/attendance/bulk",
        db=db,
        body={"class_session_id": class_session_id, "entries": entries},
    )


def list_attendance_by_enrollment(enrollment_id: str, *, db=None) -> ApiResult:
    """Per-enrollment attendance history — used to draw ✓/late/✗ chips
    next to each class on the member's own cycle page, and to fill the
    facilitator's member-detail view."""
    return _request(
        "GET", f"/api/v1/attendance/by-enrollment/{enrollment_id}", db=db,
    )


# ---- Onsite exam marks ----

def record_onsite_mark(
    *,
    enrollment_id: str,
    exam_id: str,
    raw_score: int,
    max_score: int,
    grader_notes: str | None = None,
    db=None,
) -> ApiResult:
    """Record a paper-exam mark. KG auto-computes pass/fail using the
    cycle's pass mark + triggers completion evaluation."""
    return _request(
        "POST", "/api/v1/exam-attempts/onsite",
        db=db,
        body={
            "enrollment_id": enrollment_id,
            "exam_id": exam_id,
            "raw_score": raw_score,
            "max_score": max_score,
            "grader_notes": grader_notes,
        },
    )


# ---- Milestones ----

def list_courses(*, db=None) -> ApiResult:
    """KG's course catalog (typically just "Kingdom Gateway")."""
    return _request("GET", "/api/v1/courses", db=db)


def list_modules(course_id: str, *, db=None) -> ApiResult:
    """Topics from the book, in book order. Used by the cycle-creation
    form to render the checkboxes the admin picks from."""
    return _request("GET", f"/api/v1/courses/{course_id}/modules", db=db)


# ---- Class sessions (edit / add / remove) ----

def update_class_session(class_id: str, fields: dict, *, db=None) -> ApiResult:
    """PATCH a class session — title, dates, venue, facilitator, module."""
    return _request("PATCH", f"/api/v1/classes/{class_id}", db=db, body=fields)


def create_class_session(payload: dict, *, db=None) -> ApiResult:
    """Schedule a new class within an existing cycle."""
    return _request("POST", "/api/v1/classes", db=db, body=payload)


def delete_class_session(class_id: str, *, db=None) -> ApiResult:
    return _request("DELETE", f"/api/v1/classes/{class_id}", db=db)


def list_class_session_milestones(class_session_id: str, *, db=None) -> ApiResult:
    return _request(
        "GET", f"/api/v1/milestones/class-sessions/{class_session_id}", db=db,
    )


def replace_class_session_milestones(
    class_session_id: str, milestone_ids: list[str], *, db=None,
) -> ApiResult:
    return _request(
        "PUT", f"/api/v1/milestones/class-sessions/{class_session_id}",
        db=db, body={"milestone_ids": milestone_ids},
    )


def list_kg_milestones(*, db=None) -> ApiResult:
    """Full milestone catalog from KG — used by the cycle-creation form."""
    return _request("GET", "/api/v1/milestones", db=db)


def list_exam_attempts(
    *,
    assembly_id: str | None = None,
    cycle_id: str | None = None,
    external_member_id: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    page: int = 1,
    size: int = 50,
    db=None,
) -> ApiResult:
    """List exam attempts across cycles. Returns the paginated envelope
    the KG endpoint produces — each row carries enrollment + member info
    so the portal can render the dashboard results table directly."""
    return _request(
        "GET", "/api/v1/exam-attempts",
        db=db,
        params={
            "assembly_id": assembly_id,
            "cycle_id": cycle_id,
            "external_member_id": external_member_id,
            "status": status,
            "mode": mode,
            "page": page,
            "size": size,
        },
    )


def list_cycles(*, assembly_id: str | None = None, status: str | None = None, db=None) -> ApiResult:
    """All cycles in the assembly (any status, any age) — used by the
    dashboard filter dropdown."""
    return _request(
        "GET", "/api/v1/cycles",
        db=db,
        params={"assembly_id": assembly_id, "status": status, "size": 100},
    )


def accept_enrollment_direct(enrollment_id: str, *, db=None) -> ApiResult:
    """Flip an enrollment from INVITED to ENROLLED without needing a
    token. Used when:
      * the member accepts from inside the portal (portal verified
        their identity before calling), or
      * the KG team manually marks an enrollment as accepted (for
        members invited verbally / without email).
    Idempotent — calling on an already-ENROLLED enrollment is a no-op.
    """
    return _request(
        "POST", f"/api/v1/invites/accept-direct/{enrollment_id}", db=db,
    )


def bulk_invite(
    *, cycle_id: str, members: list[dict], db=None,
) -> ApiResult:
    """Invite many members to a cycle in one call.

    ``members`` is a list of ``{"external_member_id": "...", "email": "..."}``
    dicts. KG creates an Enrollment + Invite row per member and dispatches
    the invite email via rfm-notify. The plaintext invite token is
    returned per row — the portal doesn't need to do anything with it
    (KG already baked it into the email's accept link), but it's
    available for diagnostics.
    """
    return _request(
        "POST", "/api/v1/invites/bulk", db=db,
        body={"cycle_id": cycle_id, "members": members},
    )


def plan_cycle(*, payload: dict, db=None) -> ApiResult:
    """Create a cycle with class sessions + milestones auto-generated.
    Posts to KG's POST /api/v1/cycles/plan — the form-style endpoint
    that takes module_ids[] + milestones[] in one payload."""
    return _request("POST", "/api/v1/cycles/plan", db=db, body=payload)


def list_cycle_milestones(cycle_id: str, *, db=None) -> ApiResult:
    """The milestones attached to this cycle (with mandatory flag)."""
    return _request("GET", f"/api/v1/milestones/cycles/{cycle_id}", db=db)


def list_enrollment_milestones(enrollment_id: str, *, db=None) -> ApiResult:
    """Which milestones the member has achieved already."""
    return _request("GET", f"/api/v1/milestones/enrollments/{enrollment_id}", db=db)


def mark_milestone_achieved(
    *,
    enrollment_id: str,
    milestone_id: str,
    achieved_at: str | None = None,
    notes: str | None = None,
    recorded_by_external_member_id: str | None = None,
    db=None,
) -> ApiResult:
    return _request(
        "POST",
        f"/api/v1/milestones/enrollments/{enrollment_id}/{milestone_id}/achieve",
        db=db,
        body={
            "achieved_at": achieved_at,
            "notes": notes,
            "recorded_by_external_member_id": recorded_by_external_member_id,
        },
    )


def revoke_milestone(
    *, enrollment_id: str, milestone_id: str, db=None,
) -> ApiResult:
    return _request(
        "DELETE",
        f"/api/v1/milestones/enrollments/{enrollment_id}/{milestone_id}",
        db=db,
    )


# ---- Certificate ----

def get_certificate_meta(enrollment_id: str, *, db=None) -> ApiResult:
    return _request(
        "GET", f"/api/v1/certificates/by-enrollment/{enrollment_id}", db=db,
    )


def get_certificate_pdf_bytes(enrollment_id: str, *, db=None) -> tuple[bool, bytes | None, str | None]:
    """Stream the certificate PDF. Returns ``(ok, body_bytes, error)``.
    Bypasses the standard `_request` JSON helper because this returns
    application/pdf, not JSON."""
    if not is_enabled(db):
        return False, None, "kg integration disabled"
    url = get_api_url(db)
    key = get_api_key(db)
    if not url or not key:
        return False, None, "kg API not configured"

    full = f"{url}/api/v1/certificates/by-enrollment/{enrollment_id}/pdf"
    req = urllib.request.Request(
        full,
        headers={"X-API-Key": key, "Accept": "application/pdf",
                 "User-Agent": "RFM-Stellenbosch-Portal/1.0 (kg-client)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=get_timeout(db)) as resp:
            return True, resp.read(), None
    except urllib.error.HTTPError as e:
        return False, None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, None, f"network error: {e.reason}"
    except Exception as e:  # pragma: no cover — defensive
        return False, None, f"request failed: {e}"


# ---------------------------------------------------------------------------
# Convenience helpers used by routes
# ---------------------------------------------------------------------------

def best_attempt_summary(attempts: list[dict]) -> dict | None:
    """Pick the highest-scoring fully-graded attempt for display on the
    member portal. Returns None if the member has no graded attempts yet."""
    graded = [a for a in (attempts or []) if a.get("status") == "GRADED"]
    if not graded:
        return None
    graded.sort(key=lambda a: (a.get("percent") or 0), reverse=True)
    return graded[0]
