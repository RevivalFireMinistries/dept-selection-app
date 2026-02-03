from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date, datetime
from database import get_db
from models import (
    CellGroup, CellGroupMembership, CellMeeting, CellMeetingAttendance, Member
)
from schemas import (
    CellGroupCreate, CellGroupUpdate, CellGroupResponse, CellGroupMemberBrief,
    CellMembershipCreate, CellMembershipUpdate, CellMembershipResponse,
    CellMeetingCreate, CellMeetingUpdate, CellMeetingResponse,
    CellMeetingAttendanceCreate, CellMeetingAttendanceResponse,
    CellLeaderGroupResponse
)

router = APIRouter(prefix="/api", tags=["cell-groups"])


# ============ CELL GROUPS ============

@router.get("/cell-groups", response_model=List[CellGroupResponse])
def get_cell_groups(
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    """Get all cell groups"""
    query = db.query(CellGroup)
    if active_only:
        query = query.filter(CellGroup.is_active == True)

    groups = query.order_by(CellGroup.name).all()

    result = []
    for group in groups:
        member_count = db.query(func.count(CellGroupMembership.id)).filter(
            CellGroupMembership.cell_group_id == group.id,
            CellGroupMembership.is_active == True
        ).scalar()

        response = CellGroupResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            meeting_day=group.meeting_day,
            meeting_time=group.meeting_time,
            meeting_location=group.meeting_location,
            is_active=group.is_active,
            created_at=group.created_at,
            leader=CellGroupMemberBrief(
                id=group.leader.id,
                full_name=group.leader.full_name,
                phone=group.leader.phone
            ) if group.leader else None,
            assistant_leader=CellGroupMemberBrief(
                id=group.assistant_leader.id,
                full_name=group.assistant_leader.full_name,
                phone=group.assistant_leader.phone
            ) if group.assistant_leader else None,
            member_count=member_count
        )
        result.append(response)

    return result


@router.post("/cell-groups", response_model=CellGroupResponse)
def create_cell_group(group: CellGroupCreate, db: Session = Depends(get_db)):
    """Create a new cell group"""
    # Verify leader exists if provided
    if group.leader_id:
        leader = db.query(Member).filter(Member.id == group.leader_id).first()
        if not leader:
            raise HTTPException(status_code=404, detail="Leader not found")

    if group.assistant_leader_id:
        assistant = db.query(Member).filter(Member.id == group.assistant_leader_id).first()
        if not assistant:
            raise HTTPException(status_code=404, detail="Assistant leader not found")

    db_group = CellGroup(**group.model_dump())
    db.add(db_group)
    db.commit()
    db.refresh(db_group)

    # Auto-add leader as member with leader role
    if group.leader_id:
        leader_membership = CellGroupMembership(
            cell_group_id=db_group.id,
            member_id=group.leader_id,
            role="leader"
        )
        db.add(leader_membership)

    if group.assistant_leader_id:
        assistant_membership = CellGroupMembership(
            cell_group_id=db_group.id,
            member_id=group.assistant_leader_id,
            role="assistant"
        )
        db.add(assistant_membership)

    db.commit()

    return CellGroupResponse(
        id=db_group.id,
        name=db_group.name,
        description=db_group.description,
        meeting_day=db_group.meeting_day,
        meeting_time=db_group.meeting_time,
        meeting_location=db_group.meeting_location,
        is_active=db_group.is_active,
        created_at=db_group.created_at,
        leader=CellGroupMemberBrief(
            id=db_group.leader.id,
            full_name=db_group.leader.full_name,
            phone=db_group.leader.phone
        ) if db_group.leader else None,
        assistant_leader=CellGroupMemberBrief(
            id=db_group.assistant_leader.id,
            full_name=db_group.assistant_leader.full_name,
            phone=db_group.assistant_leader.phone
        ) if db_group.assistant_leader else None,
        member_count=0
    )


