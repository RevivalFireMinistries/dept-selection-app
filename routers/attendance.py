from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import List, Optional
from datetime import date, datetime, timedelta
from database import get_db
from models import (
    Service, ServiceInstance, Attendance, Visitor, MemberQRCode, Member
)
from schemas import (
    ServiceCreate, ServiceUpdate, ServiceResponse,
    ServiceInstanceCreate, ServiceInstanceResponse,
    VisitorCreate, VisitorUpdate, VisitorResponse,
    AttendanceCreate, AttendanceResponse,
    PhoneCheckInRequest, QRCheckInRequest, CheckInResponse,
    MemberQRCodeResponse
)
import io

router = APIRouter(prefix="/api", tags=["attendance"])


# ============ SERVICE MANAGEMENT ============

@router.get("/services", response_model=List[ServiceResponse])
def get_services(
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    """Get all services, optionally filtered by active status"""
    query = db.query(Service)
    if active_only:
        query = query.filter(Service.is_active == True)
    return query.order_by(Service.day_of_week, Service.start_time).all()


@router.post("/services", response_model=ServiceResponse)
def create_service(service: ServiceCreate, db: Session = Depends(get_db)):
    """Create a new service"""
    db_service = Service(**service.model_dump())
    db.add(db_service)
    db.commit()
    db.refresh(db_service)
    return db_service


@router.get("/services/{service_id}", response_model=ServiceResponse)
def get_service(service_id: int, db: Session = Depends(get_db)):
    """Get a specific service"""
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    return service


@router.put("/services/{service_id}", response_model=ServiceResponse)
def update_service(
    service_id: int,
    service_update: ServiceUpdate,
    db: Session = Depends(get_db)
):
    """Update a service"""
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    update_data = service_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(service, key, value)

    db.commit()
    db.refresh(service)
    return service


@router.delete("/services/{service_id}")
def delete_service(service_id: int, db: Session = Depends(get_db)):
    """Delete a service"""
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    db.delete(service)
    db.commit()
    return {"message": "Service deleted"}


# ============ SERVICE INSTANCES ============

@router.get("/service-instances", response_model=List[ServiceInstanceResponse])
def get_service_instances(
    service_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    """Get service instances, optionally filtered by service and date range"""
    query = db.query(ServiceInstance)

    if service_id:
        query = query.filter(ServiceInstance.service_id == service_id)

    if start_date:
        query = query.filter(ServiceInstance.date >= start_date)

    if end_date:
        query = query.filter(ServiceInstance.date <= end_date)

    instances = query.order_by(ServiceInstance.date.desc()).limit(100).all()

    # Add attendance count to each instance
    result = []
    for instance in instances:
        count = db.query(func.count(Attendance.id)).filter(
            Attendance.service_instance_id == instance.id
        ).scalar()

        response = ServiceInstanceResponse.model_validate(instance)
        response.attendance_count = count
        result.append(response)

    return result


@router.post("/service-instances", response_model=ServiceInstanceResponse)
def create_service_instance(
    instance: ServiceInstanceCreate,
    db: Session = Depends(get_db)
):
    """Create a service instance for a specific date"""
    # Check if service exists
    service = db.query(Service).filter(Service.id == instance.service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # Check for duplicate
    existing = db.query(ServiceInstance).filter(
        ServiceInstance.service_id == instance.service_id,
        ServiceInstance.date == instance.date
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Service instance already exists for this date")

    db_instance = ServiceInstance(**instance.model_dump())
    db.add(db_instance)
    db.commit()
    db.refresh(db_instance)
    return db_instance


@router.get("/service-instances/{instance_id}", response_model=ServiceInstanceResponse)
def get_service_instance(instance_id: int, db: Session = Depends(get_db)):
    """Get a specific service instance"""
    instance = db.query(ServiceInstance).filter(ServiceInstance.id == instance_id).first()
    if not instance:
        raise HTTPException(status_code=404, detail="Service instance not found")

    count = db.query(func.count(Attendance.id)).filter(
        Attendance.service_instance_id == instance.id
    ).scalar()

    response = ServiceInstanceResponse.model_validate(instance)
    response.attendance_count = count
    return response


@router.get("/service-instances/today", response_model=List[ServiceInstanceResponse])
def get_todays_services(db: Session = Depends(get_db)):
    """Get or create service instances for today"""
    today = date.today()
    today_day_of_week = today.weekday()  # 0=Monday, 6=Sunday

    # Get active services for today
    services = db.query(Service).filter(
        Service.is_active == True,
        Service.day_of_week == today_day_of_week
    ).all()

    result = []
    for service in services:
        # Check if instance exists
        instance = db.query(ServiceInstance).filter(
            ServiceInstance.service_id == service.id,
            ServiceInstance.date == today
        ).first()

        # Create if doesn't exist
        if not instance:
            instance = ServiceInstance(service_id=service.id, date=today)
            db.add(instance)
            db.commit()
            db.refresh(instance)

        count = db.query(func.count(Attendance.id)).filter(
            Attendance.service_instance_id == instance.id
        ).scalar()

        response = ServiceInstanceResponse.model_validate(instance)
        response.attendance_count = count
        result.append(response)

    return result


# ============ ATTENDANCE ============

@router.get("/service-instances/{instance_id}/attendance", response_model=List[AttendanceResponse])
def get_instance_attendance(instance_id: int, db: Session = Depends(get_db)):
    """Get all attendance records for a service instance"""
    instance = db.query(ServiceInstance).filter(ServiceInstance.id == instance_id).first()
    if not instance:
        raise HTTPException(status_code=404, detail="Service instance not found")

    records = db.query(Attendance).filter(
        Attendance.service_instance_id == instance_id
    ).order_by(Attendance.check_in_time.desc()).all()

    result = []
    for record in records:
        response = AttendanceResponse(
            id=record.id,
            service_instance_id=record.service_instance_id,
            member_id=record.member_id,
            visitor_id=record.visitor_id,
            check_in_method=record.check_in_method,
            check_in_time=record.check_in_time,
            member_name=record.member.full_name if record.member else None,
            visitor_name=record.visitor.full_name if record.visitor else None
        )
        result.append(response)

    return result


@router.post("/service-instances/{instance_id}/attendance", response_model=AttendanceResponse)
def mark_attendance(
    instance_id: int,
    attendance: AttendanceCreate,
    db: Session = Depends(get_db)
):
    """Mark attendance for a member or visitor"""
    instance = db.query(ServiceInstance).filter(ServiceInstance.id == instance_id).first()
    if not instance:
        raise HTTPException(status_code=404, detail="Service instance not found")

    if instance.is_cancelled:
        raise HTTPException(status_code=400, detail="Cannot mark attendance for cancelled service")

    # Validate that either member_id or visitor_id is provided
    if not attendance.member_id and not attendance.visitor_id:
        raise HTTPException(status_code=400, detail="Either member_id or visitor_id is required")

    # Check for duplicate attendance
    existing_query = db.query(Attendance).filter(
        Attendance.service_instance_id == instance_id
    )

    if attendance.member_id:
        existing = existing_query.filter(Attendance.member_id == attendance.member_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Member already checked in")

        # Verify member exists
        member = db.query(Member).filter(Member.id == attendance.member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

    if attendance.visitor_id:
        existing = existing_query.filter(Attendance.visitor_id == attendance.visitor_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Visitor already checked in")

        # Verify visitor exists
        visitor = db.query(Visitor).filter(Visitor.id == attendance.visitor_id).first()
        if not visitor:
            raise HTTPException(status_code=404, detail="Visitor not found")

    db_attendance = Attendance(
        service_instance_id=instance_id,
        **attendance.model_dump()
    )
    db.add(db_attendance)
    db.commit()
    db.refresh(db_attendance)

    return AttendanceResponse(
        id=db_attendance.id,
        service_instance_id=db_attendance.service_instance_id,
        member_id=db_attendance.member_id,
        visitor_id=db_attendance.visitor_id,
        check_in_method=db_attendance.check_in_method,
        check_in_time=db_attendance.check_in_time,
        member_name=db_attendance.member.full_name if db_attendance.member else None,
        visitor_name=db_attendance.visitor.full_name if db_attendance.visitor else None
    )


@router.delete("/attendance/{attendance_id}")
def remove_attendance(attendance_id: int, db: Session = Depends(get_db)):
    """Remove an attendance record"""
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    db.delete(attendance)
    db.commit()
    return {"message": "Attendance removed"}


# ============ CHECK-IN METHODS ============

@router.post("/checkin/phone", response_model=CheckInResponse)
def phone_checkin(request: PhoneCheckInRequest, db: Session = Depends(get_db)):
    """Self check-in using phone number"""
    # Find member by phone
    member = db.query(Member).filter(Member.phone == request.phone).first()
    if not member:
        return CheckInResponse(
            success=False,
            message="Phone number not found. Please register first."
        )

    # Check service instance
    instance = db.query(ServiceInstance).filter(
        ServiceInstance.id == request.service_instance_id
    ).first()

    if not instance:
        return CheckInResponse(success=False, message="Service not found")

    if instance.is_cancelled:
        return CheckInResponse(success=False, message="Service has been cancelled")

    # Check if already checked in
    existing = db.query(Attendance).filter(
        Attendance.service_instance_id == instance.id,
        Attendance.member_id == member.id
    ).first()

    if existing:
        return CheckInResponse(
            success=False,
            message="You're already checked in!",
            member_name=member.full_name,
            service_name=instance.service.name
        )

    # Create attendance record
    attendance = Attendance(
        service_instance_id=instance.id,
        member_id=member.id,
        check_in_method="self"
    )
    db.add(attendance)
    db.commit()

    return CheckInResponse(
        success=True,
        message="Check-in successful!",
        member_name=member.full_name,
        service_name=instance.service.name
    )


@router.post("/checkin/qr", response_model=CheckInResponse)
def qr_checkin(request: QRCheckInRequest, db: Session = Depends(get_db)):
    """Check-in using QR code"""
    # Find member by QR code
    qr_code = db.query(MemberQRCode).filter(
        MemberQRCode.code == request.qr_code,
        MemberQRCode.is_active == True
    ).first()

    if not qr_code:
        return CheckInResponse(success=False, message="Invalid or inactive QR code")

    member = qr_code.member

    # Check service instance
    instance = db.query(ServiceInstance).filter(
        ServiceInstance.id == request.service_instance_id
    ).first()

    if not instance:
        return CheckInResponse(success=False, message="Service not found")

    if instance.is_cancelled:
        return CheckInResponse(success=False, message="Service has been cancelled")

    # Check if already checked in
    existing = db.query(Attendance).filter(
        Attendance.service_instance_id == instance.id,
        Attendance.member_id == member.id
    ).first()

    if existing:
        return CheckInResponse(
            success=False,
            message="Already checked in!",
            member_name=member.full_name,
            service_name=instance.service.name
        )

    # Create attendance record
    attendance = Attendance(
        service_instance_id=instance.id,
        member_id=member.id,
        check_in_method="qr"
    )
    db.add(attendance)
    db.commit()

    return CheckInResponse(
        success=True,
        message="Check-in successful!",
        member_name=member.full_name,
        service_name=instance.service.name
    )


# ============ VISITORS ============

@router.get("/visitors", response_model=List[VisitorResponse])
def get_visitors(
    converted: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    """Get all visitors, optionally filtered by conversion status"""
    query = db.query(Visitor)

    if converted is not None:
        if converted:
            query = query.filter(Visitor.converted_to_member_id.isnot(None))
        else:
            query = query.filter(Visitor.converted_to_member_id.is_(None))

    return query.order_by(Visitor.first_visit_date.desc()).all()


@router.post("/visitors", response_model=VisitorResponse)
def create_visitor(visitor: VisitorCreate, db: Session = Depends(get_db)):
    """Register a new visitor (first-timer)"""
    db_visitor = Visitor(**visitor.model_dump())
    db.add(db_visitor)
    db.commit()
    db.refresh(db_visitor)
    return db_visitor


@router.get("/visitors/{visitor_id}", response_model=VisitorResponse)
def get_visitor(visitor_id: int, db: Session = Depends(get_db)):
    """Get a specific visitor"""
    visitor = db.query(Visitor).filter(Visitor.id == visitor_id).first()
    if not visitor:
        raise HTTPException(status_code=404, detail="Visitor not found")
    return visitor


@router.put("/visitors/{visitor_id}", response_model=VisitorResponse)
def update_visitor(
    visitor_id: int,
    visitor_update: VisitorUpdate,
    db: Session = Depends(get_db)
):
    """Update visitor information"""
    visitor = db.query(Visitor).filter(Visitor.id == visitor_id).first()
    if not visitor:
        raise HTTPException(status_code=404, detail="Visitor not found")

    update_data = visitor_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(visitor, key, value)

    db.commit()
    db.refresh(visitor)
    return visitor


@router.post("/visitors/{visitor_id}/convert")
def convert_visitor_to_member(
    visitor_id: int,
    db: Session = Depends(get_db)
):
    """Convert a visitor to a member"""
    visitor = db.query(Visitor).filter(Visitor.id == visitor_id).first()
    if not visitor:
        raise HTTPException(status_code=404, detail="Visitor not found")

    if visitor.converted_to_member_id:
        raise HTTPException(status_code=400, detail="Visitor already converted")

    # Create new member from visitor data
    member = Member(
        full_name=visitor.full_name,
        phone=visitor.phone or "",
        email=visitor.email or "",
        address=visitor.address or "",
        member_since=date.today()
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    # Update visitor with member reference
    visitor.converted_to_member_id = member.id
    db.commit()

    return {
        "message": "Visitor converted to member",
        "member_id": member.id,
        "member_name": member.full_name
    }


# ============ QR CODES ============

@router.get("/members/{member_id}/qr", response_model=MemberQRCodeResponse)
def get_member_qr_code(member_id: int, db: Session = Depends(get_db)):
    """Get or create QR code for a member"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check for existing QR code
    qr_code = db.query(MemberQRCode).filter(MemberQRCode.member_id == member_id).first()

    if not qr_code:
        # Create new QR code
        import uuid
        qr_code = MemberQRCode(
            member_id=member_id,
            code=str(uuid.uuid4())
        )
        db.add(qr_code)
        db.commit()
        db.refresh(qr_code)

    return qr_code


@router.post("/members/{member_id}/qr/regenerate", response_model=MemberQRCodeResponse)
def regenerate_qr_code(member_id: int, db: Session = Depends(get_db)):
    """Regenerate QR code for a member"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    import uuid

    # Deactivate existing QR code
    existing = db.query(MemberQRCode).filter(MemberQRCode.member_id == member_id).first()
    if existing:
        existing.is_active = False

    # Create new QR code
    qr_code = MemberQRCode(
        member_id=member_id,
        code=str(uuid.uuid4())
    )
    db.add(qr_code)
    db.commit()
    db.refresh(qr_code)

    return qr_code


@router.get("/members/{member_id}/qr/image")
def get_qr_code_image(member_id: int, db: Session = Depends(get_db)):
    """Generate and return QR code image for a member"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Get or create QR code
    qr_code = db.query(MemberQRCode).filter(
        MemberQRCode.member_id == member_id,
        MemberQRCode.is_active == True
    ).first()

    if not qr_code:
        import uuid
        qr_code = MemberQRCode(
            member_id=member_id,
            code=str(uuid.uuid4())
        )
        db.add(qr_code)
        db.commit()
        db.refresh(qr_code)

    # Generate QR code image
    try:
        import qrcode
        from PIL import Image

        # Create QR code with checkin prefix
        qr_data = f"checkin:{qr_code.code}"
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        # Convert to bytes
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)

        return StreamingResponse(
            img_byte_arr,
            media_type="image/png",
            headers={
                "Content-Disposition": f"inline; filename=qr_{member.full_name.replace(' ', '_')}.png"
            }
        )
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="QR code generation not available. Please install qrcode and Pillow packages."
        )


# ============ ATTENDANCE REPORTS ============

@router.get("/attendance/report")
def attendance_report(
    start_date: date,
    end_date: date,
    service_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Get attendance statistics for a date range"""
    query = db.query(ServiceInstance).filter(
        ServiceInstance.date >= start_date,
        ServiceInstance.date <= end_date,
        ServiceInstance.is_cancelled == False
    )

    if service_id:
        query = query.filter(ServiceInstance.service_id == service_id)

    instances = query.all()

    report = {
        "date_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "total_services": len(instances),
        "total_attendance": 0,
        "member_attendance": 0,
        "visitor_attendance": 0,
        "by_service": {},
        "by_date": {}
    }

    for instance in instances:
        attendance_records = db.query(Attendance).filter(
            Attendance.service_instance_id == instance.id
        ).all()

        member_count = sum(1 for a in attendance_records if a.member_id)
        visitor_count = sum(1 for a in attendance_records if a.visitor_id)
        total = member_count + visitor_count

        report["total_attendance"] += total
        report["member_attendance"] += member_count
        report["visitor_attendance"] += visitor_count

        # By service
        service_name = instance.service.name
        if service_name not in report["by_service"]:
            report["by_service"][service_name] = {"count": 0, "total": 0}
        report["by_service"][service_name]["count"] += 1
        report["by_service"][service_name]["total"] += total

        # By date
        date_str = instance.date.isoformat()
        if date_str not in report["by_date"]:
            report["by_date"][date_str] = 0
        report["by_date"][date_str] += total

    # Calculate averages
    if report["total_services"] > 0:
        report["average_attendance"] = round(report["total_attendance"] / report["total_services"], 1)
    else:
        report["average_attendance"] = 0

    return report
