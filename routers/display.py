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

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
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

    # Get approved items that haven't been fetched yet
    submissions = db.query(DisplaySubmission).filter(
        DisplaySubmission.status == "approved",
        DisplaySubmission.fetched == False,
        DisplaySubmission.service_date == target_date,
    ).order_by(
        DisplaySubmission.display_slot,
        DisplaySubmission.display_order.nullslast(),
        DisplaySubmission.created_at,
    ).all()

    if not submissions:
        return {"items": [], "count": 0}

    result = [submission_to_dict(s) for s in submissions]

    # Mark as fetched — don't delete the file or the DB row here.
    #
    # The previous code deleted both atomically with the fetch response,
    # which meant FirePresenter received a URL pointing at a file the
    # server had already removed by the time the request finished. The
    # in-popup thumbnail and the main-process local-cache fetch both hit
    # 404s because of this.
    #
    # The `fetched == False` filter at the top of this handler is the
    # idempotency mechanism — flipping the flag is enough to stop the
    # same submission being returned again. Disk space is managed by a
    # scheduled cleanup elsewhere (see scheduler.py
    # cleanup_old_display_submissions) which removes fetched submissions
    # older than 14 days, giving any display client plenty of time to
    # download the asset.
    for s in submissions:
        s.fetched = True
    db.commit()

    return {"items": result, "count": len(result)}


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
    db: Session = Depends(get_db),
):
    sub = db.query(DisplaySubmission).filter(DisplaySubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    if sub.file_path and os.path.exists(sub.file_path):
        try:
            os.remove(sub.file_path)
        except:
            pass

    db.delete(sub)
    db.commit()
    return {"status": "deleted", "id": submission_id}
