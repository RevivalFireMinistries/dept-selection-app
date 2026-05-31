"""
Display Submission API routes for FirePresenter integration.

Endpoints:
  POST /api/display/submit          — Submit content for display (with optional file upload)
  GET  /api/display/submissions     — List submissions (filtered by date, status)
  GET  /api/display/fetch           — FirePresenter polls this: returns approved items, then deletes them
  PUT  /api/display/{id}/approve    — Approve a submission
  PUT  /api/display/{id}/reject     — Reject a submission
  DELETE /api/display/{id}          — Delete a submission
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional
from datetime import date, datetime
import os
import re
import uuid
import json

from database import get_db
from models import DisplaySubmission


# ─── Title quality validation ──────────────────────────────────────────
#
# Submitters on phones often paste the photo's filename into the title
# field — and phone-camera filenames are usually UUIDs or hex blobs that
# tell the FirePresenter operator nothing useful. We enforce a basic
# "is this human-readable" check at the submission boundary so the
# garbage never lands in the schedule downstream.

_UUID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}(\.\w+)?$",
    re.IGNORECASE,
)
_LONG_HEX_RE = re.compile(r"[a-f0-9]{12,}", re.IGNORECASE)
_MEDIA_EXT_RE = re.compile(
    r"\.(jpe?g|png|webp|gif|bmp|mp4|webm|mov|avi|mkv|pptx?)$",
    re.IGNORECASE,
)


def _title_quality_error(title: str, file_name: str | None) -> str | None:
    """Return a user-friendly error string if the title is unfit for
    display, else None. Keep the checks tight and the error messages
    actionable — submitters fix what they understand."""
    t = (title or "").strip()
    if not t:
        return "Please give your submission a short title so the media team knows what it is."
    if len(t) < 3:
        return "Title is too short — describe what this is in a few words."
    if _UUID_RE.match(t):
        return (
            "That looks like the filename of your photo, not a title. "
            "Please describe what it is — e.g. \"Youth camp poster\", \"Praise team welcome\"."
        )
    # Title is exactly the uploaded file's name, OR a name-with-extension
    # variant of it. Same root cause as UUID — submitter pasted the file
    # name instead of describing the content.
    if file_name:
        fn = file_name.strip()
        if t == fn or t == os.path.splitext(fn)[0]:
            return (
                "Your title is the same as the uploaded filename. "
                "Please describe what the content is — e.g. \"Membership class poster\"."
            )
    # A title that's mostly a long hex blob with an optional extension —
    # catches camera roll names that aren't strict UUIDs but still
    # tell the operator nothing.
    bare = _MEDIA_EXT_RE.sub("", t)
    if _LONG_HEX_RE.search(bare) and len(re.sub(r"[a-f0-9.\-_\s]", "", bare, flags=re.IGNORECASE)) < 3:
        return (
            "That title looks like a generated filename. "
            "Please write a short description so the media team can label it."
        )
    return None

router = APIRouter()

# Upload directory for submitted files.
#
# In production this MUST point at a persistent volume — Railway / Fly / etc.
# containers are ephemeral, so anything written to the in-container filesystem
# at runtime is wiped on the next redeploy. DB rows persist (Postgres), but
# the files they reference don't, which is why FirePresenter was getting
# 404s for every submission older than the last deploy.
#
# Set the env var `DISPLAY_UPLOAD_DIR` to the mount path of a Railway volume
# (e.g. `/data/display_uploads`). The default is the in-repo `static/...`
# location, which is fine for local development.
UPLOAD_DIR = os.environ.get("DISPLAY_UPLOAD_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "static", "display_uploads"
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ─── Cross-instance fetch tracking ─────────────────────────────────────
#
# In-memory map of submission_id → {instance_id: last_fetched_at}.
# Lost on server restart, which is fine: the data is purely an
# at-a-glance "which campuses have polled this poster" courtesy badge
# for operators. The contract docs even acknowledge "no auth, log only"
# for this data.
#
# Why an in-memory dict over a SQL table:
#   - Submissions only live 14 days and we cap each instance's row at
#     1 timestamp, so total entries are bounded by submissions × active
#     instances. Tiny. Doesn't earn a migration.
#   - Restart loss is acceptable: the next poll from any instance
#     repopulates its own row within minutes.
#   - Zero migration risk for a feature that's a UX courtesy, not a
#     correctness requirement.
#
# Prune any entry older than 24h on each fetch to keep growth bounded
# under pathological reconnect-loop scenarios.
from datetime import timedelta as _td
from threading import Lock as _Lock

_recent_fetches: dict[int, dict[str, datetime]] = {}
_recent_fetches_lock = _Lock()

def _record_instance_fetch(submission_ids: list[int], instance_id: str) -> None:
    """Stamp `instance_id` against each submission as having been polled.
    No-op when instance_id is the placeholder 'unknown' (older clients)."""
    if not instance_id or instance_id == "unknown":
        return
    now = datetime.now()
    cutoff = now - _td(hours=24)
    with _recent_fetches_lock:
        # Walk a snapshot of keys so we can mutate the dict while iterating.
        for sid in list(_recent_fetches.keys()):
            insts = _recent_fetches[sid]
            for iid in list(insts.keys()):
                if insts[iid] < cutoff:
                    del insts[iid]
            if not insts:
                del _recent_fetches[sid]
        for sid in submission_ids:
            _recent_fetches.setdefault(sid, {})[instance_id] = now

def _fetches_for(sid: int) -> list[dict]:
    """Return the list of {instance, at} entries for a submission, sorted
    most-recent first. Always returns a list (possibly empty)."""
    with _recent_fetches_lock:
        m = _recent_fetches.get(sid) or {}
    rows = [{"instance": iid, "at": at.isoformat()} for iid, at in m.items()]
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


def submission_to_dict(sub: DisplaySubmission) -> dict:
    return {
        "id": sub.id,
        "submitter_name": sub.submitter_name,
        "submitter_dept": sub.submitter_dept,
        "submitter_phone": sub.submitter_phone,
        "content_type": sub.content_type,
        "title": sub.title,
        "body": sub.body,
        "subtitle": sub.subtitle,
        "comments": sub.comments,
        "file_name": sub.file_name,
        "file_path": sub.file_path,
        "file_size": sub.file_size,
        "file_url": f"/static/display_uploads/{os.path.basename(sub.file_path)}" if sub.file_path else None,
        "service_date": str(sub.service_date) if sub.service_date else None,
        "display_slot": sub.display_slot,
        "display_duration": sub.display_duration,
        "display_order": sub.display_order,
        "status": sub.status,
        "reviewed_by": sub.reviewed_by,
        "review_note": sub.review_note,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


# ─── Submit content for display ─────────────────────────────────────────

@router.post("/submit")
async def submit_display_content(
    submitter_name: str = Form(...),
    content_type: str = Form(...),
    title: str = Form(...),
    service_date: str = Form(...),
    display_slot: str = Form("announcements"),
    submitter_dept: Optional[str] = Form(None),
    submitter_phone: Optional[str] = Form(None),
    body: Optional[str] = Form(None),
    subtitle: Optional[str] = Form(None),
    comments: Optional[str] = Form(None),
    display_duration: Optional[int] = Form(None),
    display_order: Optional[int] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Submit content for display on FirePresenter."""

    # Validate content type
    valid_types = ["sermon_info", "scripture", "nugget", "image", "video", "powerpoint", "announcement"]
    if content_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid content_type. Must be one of: {valid_types}")

    # Validate display slot
    valid_slots = ["before_service", "during_worship", "announcements", "during_sermon", "after_service", "any"]
    if display_slot not in valid_slots:
        raise HTTPException(status_code=400, detail=f"Invalid display_slot. Must be one of: {valid_slots}")

    # Validate the title's readability. We check this BEFORE writing the
    # uploaded file to disk so a rejected submission doesn't leave
    # orphaned files behind. The file-name comparison uses the
    # uploaded file's original name (file.filename) if there is one.
    incoming_file_name = file.filename if (file and file.filename) else None
    title_err = _title_quality_error(title, incoming_file_name)
    if title_err:
        raise HTTPException(status_code=400, detail=title_err)

    # Handle file upload
    file_path = None
    file_name = None
    file_size = None

    if file and file.filename:
        ext = os.path.splitext(file.filename)[1].lower()
        allowed_exts = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.mp4', '.webm', '.mov', '.avi', '.mkv', '.pptx']
        if ext not in allowed_exts:
            raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")

        # Save with unique name
        unique_name = f"{uuid.uuid4().hex}{ext}"
        dest_path = os.path.join(UPLOAD_DIR, unique_name)
        content = await file.read()
        with open(dest_path, "wb") as f:
            f.write(content)

        file_path = dest_path
        file_name = file.filename
        file_size = len(content)

    # Parse service date
    try:
        svc_date = date.fromisoformat(service_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid service_date format. Use YYYY-MM-DD")

    submission = DisplaySubmission(
        submitter_name=submitter_name,
        submitter_dept=submitter_dept,
        submitter_phone=submitter_phone,
        content_type=content_type,
        title=title,
        body=body,
        subtitle=subtitle,
        comments=comments,
        file_path=file_path,
        file_name=file_name,
        file_size=file_size,
        service_date=svc_date,
        display_slot=display_slot,
        display_duration=display_duration,
        display_order=display_order,
        status="approved",  # Auto-approved — no review needed
    )

    db.add(submission)
    db.commit()
    db.refresh(submission)

    return {"status": "success", "id": submission.id, "message": "Submission received. It will be reviewed by the media team."}


# ─── List submissions (for admin review) ────────────────────────────────

@router.get("/submissions")
def list_submissions(
    status: Optional[str] = Query(None),
    service_date: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List display submissions with optional filters."""
    query = db.query(DisplaySubmission).order_by(desc(DisplaySubmission.created_at))

    if status:
        query = query.filter(DisplaySubmission.status == status)
    if service_date:
        try:
            d = date.fromisoformat(service_date)
            query = query.filter(DisplaySubmission.service_date == d)
        except ValueError:
            pass
    if content_type:
        query = query.filter(DisplaySubmission.content_type == content_type)

    return [submission_to_dict(s) for s in query.all()]


# ─── FirePresenter fetch endpoint — returns approved items then deletes ──

@router.get("/fetch")
def fetch_for_presenter(
    request: Request,
    service_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    FirePresenter polls this endpoint.
    Returns all approved, unfetched submissions for the given date (or today).
    After returning, marks them as fetched and deletes them.
    """
    target_date = date.today()
    if service_date:
        try:
            target_date = date.fromisoformat(service_date)
        except ValueError:
            pass

    # Each FirePresenter install sends a stable per-install ID. Log it so
    # an admin can answer "which FirePresenters polled the server today,
    # and did they all see the latest submission?" without crawling
    # individual logs. We don't fail the request if the header is missing
    # — older FirePresenter builds (<= v2.3.0) won't send it.
    instance = (request.headers.get("X-FirePresenter-Instance") or "unknown").strip()[:80]

    # Return ALL approved submissions for the target date — no fetched
    # filter, no fetched=True write. Multiple FirePresenter instances may
    # each want their own copy of the same submission (e.g. a poster
    # used at the main church AND a remote campus), so the server can't
    # treat a single fetch as "consumed by all clients". Each instance
    # tracks which submissions it has already seen client-side via
    # localStorage and only pops the new-content notification for IDs
    # it hasn't processed before.
    #
    # The `fetched` column is left in place for future use / audit but
    # no longer affects this query.
    submissions = db.query(DisplaySubmission).filter(
        DisplaySubmission.status == "approved",
        DisplaySubmission.service_date == target_date,
    ).order_by(
        DisplaySubmission.display_slot,
        DisplaySubmission.display_order.nullslast(),
        DisplaySubmission.created_at,
    ).all()

    if not submissions:
        print(f"[display.fetch] instance={instance} date={target_date.isoformat()} count=0")
        return {"items": [], "count": 0}

    # Stamp this instance against every returned submission BEFORE building
    # the response — that way the requester also sees its own poll
    # reflected (and clients can filter self vs. other in the UI).
    _record_instance_fetch([s.id for s in submissions], instance)

    print(f"[display.fetch] instance={instance} date={target_date.isoformat()} count={len(submissions)}")
    items: list[dict] = []
    for s in submissions:
        d = submission_to_dict(s)
        # fetched_by: cross-instance courtesy badge. Each entry is
        # { "instance": "<X-FirePresenter-Instance>", "at": "<ISO>" }.
        # Sorted most-recent first. Empty list when no instance with a
        # header has polled this submission yet (or only "unknown"
        # legacy clients, which we don't track).
        d["fetched_by"] = _fetches_for(s.id)
        items.append(d)
    return {"items": items, "count": len(items)}


# ─── Approve / Reject ───────────────────────────────────────────────────

@router.put("/{submission_id}/approve")
def approve_submission(
    submission_id: int,
    reviewed_by: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    sub = db.query(DisplaySubmission).filter(DisplaySubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    sub.status = "approved"
    sub.reviewed_by = reviewed_by or "admin"
    db.commit()
    return {"status": "approved", "id": sub.id}


@router.put("/{submission_id}/reject")
def reject_submission(
    submission_id: int,
    review_note: Optional[str] = Query(None),
    reviewed_by: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    sub = db.query(DisplaySubmission).filter(DisplaySubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    sub.status = "rejected"
    sub.reviewed_by = reviewed_by or "admin"
    sub.review_note = review_note
    db.commit()
    return {"status": "rejected", "id": sub.id}


# ─── Delete ─────────────────────────────────────────────────────────────

@router.delete("/{submission_id}")
def delete_submission(
    submission_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a submission. Authorised when:
      - the caller is the submitter (logged-in member whose full_name
        matches submitter_name), or
      - the caller has an admin session.
    Anonymous deletes are refused — this used to be wide-open."""
    sub = db.query(DisplaySubmission).filter(DisplaySubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Resolve who is calling — admin or member
    from routers.pages import is_authenticated, get_current_member
    is_admin = is_authenticated(request)
    actor = None if is_admin else get_current_member(request, db)

    if not is_admin:
        if not actor:
            raise HTTPException(status_code=401, detail="Please log in")
        # Loose name match (case-insensitive, whitespace-collapsed) so
        # display-name variants like 'Pastor Russel Mupfumira' vs
        # 'Russel Mupfumira' all resolve to the same human.
        def _norm(s: str) -> str:
            return " ".join((s or "").lower().split())
        if _norm(actor.full_name) not in _norm(sub.submitter_name) and \
           _norm(sub.submitter_name) not in _norm(actor.full_name):
            raise HTTPException(status_code=403, detail="You can only delete your own submissions")

    if sub.file_path and os.path.exists(sub.file_path):
        try:
            os.remove(sub.file_path)
        except Exception:
            pass

    db.delete(sub)
    db.commit()
    return {"status": "deleted", "id": submission_id}