@router.get("/cell-groups/{group_id}", response_model=CellGroupResponse)
def get_cell_group(group_id: int, db: Session = Depends(get_db)):
    """Get a specific cell group"""
    group = db.query(CellGroup).filter(CellGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Cell group not found")

    member_count = db.query(func.count(CellGroupMembership.id)).filter(
        CellGroupMembership.cell_group_id == group_id,
        CellGroupMembership.is_active == True
    ).scalar()

    return CellGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        meeting_day=group.meeting_day,
        meeting_time=group.meeting_time,
        meeting_location=group.meeting_location,
        is_active=group.is_active,
        created_at=group.created_at,
        leader=CellGroupMemberBrief(
            id=group.leader.id,
            full_name=group.leader.full_name,
            phone=group.leader.phone
        ) if group.leader else None,
        assistant_leader=CellGroupMemberBrief(
            id=group.assistant_leader.id,
            full_name=group.assistant_leader.full_name,
            phone=group.assistant_leader.phone
        ) if group.assistant_leader else None,
        member_count=member_count
    )


@router.put("/cell-groups/{group_id}", response_model=CellGroupResponse)
def update_cell_group(
    group_id: int,
    group_update: CellGroupUpdate,
    db: Session = Depends(get_db)
):
    """Update a cell group"""
    group = db.query(CellGroup).filter(CellGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Cell group not found")

    update_data = group_update.model_dump(exclude_unset=True)

    # Verify leader exists if updating
    if "leader_id" in update_data and update_data["leader_id"]:
        leader = db.query(Member).filter(Member.id == update_data["leader_id"]).first()
        if not leader:
            raise HTTPException(status_code=404, detail="Leader not found")

    if "assistant_leader_id" in update_data and update_data["assistant_leader_id"]:
        assistant = db.query(Member).filter(Member.id == update_data["assistant_leader_id"]).first()
        if not assistant:
            raise HTTPException(status_code=404, detail="Assistant leader not found")

    for key, value in update_data.items():
        setattr(group, key, value)

    db.commit()
    db.refresh(group)

    member_count = db.query(func.count(CellGroupMembership.id)).filter(
        CellGroupMembership.cell_group_id == group_id,
        CellGroupMembership.is_active == True
    ).scalar()

    return CellGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        meeting_day=group.meeting_day,
        meeting_time=group.meeting_time,
        meeting_location=group.meeting_location,
        is_active=group.is_active,
        created_at=group.created_at,
        leader=CellGroupMemberBrief(
            id=group.leader.id,
            full_name=group.leader.full_name,
            phone=group.leader.phone
        ) if group.leader else None,
        assistant_leader=CellGroupMemberBrief(
            id=group.assistant_leader.id,
            full_name=group.assistant_leader.full_name,
            phone=group.assistant_leader.phone
        ) if group.assistant_leader else None,
        member_count=member_count
    )


