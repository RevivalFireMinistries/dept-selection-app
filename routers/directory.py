from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, or_
from typing import List, Optional
from datetime import date
from database import get_db
from models import Member
from schemas import (
    MemberProfileUpdate, MemberProfileResponse,
    DirectoryMemberResponse, DirectoryResponse, BirthdayReportEntry
)
import os
import uuid

router = APIRouter(prefix="/api", tags=["directory"])

# Configure upload directory
UPLOAD_DIR = "static/uploads/photos"


# ============ MEMBER DIRECTORY ============

@router.get("/directory", response_model=DirectoryResponse)
def get_directory(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    """Get paginated member directory"""
    query = db.query(Member)

    if active_only:
        query = query.filter(or_(Member.is_active == True, Member.is_active.is_(None)))

    total = query.count()
    offset = (page - 1) * page_size

    members = query.order_by(Member.full_name).offset(offset).limit(page_size).all()

    return DirectoryResponse(
        members=[
            DirectoryMemberResponse(
                id=m.id,
                full_name=m.full_name,
                phone=m.phone,
                email=m.email,
                photo_url=m.photo_url,
                is_active=m.is_active if m.is_active is not None else True
            )
            for m in members
        ],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/directory/search", response_model=DirectoryResponse)
def search_directory(
    q: str = Query(min_length=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Search members by name, phone, or email"""
    search_term = f"%{q}%"

    query = db.query(Member).filter(
        or_(
            Member.full_name.ilike(search_term),
            Member.phone.ilike(search_term),
            Member.email.ilike(search_term)
        ),
        or_(Member.is_active == True, Member.is_active.is_(None))
    )

    total = query.count()
    offset = (page - 1) * page_size

    members = query.order_by(Member.full_name).offset(offset).limit(page_size).all()

    return DirectoryResponse(
        members=[
            DirectoryMemberResponse(
                id=m.id,
                full_name=m.full_name,
                phone=m.phone,
                email=m.email,
                photo_url=m.photo_url,
                is_active=m.is_active if m.is_active is not None else True
            )
            for m in members
        ],
        total=total,
        page=page,
        page_size=page_size
    )


# ============ MEMBER PROFILES ============

@router.get("/members/{member_id}/profile", response_model=MemberProfileResponse)
def get_member_profile(member_id: int, db: Session = Depends(get_db)):
    """Get full member profile"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    return MemberProfileResponse(
        id=member.id,
        full_name=member.full_name,
        phone=member.phone,
        email=member.email or "",
        address=member.address or "",
        photo_url=member.photo_url,
        birthday=member.birthday,
        anniversary=member.anniversary,
        gender=member.gender,
        marital_status=member.marital_status,
        occupation=member.occupation,
        emergency_contact_name=member.emergency_contact_name,
        emergency_contact_phone=member.emergency_contact_phone,
        member_since=member.member_since,
        is_active=member.is_active if member.is_active is not None else True,
        created_at=member.created_at,
        updated_at=member.updated_at
    )


@router.put("/members/{member_id}/profile", response_model=MemberProfileResponse)
def update_member_profile(
    member_id: int,
    profile_update: MemberProfileUpdate,
    db: Session = Depends(get_db)
):
    """Update member profile"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    update_data = profile_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(member, key, value)

    db.commit()
    db.refresh(member)

    return MemberProfileResponse(
        id=member.id,
        full_name=member.full_name,
        phone=member.phone,
        email=member.email or "",
        address=member.address or "",
        photo_url=member.photo_url,
        birthday=member.birthday,
        anniversary=member.anniversary,
        gender=member.gender,
        marital_status=member.marital_status,
        occupation=member.occupation,
        emergency_contact_name=member.emergency_contact_name,
        emergency_contact_phone=member.emergency_contact_phone,
        member_since=member.member_since,
        is_active=member.is_active if member.is_active is not None else True,
        created_at=member.created_at,
        updated_at=member.updated_at
    )


@router.post("/members/{member_id}/photo")
async def upload_member_photo(
    member_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Upload or update member photo"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Validate file type
    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}"
        )

    # Create upload directory if it doesn't exist
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Generate unique filename
    file_ext = file.filename.split(".")[-1] if file.filename else "jpg"
    new_filename = f"{member_id}_{uuid.uuid4().hex[:8]}.{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, new_filename)

    # Delete old photo if exists
    if member.photo_url:
        old_path = member.photo_url.lstrip("/")
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    # Save new photo
    try:
        contents = await file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Update member record
    member.photo_url = f"/{file_path}"
    db.commit()

    return {
        "message": "Photo uploaded successfully",
        "photo_url": member.photo_url
    }


@router.delete("/members/{member_id}/photo")
def delete_member_photo(member_id: int, db: Session = Depends(get_db)):
    """Delete member photo"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    if member.photo_url:
        old_path = member.photo_url.lstrip("/")
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

        member.photo_url = None
        db.commit()

    return {"message": "Photo deleted"}


# ============ REPORTS ============

@router.get("/reports/birthdays", response_model=List[BirthdayReportEntry])
def get_birthday_report(
    month: Optional[int] = Query(default=None, ge=1, le=12),
    db: Session = Depends(get_db)
):
    """Get members with birthdays, optionally filtered by month"""
    query = db.query(Member).filter(
        Member.birthday.isnot(None),
        or_(Member.is_active == True, Member.is_active.is_(None))
    )

    if month:
        query = query.filter(extract('month', Member.birthday) == month)

    # Order by day of month
    members = query.order_by(extract('day', Member.birthday)).all()

    return [
        BirthdayReportEntry(
            id=m.id,
            full_name=m.full_name,
            phone=m.phone,
            birthday=m.birthday,
            day_of_month=m.birthday.day
        )
        for m in members
    ]


@router.get("/reports/anniversaries")
def get_anniversary_report(
    month: Optional[int] = Query(default=None, ge=1, le=12),
    db: Session = Depends(get_db)
):
    """Get members with anniversaries, optionally filtered by month"""
    query = db.query(Member).filter(
        Member.anniversary.isnot(None),
        or_(Member.is_active == True, Member.is_active.is_(None))
    )

    if month:
        query = query.filter(extract('month', Member.anniversary) == month)

    members = query.order_by(extract('day', Member.anniversary)).all()

    return [
        {
            "id": m.id,
            "full_name": m.full_name,
            "phone": m.phone,
            "anniversary": m.anniversary.isoformat(),
            "day_of_month": m.anniversary.day
        }
        for m in members
    ]


@router.get("/reports/new-members")
def get_new_members_report(
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db)
):
    """Get members who joined within a date range"""
    query = db.query(Member).filter(
        Member.member_since.isnot(None),
        Member.member_since >= start_date,
        Member.member_since <= end_date,
        or_(Member.is_active == True, Member.is_active.is_(None))
    )

    members = query.order_by(Member.member_since.desc()).all()

    return {
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat()
        },
        "total": len(members),
        "members": [
            {
                "id": m.id,
                "full_name": m.full_name,
                "phone": m.phone,
                "email": m.email,
                "member_since": m.member_since.isoformat() if m.member_since else None
            }
            for m in members
        ]
    }


@router.get("/reports/membership-stats")
def get_membership_stats(db: Session = Depends(get_db)):
    """Get overall membership statistics"""
    total = db.query(func.count(Member.id)).scalar()

    active = db.query(func.count(Member.id)).filter(
        or_(Member.is_active == True, Member.is_active.is_(None))
    ).scalar()

    inactive = total - active

    # Gender breakdown
    male = db.query(func.count(Member.id)).filter(
        Member.gender == "male",
        or_(Member.is_active == True, Member.is_active.is_(None))
    ).scalar()

    female = db.query(func.count(Member.id)).filter(
        Member.gender == "female",
        or_(Member.is_active == True, Member.is_active.is_(None))
    ).scalar()

    # Marital status breakdown
    marital_stats = {}
    for status in ["single", "married", "widowed", "divorced"]:
        count = db.query(func.count(Member.id)).filter(
            Member.marital_status == status,
            or_(Member.is_active == True, Member.is_active.is_(None))
        ).scalar()
        marital_stats[status] = count

    # Birthday this month
    today = date.today()
    birthdays_this_month = db.query(func.count(Member.id)).filter(
        Member.birthday.isnot(None),
        extract('month', Member.birthday) == today.month,
        or_(Member.is_active == True, Member.is_active.is_(None))
    ).scalar()

    return {
        "total_members": total,
        "active_members": active,
        "inactive_members": inactive,
        "gender": {
            "male": male,
            "female": female,
            "unspecified": active - male - female
        },
        "marital_status": marital_stats,
        "birthdays_this_month": birthdays_this_month
    }
