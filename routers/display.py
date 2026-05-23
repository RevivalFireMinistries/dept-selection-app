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
import uuid
import json

from database import get_db
from models import DisplaySubmission

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

    print(f"[display.fetch] instance={instance} date={target_date.isoformat()} count={len(submissions)}")
    return {"items": [submission_to_dict(s) for s in submissions], "count": len(submissions)}


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