@router.delete("/cell-groups/{group_id}")
def delete_cell_group(group_id: int, db: Session = Depends(get_db)):
    """Delete a cell group"""
    group = db.query(CellGroup).filter(CellGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Cell group not found")

    db.delete(group)
    db.commit()
    return {"message": "Cell group deleted"}


# ============ CELL GROUP MEMBERS ============

@router.get("/cell-groups/{group_id}/members", response_model=List[CellMembershipResponse])
def get_cell_members(
    group_id: int,
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    """Get all members of a cell group"""
    group = db.query(CellGroup).filter(CellGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Cell group not found")

    query = db.query(CellGroupMembership).filter(
        CellGroupMembership.cell_group_id == group_id
    )

    if active_only:
        query = query.filter(CellGroupMembership.is_active == True)

    memberships = query.order_by(CellGroupMembership.role, CellGroupMembership.joined_at).all()

    return [
        CellMembershipResponse(
            id=m.id,
            cell_group_id=m.cell_group_id,
            member_id=m.member_id,
            member_name=m.member.full_name,
            member_phone=m.member.phone,
            role=m.role,
            joined_at=m.joined_at,
            left_at=m.left_at,
            is_active=m.is_active
        )
        for m in memberships
    ]


@router.post("/cell-groups/{group_id}/members", response_model=CellMembershipResponse)
def add_cell_member(
    group_id: int,
    membership: CellMembershipCreate,
    db: Session = Depends(get_db)
):
    """Add a member to a cell group"""
    group = db.query(CellGroup).filter(CellGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Cell group not found")

    member = db.query(Member).filter(Member.id == membership.member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check for existing membership
    existing = db.query(CellGroupMembership).filter(
        CellGroupMembership.cell_group_id == group_id,
        CellGroupMembership.member_id == membership.member_id
    ).first()

    if existing:
        if existing.is_active:
            raise HTTPException(status_code=400, detail="Member already in this cell group")
        else:
            # Reactivate membership
            existing.is_active = True
            existing.left_at = None
            existing.role = membership.role
            db.commit()
            db.refresh(existing)
            return CellMembershipResponse(
                id=existing.id,
                cell_group_id=existing.cell_group_id,
                member_id=existing.member_id,
                member_name=member.full_name,
                member_phone=member.phone,
                role=existing.role,
                joined_at=existing.joined_at,
                left_at=existing.left_at,
                is_active=existing.is_active
            )

    db_membership = CellGroupMembership(
        cell_group_id=group_id,
        member_id=membership.member_id,
        role=membership.role
    )
    db.add(db_membership)
    db.commit()
    db.refresh(db_membership)

    return CellMembershipResponse(
        id=db_membership.id,
        cell_group_id=db_membership.cell_group_id,
        member_id=db_membership.member_id,
        member_name=member.full_name,
        member_phone=member.phone,
        role=db_membership.role,
        joined_at=db_membership.joined_at,
        left_at=db_membership.left_at,
        is_active=db_membership.is_active
    )


@router.put("/cell-groups/{group_id}/members/{member_id}", response_model=CellMembershipResponse)
def update_cell_membership(
    group_id: int,
    member_id: int,
    update: CellMembershipUpdate,
    db: Session = Depends(get_db)
):
    """Update a member's role or status in a cell group"""
    membership = db.query(CellGroupMembership).filter(
        CellGroupMembership.cell_group_id == group_id,
        CellGroupMembership.member_id == member_id
    ).first()

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(membership, key, value)

    # Set left_at if deactivating
    if "is_active" in update_data and not update_data["is_active"]:
        membership.left_at = datetime.utcnow()

    db.commit()
    db.refresh(membership)

    return CellMembershipResponse(
        id=membership.id,
        cell_group_id=membership.cell_group_id,
        member_id=membership.member_id,
        member_name=membership.member.full_name,
        member_phone=membership.member.phone,
        role=membership.role,
        joined_at=membership.joined_at,
        left_at=membership.left_at,
        is_active=membership.is_active
    )


@router.delete("/cell-groups/{group_id}/members/{member_id}")
def remove_cell_member(
    group_id: int,
    member_id: int,
    db: Session = Depends(get_db)
):
    """Remove a member from a cell group (soft delete)"""
    membership = db.query(CellGroupMembership).filter(
        CellGroupMembership.cell_group_id == group_id,
        CellGroupMembership.member_id == member_id
    ).first()

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    membership.is_active = False
    membership.left_at = datetime.utcnow()
    db.commit()

    return {"message": "Member removed from cell group"}


# ============ CELL MEETINGS ============

@router.get("/cell-groups/{group_id}/meetings", response_model=List[CellMeetingResponse])
def get_cell_meetings(
    group_id: int,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db)
):
    """Get meetings for a cell group"""
    group = db.query(CellGroup).filter(CellGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Cell group not found")

    meetings = db.query(CellMeeting).filter(
        CellMeeting.cell_group_id == group_id
    ).order_by(CellMeeting.date.desc()).limit(limit).all()

    result = []
    for meeting in meetings:
        attendance_count = db.query(func.count(CellMeetingAttendance.id)).filter(
            CellMeetingAttendance.meeting_id == meeting.id
        ).scalar()

        result.append(CellMeetingResponse(
            id=meeting.id,
            cell_group_id=meeting.cell_group_id,
            date=meeting.date,
            topic=meeting.topic,
            notes=meeting.notes,
            offering_amount=meeting.offering_amount,
            created_at=meeting.created_at,
            attendance_count=attendance_count
        ))

    return result


@router.post("/cell-groups/{group_id}/meetings", response_model=CellMeetingResponse)
def create_cell_meeting(
    group_id: int,
    meeting: CellMeetingCreate,
    db: Session = Depends(get_db)
):
    """Create a new cell meeting"""
    group = db.query(CellGroup).filter(CellGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Cell group not found")

    db_meeting = CellMeeting(
        cell_group_id=group_id,
        **meeting.model_dump()
    )
    db.add(db_meeting)
    db.commit()
    db.refresh(db_meeting)

    return CellMeetingResponse(
        id=db_meeting.id,
        cell_group_id=db_meeting.cell_group_id,
        date=db_meeting.date,
        topic=db_meeting.topic,
        notes=db_meeting.notes,
        offering_amount=db_meeting.offering_amount,
        created_at=db_meeting.created_at,
        attendance_count=0
    )


@router.get("/cell-meetings/{meeting_id}", response_model=CellMeetingResponse)
def get_cell_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """Get a specific cell meeting"""
    meeting = db.query(CellMeeting).filter(CellMeeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    attendance_count = db.query(func.count(CellMeetingAttendance.id)).filter(
        CellMeetingAttendance.meeting_id == meeting_id
    ).scalar()

    return CellMeetingResponse(
        id=meeting.id,
        cell_group_id=meeting.cell_group_id,
        date=meeting.date,
        topic=meeting.topic,
        notes=meeting.notes,
        offering_amount=meeting.offering_amount,
        created_at=meeting.created_at,
        attendance_count=attendance_count
    )


@router.put("/cell-meetings/{meeting_id}", response_model=CellMeetingResponse)
def update_cell_meeting(
    meeting_id: int,
    update: CellMeetingUpdate,
    db: Session = Depends(get_db)
):
    """Update a cell meeting"""
    meeting = db.query(CellMeeting).filter(CellMeeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(meeting, key, value)

    db.commit()
    db.refresh(meeting)

    attendance_count = db.query(func.count(CellMeetingAttendance.id)).filter(
        CellMeetingAttendance.meeting_id == meeting_id
    ).scalar()

    return CellMeetingResponse(
        id=meeting.id,
        cell_group_id=meeting.cell_group_id,
        date=meeting.date,
        topic=meeting.topic,
        notes=meeting.notes,
        offering_amount=meeting.offering_amount,
        created_at=meeting.created_at,
        attendance_count=attendance_count
    )


@router.delete("/cell-meetings/{meeting_id}")
def delete_cell_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """Delete a cell meeting"""
    meeting = db.query(CellMeeting).filter(CellMeeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    db.delete(meeting)
    db.commit()
    return {"message": "Meeting deleted"}


# ============ CELL MEETING ATTENDANCE ============

@router.get("/cell-meetings/{meeting_id}/attendance", response_model=List[CellMeetingAttendanceResponse])
def get_cell_meeting_attendance(meeting_id: int, db: Session = Depends(get_db)):
    """Get attendance for a cell meeting"""
    meeting = db.query(CellMeeting).filter(CellMeeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    attendance = db.query(CellMeetingAttendance).filter(
        CellMeetingAttendance.meeting_id == meeting_id
    ).all()

    return [
        CellMeetingAttendanceResponse(
            id=a.id,
            meeting_id=a.meeting_id,
            member_id=a.member_id,
            member_name=a.member.full_name if a.member else None,
            visitor_name=a.visitor_name,
            visitor_phone=a.visitor_phone,
            created_at=a.created_at
        )
        for a in attendance
    ]


@router.post("/cell-meetings/{meeting_id}/attendance", response_model=CellMeetingAttendanceResponse)
def record_cell_meeting_attendance(
    meeting_id: int,
    attendance: CellMeetingAttendanceCreate,
    db: Session = Depends(get_db)
):
    """Record attendance for a cell meeting"""
    meeting = db.query(CellMeeting).filter(CellMeeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Validate input
    if not attendance.member_id and not attendance.visitor_name:
        raise HTTPException(
            status_code=400,
            detail="Either member_id or visitor_name is required"
        )

    # Check for duplicate if member
    if attendance.member_id:
        existing = db.query(CellMeetingAttendance).filter(
            CellMeetingAttendance.meeting_id == meeting_id,
            CellMeetingAttendance.member_id == attendance.member_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Member already recorded")

        # Verify member exists
        member = db.query(Member).filter(Member.id == attendance.member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

    db_attendance = CellMeetingAttendance(
        meeting_id=meeting_id,
        **attendance.model_dump()
    )
    db.add(db_attendance)
    db.commit()
    db.refresh(db_attendance)

    return CellMeetingAttendanceResponse(
        id=db_attendance.id,
        meeting_id=db_attendance.meeting_id,
        member_id=db_attendance.member_id,
        member_name=db_attendance.member.full_name if db_attendance.member else None,
        visitor_name=db_attendance.visitor_name,
        visitor_phone=db_attendance.visitor_phone,
        created_at=db_attendance.created_at
    )


@router.delete("/cell-meeting-attendance/{attendance_id}")
def delete_cell_meeting_attendance(attendance_id: int, db: Session = Depends(get_db)):
    """Remove an attendance record"""
    attendance = db.query(CellMeetingAttendance).filter(
        CellMeetingAttendance.id == attendance_id
    ).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    db.delete(attendance)
    db.commit()
    return {"message": "Attendance record removed"}


# ============ CELL LEADER PORTAL ============

@router.get("/cell-leader/my-groups", response_model=List[CellLeaderGroupResponse])
def get_my_cell_groups(phone: str, db: Session = Depends(get_db)):
    """Get cell groups where the member is a leader"""
    member = db.query(Member).filter(Member.phone == phone).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Find groups where member is leader or assistant leader
    groups = db.query(CellGroup).filter(
        CellGroup.is_active == True,
        (CellGroup.leader_id == member.id) | (CellGroup.assistant_leader_id == member.id)
    ).all()

    result = []
    for group in groups:
        member_count = db.query(func.count(CellGroupMembership.id)).filter(
            CellGroupMembership.cell_group_id == group.id,
            CellGroupMembership.is_active == True
        ).scalar()

        # Get recent meetings
        recent_meetings = db.query(CellMeeting).filter(
            CellMeeting.cell_group_id == group.id
        ).order_by(CellMeeting.date.desc()).limit(5).all()

        meetings_response = []
        for meeting in recent_meetings:
            attendance_count = db.query(func.count(CellMeetingAttendance.id)).filter(
                CellMeetingAttendance.meeting_id == meeting.id
            ).scalar()
            meetings_response.append(CellMeetingResponse(
                id=meeting.id,
                cell_group_id=meeting.cell_group_id,
                date=meeting.date,
                topic=meeting.topic,
                notes=meeting.notes,
                offering_amount=meeting.offering_amount,
                created_at=meeting.created_at,
                attendance_count=attendance_count
            ))

        result.append(CellLeaderGroupResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            meeting_day=group.meeting_day,
            meeting_time=group.meeting_time,
            meeting_location=group.meeting_location,
            member_count=member_count,
            recent_meetings=meetings_response
        ))

    return result
