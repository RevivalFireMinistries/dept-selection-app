from fastapi import APIRouter, Depends, HTTPException, Query, Body, Response, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_, and_
from typing import Optional, Dict, Any, List, Tuple
from io import BytesIO
from datetime import datetime, date, timedelta
import os
import re
import uuid
import json

from database import get_db
from models import Category, Department, Member, MemberDepartment, Settings, Appeal, Meeting, MeetingRSVP, NotificationConfig, NotificationLog, PosterRequest, ServiceProgram, ProgramTemplate, ServiceSchedule, AdminAuditLog, HomeChurch, HomeChurchProgramType, HomeChurchRoster, HomeChurchAttendance
from schemas import (
    CategoryCreate, CategoryUpdate, CategoryResponse,
    DepartmentCreate, DepartmentUpdate, DepartmentResponse, DepartmentInCategory,
    MemberSubmission, MemberResponse,
    SettingUpdate, DepartmentsGroupedResponse,
    ReviewStatusUpdate, ReplaceDepartmentRequest, AssignDepartmentRequest,
    AppealCreate, AppealResolve,
    SetHODRequest,
    MeetingCreate, MeetingUpdate, RSVPRequest,
    SMTPSettingsUpdate, NotificationConfigUpdate, TestEmailRequest,
    PosterRequestCreate, PosterRequestResponse,
    ServiceProgramCreate, ServiceProgramUpdate, ServiceProgramResponse,
    ProgramTemplateCreate, ProgramTemplateUpdate,
    ServiceScheduleCreate, ServiceScheduleUpdate
)

router = APIRouter()


def validate_phone(phone: str) -> bool:
    """Validate phone number is exactly 10 digits"""
    digits = re.sub(r'\D', '', phone)
    return len(digits) == 10


def check_selections_locked(db: Session):
    """Raise 403 if results are published (selections are locked for non-admins)"""
    setting = db.query(Settings).filter(Settings.key == "resultsPublished").first()
    if setting and setting.value == "true":
        raise HTTPException(
            status_code=403,
            detail="Department selections are locked. Results have been published."
        )


# ============ AUTHENTICATION ============

import bcrypt
import secrets

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))


def _mask_email(email: str) -> str:
    """Mask email for display: j***n@example.com"""
    if not email or '@' not in email:
        return email or ""
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked = local[0] + '***'
    else:
        masked = local[0] + '***' + local[-1]
    return f"{masked}@{domain}"


def _send_password_setup_email(db, member, token: str):
    """Send a password setup email to a member who doesn't have a password yet."""
    try:
        from notifications.dispatcher import get_email_settings

        settings_dict = {}
        for s in db.query(Settings).all():
            settings_dict[s.key] = s.value

        email_settings = get_email_settings(settings_dict)

        channel = None
        if email_settings.get("resend_enabled"):
            from notifications.channels.resend import ResendChannel
            channel = ResendChannel(
                api_key=email_settings["resend_api_key"],
                from_name=email_settings.get("resend_from_name", "RFM Stellenbosch"),
                from_email=email_settings["resend_from_email"]
            )
        elif email_settings.get("smtp_enabled"):
            from notifications.channels.email import EmailChannel
            channel = EmailChannel(
                host=email_settings["smtp_host"],
                port=int(email_settings.get("smtp_port", 587)),
                username=email_settings.get("smtp_username"),
                password=email_settings.get("smtp_password"),
                from_name=email_settings.get("smtp_from_name", "RFM Stellenbosch"),
                from_email=email_settings["smtp_from_email"],
                use_tls=True
            )

        if not channel:
            print("No email channel configured for password setup")
            return

        base_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
        if base_url and not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        if not base_url:
            base_url = os.environ.get("BASE_URL", "http://localhost:8000")
        reset_url = f"{base_url}/reset-password?token={token}"

        FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
        html = f"""
        <div style="max-width:480px;margin:0 auto;font-family:{FONT};color:#111827;">
            <div style="border-top:3px solid #6366f1;padding:32px 0 16px;">
                <h1 style="font-size:20px;margin:0 0 8px;">Set Up Your Password</h1>
                <p style="color:#6b7280;font-size:14px;margin:0 0 8px;">
                    Hi {member.full_name},
                </p>
                <p style="color:#6b7280;font-size:14px;margin:0 0 24px;">
                    The RFM Stellenbosch Portal now requires a password to sign in. Click below to set up your password.
                </p>
                <a href="{reset_url}" style="display:inline-block;padding:12px 32px;background:#6366f1;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;">
                    Set My Password
                </a>
                <p style="color:#9ca3af;font-size:12px;margin:24px 0 0;">
                    This link expires in 24 hours. If you didn't try to log in, you can safely ignore this email.
                </p>
            </div>
            <div style="border-top:1px solid #e5e7eb;padding:16px 0;margin-top:24px;">
                <p style="color:#9ca3af;font-size:11px;margin:0;">RFM Stellenbosch Portal</p>
            </div>
        </div>
        """

        success, error = channel.send(member.email, "Set Up Your Password - RFM Stellenbosch Portal", html)
        if not success:
            print(f"Failed to send password setup email: {error}")
        else:
            print(f"Sent password setup email to {member.email}")

    except Exception as e:
        print(f"Error sending password setup email: {e}")


@router.post("/auth/register")
def register_member(
    request: Request,
    data: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Register a new member account (requires admin approval)"""
    full_name = (data.get("full_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    address = (data.get("address") or "").strip()
    password = data.get("password") or ""

    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if not phone or not validate_phone(phone):
        raise HTTPException(status_code=400, detail="Valid 10-digit phone number is required")
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    # Check if phone already exists
    normalized = phone.strip().replace(" ", "").replace("-", "")
    existing = db.query(Member).all()
    for m in existing:
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized:
            raise HTTPException(status_code=400, detail="An account with this phone number already exists. Try logging in or use Forgot Password.")

    member = Member(
        full_name=full_name,
        phone=phone,
        email=email,
        address=address or "",
        password_hash=_hash_password(password),
        is_active=False  # Requires admin approval
    )
    db.add(member)
    db.flush()
    _log_member_action(request, db, member, "register", "member", member.id, f"New registration (pending approval)")
    db.commit()

    return {"success": True, "message": "Your account has been created and is pending admin approval. You will be notified once approved."}


@router.post("/auth/login")
def login_member(
    data: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Login with phone + password"""
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""

    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        raise HTTPException(status_code=401, detail="No account found with this phone number")

    # First-time login: member has no password yet — phone number is the default password
    if not member.password_hash:
        # Verify they entered their phone number as the password
        phone_digits = member.phone.strip().replace(" ", "").replace("-", "")
        password_digits = password.strip().replace(" ", "").replace("-", "")
        if not password or password_digits != phone_digits:
            raise HTTPException(status_code=401, detail="First time signing in? Use your phone number as your password")

        # Generate token and redirect to set-password page
        token = secrets.token_urlsafe(32)
        member.reset_token = token
        member.reset_token_expires = datetime.utcnow() + timedelta(hours=2)
        db.commit()

        has_email = bool(member.email and member.email.strip())

        return JSONResponse(
            status_code=200,
            content={
                "needs_password": True,
                "token": token,
                "has_email": has_email,
                "full_name": member.full_name
            }
        )

    # Password provided — verify it
    if not password:
        raise HTTPException(status_code=401, detail="Please enter your password")

    if not _verify_password(password, member.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")

    # Check if account is active (admin approved)
    if not member.is_active:
        raise HTTPException(status_code=403, detail="Your account is pending admin approval. You will be notified once approved.")

    # Create response with session cookie
    from routers.pages import _sign_member_session, MEMBER_COOKIE_NAME, SESSION_MAX_AGE
    token = _sign_member_session(member.id)
    response = JSONResponse(content={
        "success": True,
        "member_id": member.id,
        "phone": member.phone,
        "full_name": member.full_name
    })
    response.set_cookie(
        key=MEMBER_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=SESSION_MAX_AGE,
        samesite="lax"
    )
    return response


@router.post("/auth/forgot-password")
def forgot_password(
    data: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Send password reset email"""
    phone = (data.get("phone") or "").strip()

    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        # Don't reveal whether the phone exists
        return {"success": True, "message": "If an account exists with this phone number, a reset link has been sent to the registered email."}

    if not member.email:
        raise HTTPException(status_code=400, detail="No email address on file. Please contact an admin.")

    # Generate reset token
    token = secrets.token_urlsafe(32)
    member.reset_token = token
    member.reset_token_expires = datetime.utcnow() + timedelta(hours=2)
    db.commit()

    # Send email
    try:
        from notifications.dispatcher import get_email_settings

        settings_dict = {}
        for s in db.query(Settings).all():
            settings_dict[s.key] = s.value

        email_settings = get_email_settings(settings_dict)

        if email_settings.get("resend_enabled"):
            from notifications.channels.resend import ResendChannel
            channel = ResendChannel(
                api_key=email_settings["resend_api_key"],
                from_name=email_settings.get("resend_from_name", "RFM Stellenbosch"),
                from_email=email_settings["resend_from_email"]
            )
        elif email_settings.get("smtp_enabled"):
            from notifications.channels.email import EmailChannel
            channel = EmailChannel(
                host=email_settings["smtp_host"],
                port=int(email_settings.get("smtp_port", 587)),
                username=email_settings.get("smtp_username"),
                password=email_settings.get("smtp_password"),
                from_name=email_settings.get("smtp_from_name", "RFM Stellenbosch"),
                from_email=email_settings["smtp_from_email"],
                use_tls=True
            )
        else:
            print("No email channel configured for password reset")
            return {"success": True, "message": "If an account exists with this phone number, a reset link has been sent to the registered email."}

        # Build reset URL
        base_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
        if base_url and not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        if not base_url:
            base_url = os.environ.get("BASE_URL", "http://localhost:8000")
        reset_url = f"{base_url}/reset-password?token={token}"

        FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
        html = f"""
        <div style="max-width:480px;margin:0 auto;font-family:{FONT};color:#111827;">
            <div style="border-top:3px solid #6366f1;padding:32px 0 16px;">
                <h1 style="font-size:20px;margin:0 0 8px;">Reset Your Password</h1>
                <p style="color:#6b7280;font-size:14px;margin:0 0 24px;">
                    We received a request to reset your password for the RFM Stellenbosch Portal.
                </p>
                <a href="{reset_url}" style="display:inline-block;padding:12px 32px;background:#6366f1;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;">
                    Reset Password
                </a>
                <p style="color:#9ca3af;font-size:12px;margin:24px 0 0;">
                    This link expires in 2 hours. If you didn't request this, you can safely ignore this email.
                </p>
            </div>
            <div style="border-top:1px solid #e5e7eb;padding:16px 0;margin-top:24px;">
                <p style="color:#9ca3af;font-size:11px;margin:0;">RFM Stellenbosch Portal</p>
            </div>
        </div>
        """

        success, error = channel.send(member.email, "Reset Your Password - RFM Stellenbosch Portal", html)
        if not success:
            print(f"Failed to send reset email: {error}")

    except Exception as e:
        print(f"Error sending reset email: {e}")

    return {"success": True, "message": "If an account exists with this phone number, a reset link has been sent to the registered email."}


@router.post("/auth/reset-password")
def reset_password(
    data: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Reset password using token from email"""
    token = (data.get("token") or "").strip()
    new_password = data.get("password") or ""

    if not token:
        raise HTTPException(status_code=400, detail="Reset token is required")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    member = db.query(Member).filter(
        Member.reset_token == token,
        Member.reset_token_expires > datetime.utcnow()
    ).first()

    if not member:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link. Please request a new one.")

    # Don't allow phone number as password
    phone_digits = member.phone.strip().replace(" ", "").replace("-", "")
    password_digits = new_password.strip().replace(" ", "").replace("-", "")
    if password_digits == phone_digits:
        raise HTTPException(status_code=400, detail="Your password cannot be your phone number. Please choose a different password.")

    member.password_hash = _hash_password(new_password)
    member.reset_token = None
    member.reset_token_expires = None

    # Allow updating email if provided (for members who didn't have one)
    new_email = (data.get("email") or "").strip()
    if new_email:
        member.email = new_email

    db.commit()

    return {"success": True, "message": "Password has been set successfully. You can now sign in."}


@router.get("/auth/me")
def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Check if user is logged in and return their info including departments"""
    from routers.pages import get_current_member
    member = get_current_member(request, db)
    if not member:
        return {"logged_in": False}

    roles = []
    if member.leadership_roles:
        try:
            roles = json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
        except (ValueError, TypeError):
            roles = []

    # Get approved departments with category info
    member_with_depts = db.query(Member).options(
        joinedload(Member.departments).joinedload(MemberDepartment.department).joinedload(Department.category)
    ).filter(Member.id == member.id).first()

    departments = []
    if member_with_depts:
        for md in member_with_depts.departments:
            if md.status == "approved":
                dept_name = md.department.name if md.department else ""
                cat_name = md.department.category.name if md.department and md.department.category else ""
                departments.append({"id": md.department.id, "name": dept_name, "category": cat_name})

    # Check if member still needs to set up a password
    needs_password_setup = not member.password_hash

    return {
        "logged_in": True,
        "member_id": member.id,
        "full_name": member.full_name,
        "phone": member.phone,
        "email": member.email,
        "leadership_roles": roles,
        "departments": departments,
        "needs_password_setup": needs_password_setup
    }


# ============ AUDIT LOGGING ============

def _log_admin_action(request: Request, db: Session, action: str, entity_type: str = None, entity_id: int = None, details: str = None):
    """Log an admin action for audit trail"""
    from routers.pages import get_admin_identity
    admin = get_admin_identity(request)
    log = AdminAuditLog(
        admin_member_id=admin["member_id"] if admin else None,
        admin_name=admin["name"] if admin else "Unknown",
        actor_type="admin",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=request.client.host if request.client else None
    )
    db.add(log)


def _log_member_action(request: Request, db: Session, member, action: str, entity_type: str = None, entity_id: int = None, details: str = None):
    """Log a member/user action for audit trail"""
    log = AdminAuditLog(
        admin_member_id=member.id if member else None,
        admin_name=member.full_name if member else "Unknown",
        actor_type="member",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=request.client.host if request.client else None
    )
    db.add(log)


def _log_system_action(db: Session, action: str, entity_type: str = None, entity_id: int = None, details: str = None):
    """Log a system/automated action for audit trail"""
    log = AdminAuditLog(
        admin_member_id=None,
        admin_name="System",
        actor_type="system",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=None
    )
    db.add(log)


@router.get("/admin/identity")
def get_admin_identity_endpoint(request: Request):
    """Get the current admin's identity from cookie"""
    from routers.pages import get_admin_identity
    admin = get_admin_identity(request)
    if not admin:
        return {"authenticated": False}
    return {"authenticated": True, "member_id": admin["member_id"], "name": admin["name"]}


@router.get("/admin/audit-log")
def get_audit_log(
    limit: int = Query(100),
    offset: int = Query(0),
    actor_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    member_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Get audit log entries with filtering"""
    query = db.query(AdminAuditLog)

    if actor_type:
        query = query.filter(AdminAuditLog.actor_type == actor_type)
    if action:
        query = query.filter(AdminAuditLog.action == action)
    if member_id:
        query = query.filter(AdminAuditLog.admin_member_id == member_id)
    if search:
        query = query.filter(
            or_(
                AdminAuditLog.admin_name.ilike(f"%{search}%"),
                AdminAuditLog.details.ilike(f"%{search}%"),
                AdminAuditLog.action.ilike(f"%{search}%")
            )
        )
    if date_from:
        query = query.filter(AdminAuditLog.created_at >= date_from)
    if date_to:
        query = query.filter(AdminAuditLog.created_at <= date_to + "T23:59:59")

    total = query.count()
    logs = query.order_by(AdminAuditLog.created_at.desc()).offset(offset).limit(limit).all()

    # Get distinct actors and actions for filter dropdowns
    all_actors = db.query(AdminAuditLog.admin_name, AdminAuditLog.admin_member_id, AdminAuditLog.actor_type)\
        .distinct().order_by(AdminAuditLog.admin_name).all()
    all_actions = db.query(AdminAuditLog.action).distinct().order_by(AdminAuditLog.action).all()

    return {
        "total": total,
        "actors": [{"name": a[0], "id": a[1], "actor_type": a[2]} for a in all_actors],
        "actions": [a[0] for a in all_actions],
        "logs": [{
            "id": l.id,
            "admin_name": l.admin_name,
            "admin_member_id": l.admin_member_id,
            "actor_type": l.actor_type if hasattr(l, 'actor_type') and l.actor_type else "admin",
            "action": l.action,
            "entity_type": l.entity_type,
            "entity_id": l.entity_id,
            "details": l.details,
            "ip_address": l.ip_address,
            "created_at": l.created_at.isoformat() if l.created_at else None
        } for l in logs]
    }


# ============ ADMIN: MEMBER APPROVAL ============

@router.get("/admin/pending-registrations")
def get_pending_registrations(db: Session = Depends(get_db)):
    """Admin: get all inactive (pending approval) members"""
    pending = db.query(Member).filter(Member.is_active == False).order_by(Member.created_at.desc()).all()
    return [{
        "id": m.id,
        "full_name": m.full_name,
        "phone": m.phone,
        "email": m.email,
        "created_at": m.created_at.isoformat() if m.created_at else None
    } for m in pending]


@router.post("/admin/approve-member/{member_id}")
def approve_member(member_id: int, request: Request = None, db: Session = Depends(get_db)):
    """Admin: approve a pending member registration"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    member.is_active = True
    if request:
        _log_admin_action(request, db, "approve_member", "member", member_id, f"Approved registration for {member.full_name}")
    db.commit()

    # Send approval email
    try:
        from notifications.dispatcher import get_email_settings
        settings_dict = {}
        for s in db.query(Settings).all():
            settings_dict[s.key] = s.value
        email_settings = get_email_settings(settings_dict)

        channel = None
        if email_settings.get("resend_enabled"):
            from notifications.channels.resend import ResendChannel
            channel = ResendChannel(
                api_key=email_settings["resend_api_key"],
                from_name=email_settings.get("resend_from_name", "RFM Stellenbosch"),
                from_email=email_settings["resend_from_email"]
            )
        elif email_settings.get("smtp_enabled"):
            from notifications.channels.email import EmailChannel
            channel = EmailChannel(
                host=email_settings["smtp_host"],
                port=int(email_settings.get("smtp_port", 587)),
                username=email_settings.get("smtp_username"),
                password=email_settings.get("smtp_password"),
                from_name=email_settings.get("smtp_from_name", "RFM Stellenbosch"),
                from_email=email_settings["smtp_from_email"],
                use_tls=True
            )

        if channel and member.email:
            base_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
            if base_url and not base_url.startswith("http"):
                base_url = f"https://{base_url}"
            if not base_url:
                base_url = os.environ.get("BASE_URL", "http://localhost:8000")

            FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
            html = f"""
            <div style="max-width:480px;margin:0 auto;font-family:{FONT};color:#111827;">
                <div style="border-top:3px solid #059669;padding:32px 0 16px;">
                    <h1 style="font-size:20px;margin:0 0 8px;">Account Approved!</h1>
                    <p style="color:#6b7280;font-size:14px;margin:0 0 24px;">
                        Hi {member.full_name}, your RFM Stellenbosch Portal account has been approved. You can now log in.
                    </p>
                    <a href="{base_url}" style="display:inline-block;padding:12px 32px;background:#059669;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;">
                        Log In Now
                    </a>
                </div>
                <div style="border-top:1px solid #e5e7eb;padding:16px 0;margin-top:24px;">
                    <p style="color:#9ca3af;font-size:11px;margin:0;">RFM Stellenbosch Portal</p>
                </div>
            </div>
            """
            channel.send(member.email, "Account Approved - RFM Stellenbosch Portal", html)
    except Exception as e:
        print(f"Error sending approval email: {e}")

    return {"success": True}


@router.post("/admin/reject-member/{member_id}")
def reject_member(member_id: int, request: Request = None, db: Session = Depends(get_db)):
    """Admin: reject and delete a pending member registration"""
    member = db.query(Member).filter(Member.id == member_id, Member.is_active == False).first()
    if not member:
        raise HTTPException(status_code=404, detail="Pending member not found")
    member_name = member.full_name
    if request:
        _log_admin_action(request, db, "reject_member", "member", member_id, f"Rejected registration for {member_name}")
    db.delete(member)
    db.commit()
    return {"success": True}


# ============ DEPARTMENTS ============

@router.get("/departments")
def get_departments(db: Session = Depends(get_db)):
    """Get all departments grouped by category"""
    categories = db.query(Category).options(
        joinedload(Category.departments).joinedload(Department.hod)
    ).order_by(Category.name).all()

    uncategorized = db.query(Department).options(
        joinedload(Department.hod)
    ).filter(
        Department.category_id == None
    ).order_by(Department.name).all()

    def dept_dict(d):
        result = {"id": d.id, "name": d.name, "categoryId": d.category_id}
        if d.hod:
            result["hod"] = {"id": d.hod.id, "fullName": d.hod.full_name, "phone": d.hod.phone}
        else:
            result["hod"] = None
        return result

    return {
        "categories": [
            {
                "id": cat.id,
                "name": cat.name,
                "maxSelections": cat.max_selections,
                "createdAt": cat.created_at.isoformat() if cat.created_at else None,
                "departments": [
                    dept_dict(d)
                    for d in sorted(cat.departments, key=lambda x: x.name)
                ]
            }
            for cat in categories
        ],
        "uncategorized": [
            dept_dict(d)
            for d in uncategorized
        ]
    }


@router.post("/departments")
def create_department(data: DepartmentCreate, request: Request, db: Session = Depends(get_db)):
    """Create a new department"""
    if not data.name:
        raise HTTPException(status_code=400, detail="Name is required")

    department = Department(name=data.name, category_id=data.category_id)
    db.add(department)
    db.commit()
    db.refresh(department)

    _log_admin_action(request, db, "create_department", "department", department.id, f"Created department '{department.name}'")
    db.commit()

    return {"id": department.id, "name": department.name, "categoryId": department.category_id}


@router.put("/departments")
def update_department(data: DepartmentUpdate, request: Request, db: Session = Depends(get_db)):
    """Update an existing department"""
    if not data.id or not data.name:
        raise HTTPException(status_code=400, detail="ID and name are required")

    department = db.query(Department).filter(Department.id == data.id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    old_name = department.name
    department.name = data.name
    department.category_id = data.category_id
    db.commit()

    _log_admin_action(request, db, "update_department", "department", department.id, f"Updated department '{old_name}' → '{department.name}'")
    db.commit()

    return {"id": department.id, "name": department.name, "categoryId": department.category_id}


@router.delete("/departments")
def delete_department(id: int = Query(...), request: Request = None, db: Session = Depends(get_db)):
    """Delete a department"""
    department = db.query(Department).filter(Department.id == id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    dept_name = department.name
    db.delete(department)
    db.commit()

    if request:
        _log_admin_action(request, db, "delete_department", "department", id, f"Deleted department '{dept_name}'")
        db.commit()

    return {"success": True}


# ============ CATEGORIES ============

@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    """Get all categories with their departments"""
    categories = db.query(Category).options(
        joinedload(Category.departments)
    ).order_by(Category.name).all()

    return [
        {
            "id": cat.id,
            "name": cat.name,
            "maxSelections": cat.max_selections,
            "createdAt": cat.created_at.isoformat() if cat.created_at else None,
            "departments": [
                {"id": d.id, "name": d.name, "categoryId": d.category_id}
                for d in sorted(cat.departments, key=lambda x: x.name)
            ]
        }
        for cat in categories
    ]


@router.post("/categories")
def create_category(data: CategoryCreate, request: Request, db: Session = Depends(get_db)):
    """Create a new category"""
    if not data.name:
        raise HTTPException(status_code=400, detail="Name is required")

    category = Category(name=data.name, max_selections=data.max_selections)
    db.add(category)
    db.commit()
    db.refresh(category)

    _log_admin_action(request, db, "create_category", "category", category.id, f"Created category '{category.name}' (max: {category.max_selections})")
    db.commit()

    return {"id": category.id, "name": category.name, "maxSelections": category.max_selections}


@router.put("/categories")
def update_category(data: CategoryUpdate, request: Request, db: Session = Depends(get_db)):
    """Update an existing category"""
    if not data.id or not data.name:
        raise HTTPException(status_code=400, detail="ID and name are required")

    category = db.query(Category).filter(Category.id == data.id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    old_name = category.name
    category.name = data.name
    category.max_selections = data.max_selections
    db.commit()

    _log_admin_action(request, db, "update_category", "category", category.id, f"Updated category '{old_name}' → '{category.name}'")
    db.commit()

    return {"id": category.id, "name": category.name, "maxSelections": category.max_selections}


@router.delete("/categories")
def delete_category(id: int = Query(...), request: Request = None, db: Session = Depends(get_db)):
    """Delete a category (departments become uncategorized)"""
    category = db.query(Category).filter(Category.id == id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    cat_name = category.name
    db.delete(category)
    db.commit()

    if request:
        _log_admin_action(request, db, "delete_category", "category", id, f"Deleted category '{cat_name}'")
        db.commit()

    return {"success": True}


# ============ MEMBERS ============

@router.get("/members")
def get_members(db: Session = Depends(get_db)):
    """Get all members with their departments"""
    members = db.query(Member).options(
        joinedload(Member.departments).joinedload(MemberDepartment.department).joinedload(Department.category)
    ).order_by(Member.created_at.desc()).all()

    return [
        {
            "id": m.id,
            "fullName": _title_case_name(m.full_name),
            "titledName": _get_titled_name(m),
            "phone": m.phone,
            "email": m.email,
            "address": m.address,
            "createdAt": m.created_at.isoformat() if m.created_at else None,
            "departments": [
                {
                    "id": md.id,
                    "memberId": md.member_id,
                    "departmentId": md.department_id,
                    "createdAt": md.created_at.isoformat() if md.created_at else None,
                    "department": {
                        "id": md.department.id,
                        "name": md.department.name,
                        "categoryId": md.department.category_id,
                        "category": {
                            "id": md.department.category.id,
                            "name": md.department.category.name
                        } if md.department.category else None
                    }
                }
                for md in m.departments
            ]
        }
        for m in members
    ]


@router.delete("/members")
def delete_member(id: int = Query(None), db: Session = Depends(get_db)):
    """Delete a member or all members if id=all"""
    if id is None:
        raise HTTPException(status_code=400, detail="Member ID is required")

    member = db.query(Member).filter(Member.id == id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    db.delete(member)
    db.commit()

    return {"success": True}


@router.delete("/members/purge")
def purge_all_members(db: Session = Depends(get_db)):
    """Delete all member submissions"""
    count = db.query(Member).count()
    db.query(MemberDepartment).delete()
    db.query(Member).delete()
    db.commit()

    return {"success": True, "deleted": count}


@router.get("/members/lookup")
def lookup_member_by_phone(phone: str = Query(...), db: Session = Depends(get_db)):
    """Lookup a member by phone number"""
    # Normalize phone - remove spaces and common formatting
    normalized = phone.strip().replace(" ", "").replace("-", "")

    # Try exact match first
    member = db.query(Member).filter(Member.phone == phone).first()

    # Try normalized match
    if not member:
        members = db.query(Member).all()
        for m in members:
            m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
            if m_normalized == normalized:
                member = m
                break

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    return {
        "id": member.id,
        "fullName": member.full_name,
        "phone": member.phone
    }


@router.get("/members/{member_id}")
def get_member_by_id(member_id: int, db: Session = Depends(get_db)):
    """Get a single member by ID with their departments"""
    member = db.query(Member).options(
        joinedload(Member.departments).joinedload(MemberDepartment.department)
    ).filter(Member.id == member_id).first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    return {
        "id": member.id,
        "fullName": member.full_name,
        "phone": member.phone,
        "email": member.email,
        "address": member.address,
        "createdAt": member.created_at.isoformat() if member.created_at else None,
        "departments": [
            {
                "id": md.id,
                "departmentId": md.department_id,
                "department": {
                    "id": md.department.id,
                    "name": md.department.name
                }
            }
            for md in member.departments
        ]
    }


@router.put("/members/{member_id}")
def update_member(member_id: int, data: dict, source: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Update a member's information and department selections"""
    if source != "desk":
        check_selections_locked(db)

    member = db.query(Member).filter(Member.id == member_id).first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Update basic info
    if "full_name" in data:
        member.full_name = _title_case_name(data["full_name"])
    if "email" in data:
        member.email = data["email"]
    if "address" in data:
        member.address = data["address"]

    # Update departments if provided
    if "selected_departments" in data:
        selected = data["selected_departments"]

        # Validate max departments
        max_setting = db.query(Settings).filter(Settings.key == "maxDepartments").first()
        max_departments = int(max_setting.value) if max_setting else 3

        if len(selected) > max_departments:
            raise HTTPException(
                status_code=400,
                detail=f"You can only select up to {max_departments} departments"
            )

        # Validate per-category limits
        departments = db.query(Department).options(
            joinedload(Department.category)
        ).filter(Department.id.in_(selected)).all()

        category_selections: Dict[int, list] = {}
        for dept in departments:
            if dept.category_id:
                if dept.category_id not in category_selections:
                    category_selections[dept.category_id] = []
                category_selections[dept.category_id].append(dept.id)

        for category_id, selected_dept_ids in category_selections.items():
            category = db.query(Category).filter(Category.id == category_id).first()
            max_allowed = category.max_selections if category else 1
            if len(selected_dept_ids) > max_allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"You can only select up to {max_allowed} department(s) from '{category.name}'"
                )

        # Get existing department associations with their statuses
        existing_mds = db.query(MemberDepartment).filter(
            MemberDepartment.member_id == member_id
        ).all()

        # Build a map of existing department_id -> MemberDepartment for status preservation
        existing_map = {md.department_id: md for md in existing_mds}
        existing_dept_ids = set(existing_map.keys())
        new_dept_ids = set(selected)

        # Departments to remove (in existing but not in new selection)
        to_remove = existing_dept_ids - new_dept_ids
        # Departments to add (in new selection but not existing)
        to_add = new_dept_ids - existing_dept_ids
        # Departments to keep (in both) - preserve their status
        to_keep = existing_dept_ids & new_dept_ids

        # Delete only the removed departments
        if to_remove:
            db.query(MemberDepartment).filter(
                MemberDepartment.member_id == member_id,
                MemberDepartment.department_id.in_(to_remove)
            ).delete(synchronize_session=False)

        # Create new associations only for newly added departments (with pending status)
        for dept_id in to_add:
            md = MemberDepartment(
                member_id=member_id,
                department_id=dept_id,
                source="member",
                status="pending"
            )
            db.add(md)

    db.commit()

    return {"success": True, "memberId": member_id}


# ============ SETTINGS ============

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    """Get all settings as key-value pairs"""
    settings = db.query(Settings).all()
    return {s.key: s.value for s in settings}


@router.put("/settings")
def update_setting(data: SettingUpdate, request: Request = None, db: Session = Depends(get_db)):
    """Update or create a setting"""
    if not data.key or data.value is None:
        raise HTTPException(status_code=400, detail="Key and value are required")

    setting = db.query(Settings).filter(Settings.key == data.key).first()
    if setting:
        setting.value = str(data.value)
    else:
        setting = Settings(key=data.key, value=str(data.value))
        db.add(setting)

    # Don't log password values
    safe_value = "****" if "password" in data.key.lower() else str(data.value)
    if request:
        _log_admin_action(request, db, "update_settings", "settings", None, f"Updated {data.key} = {safe_value}")
    db.commit()

    return {"success": True}


# ============ SUBMIT ============

@router.post("/submit")
def submit_form(data: MemberSubmission, request: Request, source: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Submit member department selection form"""
    if source != "desk":
        check_selections_locked(db)

    # Validate required fields
    if not data.full_name or not data.phone or not data.address:
        raise HTTPException(status_code=400, detail="Full name, phone, and address are required")

    # Validate phone format (10 digits)
    if not validate_phone(data.phone):
        raise HTTPException(status_code=400, detail="Phone number must be 10 digits (e.g., 0711234456)")

    if not data.selected_departments or len(data.selected_departments) == 0:
        raise HTTPException(status_code=400, detail="Please select at least one department")

    # Check max departments limit
    max_setting = db.query(Settings).filter(Settings.key == "maxDepartments").first()
    max_departments = int(max_setting.value) if max_setting else 3

    if len(data.selected_departments) > max_departments:
        raise HTTPException(
            status_code=400,
            detail=f"You can only select up to {max_departments} departments"
        )

    # Check per-category selection limits
    departments = db.query(Department).options(
        joinedload(Department.category)
    ).filter(
        Department.id.in_(data.selected_departments)
    ).all()

    # Count selections per category
    category_selections: Dict[int, list] = {}
    for dept in departments:
        if dept.category_id:
            if dept.category_id not in category_selections:
                category_selections[dept.category_id] = []
            category_selections[dept.category_id].append(dept.id)

    # Validate against each category's max_selections
    for category_id, selected_dept_ids in category_selections.items():
        category = db.query(Category).filter(Category.id == category_id).first()
        max_allowed = category.max_selections if category else 1
        if len(selected_dept_ids) > max_allowed:
            raise HTTPException(
                status_code=400,
                detail=f"You can only select up to {max_allowed} department(s) from '{category.name}'"
            )

    # Create member
    member = Member(
        full_name=_title_case_name(data.full_name),
        phone=data.phone,
        email=data.email or "",
        address=data.address
    )
    db.add(member)
    db.flush()

    # Create member-department associations
    dept_names = [d.name for d in departments]
    for dept_id in data.selected_departments:
        md = MemberDepartment(member_id=member.id, department_id=dept_id)
        db.add(md)

    _log_member_action(request, db, member, "submit_selection", "member", member.id,
        f"Selected {len(data.selected_departments)} dept(s): {', '.join(dept_names)}")
    db.commit()
    db.refresh(member)

    return {"success": True, "memberId": member.id}


# ============ SEED ============

@router.api_route("/seed", methods=["GET", "POST"])
def seed_database(db: Session = Depends(get_db)):
    """Initialize database with default data"""
    # Check if already seeded
    existing = db.query(Settings).first()
    if existing:
        return {"message": "Database already seeded", "seeded": False}

    # Create default settings
    db.add(Settings(key="maxDepartments", value="3"))
    db.add(Settings(key="adminPassword", value="admin123"))
    db.add(Settings(key="deskPassword", value="desk123"))
    db.add(Settings(key="resultsPublished", value="false"))
    db.add(Settings(key="publishedAt", value=""))
    db.add(Settings(key="appealWindowOpen", value="false"))
    db.add(Settings(key="selectionYear", value="2026"))

    # Create categories with departments
    music = Category(name="Music Ministry")
    db.add(music)
    db.flush()
    db.add(Department(name="Choir", category_id=music.id))
    db.add(Department(name="Praise Team", category_id=music.id))
    db.add(Department(name="Sound & Media", category_id=music.id))

    children = Category(name="Children's Ministry")
    db.add(children)
    db.flush()
    db.add(Department(name="Sunday School Teachers", category_id=children.id))
    db.add(Department(name="Nursery", category_id=children.id))

    outreach = Category(name="Outreach")
    db.add(outreach)
    db.flush()
    db.add(Department(name="Evangelism Team", category_id=outreach.id))
    db.add(Department(name="Community Service", category_id=outreach.id))

    # Create uncategorized departments
    db.add(Department(name="Ushering"))
    db.add(Department(name="Prayer Team"))
    db.add(Department(name="Hospitality"))

    db.commit()

    return {"message": "Database seeded successfully", "seeded": True}


# ============ STATS ============

@router.get("/stats/departments")
def get_department_stats(db: Session = Depends(get_db)):
    """Get member count per department, grouped by category"""
    # Get all departments with their member counts
    departments = db.query(Department).options(
        joinedload(Department.category)
    ).all()

    # Count members per department
    dept_counts = {}
    member_depts = db.query(MemberDepartment).all()
    for md in member_depts:
        dept_counts[md.department_id] = dept_counts.get(md.department_id, 0) + 1

    # Group by category
    categories_data = {}
    uncategorized = []

    for dept in departments:
        count = dept_counts.get(dept.id, 0)
        dept_info = {
            "id": dept.id,
            "name": dept.name,
            "memberCount": count
        }

        if dept.category_id:
            if dept.category_id not in categories_data:
                categories_data[dept.category_id] = {
                    "id": dept.category.id,
                    "name": dept.category.name,
                    "departments": []
                }
            categories_data[dept.category_id]["departments"].append(dept_info)
        else:
            uncategorized.append(dept_info)

    # Sort departments by member count (descending)
    for cat_id in categories_data:
        categories_data[cat_id]["departments"].sort(key=lambda x: x["memberCount"], reverse=True)
    uncategorized.sort(key=lambda x: x["memberCount"], reverse=True)

    return {
        "categories": list(categories_data.values()),
        "uncategorized": uncategorized
    }


@router.get("/stats/departments/{department_id}")
def get_department_members(department_id: int, db: Session = Depends(get_db)):
    """Get all members who selected a specific department"""
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # Get members who selected this department
    member_depts = db.query(MemberDepartment).filter(
        MemberDepartment.department_id == department_id
    ).all()

    member_ids = [md.member_id for md in member_depts]
    members = db.query(Member).filter(Member.id.in_(member_ids)).order_by(Member.full_name).all()

    return {
        "department": {
            "id": department.id,
            "name": department.name,
            "categoryId": department.category_id
        },
        "members": [
            {
                "id": m.id,
                "fullName": m.full_name,
                "phone": m.phone,
                "email": m.email,
                "address": m.address
            }
            for m in members
        ],
        "totalCount": len(members)
    }


# ============ EXPORT ============

def sanitize_sheet_name(name: str) -> str:
    """Sanitize string for Excel sheet name"""
    # Remove invalid characters
    sanitized = re.sub(r'[\[\]:*?/\\]', '', name)
    # Limit to 31 characters
    return sanitized[:31]


@router.get("/export")
def export_data(
    type: str = Query("department"),
    approved_only: bool = Query(False),
    db: Session = Depends(get_db)
):
    """Export data to Excel file. Use approved_only=true to export only approved selections."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    today = datetime.now().strftime("%Y-%m-%d")
    suffix = "-approved" if approved_only else ""

    if type == "member":
        # Export by member
        ws = wb.active
        ws.title = "Members"

        # Get all departments for headers
        all_departments = db.query(Department).order_by(Department.name).all()
        dept_names = [d.name for d in all_departments]
        dept_ids = [d.id for d in all_departments]

        # Headers
        headers = ["Full Name", "Phone", "Email", "Address", "Submitted On"] + dept_names
        for col, header in enumerate(headers, 1):
            ws.cell(row=1, column=col, value=header)

        # Get all members
        members = db.query(Member).options(
            joinedload(Member.departments)
        ).order_by(Member.created_at.desc()).all()

        for row, member in enumerate(members, 2):
            ws.cell(row=row, column=1, value=member.full_name)
            ws.cell(row=row, column=2, value=member.phone)
            ws.cell(row=row, column=3, value=member.email)
            ws.cell(row=row, column=4, value=member.address)
            ws.cell(row=row, column=5, value=member.created_at.strftime("%Y-%m-%d %H:%M") if member.created_at else "")

            # Filter by status if approved_only
            if approved_only:
                member_dept_ids = {md.department_id for md in member.departments if md.status == "approved"}
            else:
                member_dept_ids = {md.department_id for md in member.departments}

            for i, dept_id in enumerate(dept_ids):
                ws.cell(row=row, column=6 + i, value="Yes" if dept_id in member_dept_ids else "")

        # Set column widths
        for col in range(1, len(headers) + 1):
            ws.column_dimensions[get_column_letter(col)].width = 15

        # Summary sheet
        ws_summary = wb.create_sheet("Summary")
        ws_summary.cell(row=1, column=1, value="Department")
        ws_summary.cell(row=1, column=2, value="Member Count")

        for row, dept in enumerate(all_departments, 2):
            if approved_only:
                count = db.query(MemberDepartment).filter(
                    MemberDepartment.department_id == dept.id,
                    MemberDepartment.status == "approved"
                ).count()
            else:
                count = db.query(MemberDepartment).filter(MemberDepartment.department_id == dept.id).count()
            ws_summary.cell(row=row, column=1, value=dept.name)
            ws_summary.cell(row=row, column=2, value=count)

        filename = f"members-report{suffix}-{today}.xlsx"

    else:
        # Export by department
        departments = db.query(Department).options(
            joinedload(Department.category),
            joinedload(Department.member_departments).joinedload(MemberDepartment.member)
        ).order_by(Department.name).all()

        # Summary sheet
        ws = wb.active
        ws.title = "Summary"
        ws.cell(row=1, column=1, value="Department")
        ws.cell(row=1, column=2, value="Category")
        ws.cell(row=1, column=3, value="Member Count")

        for row, dept in enumerate(departments, 2):
            if approved_only:
                filtered_mds = [md for md in dept.member_departments if md.status == "approved"]
            else:
                filtered_mds = dept.member_departments
            ws.cell(row=row, column=1, value=dept.name)
            ws.cell(row=row, column=2, value=dept.category.name if dept.category else "Uncategorized")
            ws.cell(row=row, column=3, value=len(filtered_mds))

        # One sheet per department
        for dept in departments:
            if approved_only:
                filtered_mds = [md for md in dept.member_departments if md.status == "approved"]
            else:
                filtered_mds = dept.member_departments

            sheet_name = sanitize_sheet_name(dept.name)
            ws_dept = wb.create_sheet(sheet_name)

            # Header info
            ws_dept.cell(row=1, column=1, value="Department:")
            ws_dept.cell(row=1, column=2, value=dept.name)
            ws_dept.cell(row=2, column=1, value="Category:")
            ws_dept.cell(row=2, column=2, value=dept.category.name if dept.category else "Uncategorized")
            ws_dept.cell(row=3, column=1, value="Total Members:")
            ws_dept.cell(row=3, column=2, value=len(filtered_mds))

            # Column headers
            ws_dept.cell(row=5, column=1, value="Full Name")
            ws_dept.cell(row=5, column=2, value="Phone")
            ws_dept.cell(row=5, column=3, value="Email")
            ws_dept.cell(row=5, column=4, value="Address")
            ws_dept.cell(row=5, column=5, value="Submitted On")

            # Members
            for row, md in enumerate(filtered_mds, 6):
                ws_dept.cell(row=row, column=1, value=md.member.full_name)
                ws_dept.cell(row=row, column=2, value=md.member.phone)
                ws_dept.cell(row=row, column=3, value=md.member.email)
                ws_dept.cell(row=row, column=4, value=md.member.address)
                ws_dept.cell(row=row, column=5, value=md.member.created_at.strftime("%Y-%m-%d %H:%M") if md.member.created_at else "")

            # Set column widths
            for col in range(1, 6):
                ws_dept.column_dimensions[get_column_letter(col)].width = 20

        filename = f"departments-report{suffix}-{today}.xlsx"

    # Save to BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============ ADMIN APPROVAL ENDPOINTS ============

@router.get("/admin/reviews")
def get_all_reviews(db: Session = Depends(get_db)):
    """Get all members with their department selections for review"""
    members = db.query(Member).options(
        joinedload(Member.departments).joinedload(MemberDepartment.department).joinedload(Department.category)
    ).order_by(Member.created_at.desc()).all()

    result = []
    for m in members:
        selections = []
        for md in m.departments:
            selections.append({
                "id": md.id,
                "member_id": md.member_id,
                "department_id": md.department_id,
                "department_name": md.department.name,
                "category_name": md.department.category.name if md.department.category else None,
                "source": md.source or "member",
                "status": md.status or "pending",
                "admin_note": md.admin_note,
                "created_at": md.created_at.isoformat() if md.created_at else None,
                "status_changed_at": md.status_changed_at.isoformat() if md.status_changed_at else None,
                "replaced_by_id": md.replaced_by_id
            })

        result.append({
            "id": m.id,
            "full_name": m.full_name,
            "phone": m.phone,
            "email": m.email,
            "address": m.address,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "selections": selections
        })

    return result


@router.put("/admin/reviews/{member_department_id}")
def update_review_status(
    member_department_id: int,
    data: ReviewStatusUpdate,
    request: Request = None,
    db: Session = Depends(get_db)
):
    """Approve or reject a single department selection"""
    md = db.query(MemberDepartment).options(
        joinedload(MemberDepartment.member),
        joinedload(MemberDepartment.department).joinedload(Department.category)
    ).filter(MemberDepartment.id == member_department_id).first()
    if not md:
        raise HTTPException(status_code=404, detail="Selection not found")

    if data.status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    md.status = data.status
    md.admin_note = data.admin_note
    md.status_changed_at = datetime.now()
    action = "review_approve" if data.status == "approved" else "review_reject"
    if request:
        _log_admin_action(request, db, action, "member_department", member_department_id,
            f"{'Approved' if data.status == 'approved' else 'Rejected'} {md.member.full_name} for {md.department.name}")
    db.commit()

    # Dispatch notification
    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        event_type = EventType.MEMBER_APPROVED if data.status == "approved" else EventType.MEMBER_REJECTED
        dispatch_event(db, event_type, {
            "member_id": md.member.id,
            "member_name": md.member.full_name,
            "member_email": md.member.email,
            "department_name": md.department.name,
            "category_name": md.department.category.name if md.department.category else None,
            "admin_note": data.admin_note
        })
    except Exception as e:
        print(f"Failed to dispatch notification: {e}")

    return {"success": True, "id": member_department_id, "status": data.status}


@router.post("/admin/reviews/{member_department_id}/replace")
def replace_department(
    member_department_id: int,
    data: ReplaceDepartmentRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Replace a selection with a different department (reject original, create admin-assigned)"""
    md = db.query(MemberDepartment).filter(MemberDepartment.id == member_department_id).first()
    if not md:
        raise HTTPException(status_code=404, detail="Selection not found")

    # Check new department exists
    new_dept = db.query(Department).filter(Department.id == data.new_department_id).first()
    if not new_dept:
        raise HTTPException(status_code=404, detail="New department not found")

    # Reject the original
    md.status = "rejected"
    md.admin_note = data.admin_note or f"Replaced with {new_dept.name}"
    md.status_changed_at = datetime.now()

    # Create new admin-assigned selection
    new_md = MemberDepartment(
        member_id=md.member_id,
        department_id=data.new_department_id,
        source="admin",
        status="approved",
        admin_note=f"Replacement for {md.department.name}",
        status_changed_at=datetime.now()
    )
    db.add(new_md)
    db.flush()

    # Link original to replacement
    md.replaced_by_id = new_md.id
    db.commit()

    _log_admin_action(request, db, "replace_department", "member_department", member_department_id,
                      f"Replaced '{md.department.name}' with '{new_dept.name}' for member #{md.member_id}")
    db.commit()

    return {"success": True, "original_id": member_department_id, "new_id": new_md.id}


@router.post("/admin/members")
def admin_create_member(data: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Admin: create a new member without requiring department selections"""
    full_name = _title_case_name((data.get("full_name") or "").strip())
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    address = (data.get("address") or "").strip()

    if not full_name or not phone:
        raise HTTPException(status_code=400, detail="Full name and phone are required")

    # Validate phone format (10 digits)
    if not validate_phone(phone):
        raise HTTPException(status_code=400, detail="Phone number must be 10 digits (e.g., 0711234456)")

    # Check if member with same phone and name already exists
    existing = db.query(Member).filter(
        Member.phone == phone,
        func.lower(Member.full_name) == full_name.lower()
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="A member with this name and phone already exists")

    member = Member(
        full_name=full_name,
        phone=phone,
        email=email,
        address=address
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    if request:
        _log_admin_action(request, db, "create_member", "member", member.id, f"Created member '{member.full_name}' ({member.phone})")
        db.commit()

    return {
        "id": member.id,
        "fullName": member.full_name,
        "phone": member.phone,
        "email": member.email,
        "address": member.address
    }


@router.put("/admin/members/{member_id}")
def admin_update_member(member_id: int, data: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Admin: update a member's personal details (name, phone, email, address)"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    if "full_name" in data and data["full_name"].strip():
        member.full_name = _title_case_name(data["full_name"].strip())
    if "phone" in data and data["phone"].strip():
        # Check for phone conflict
        existing = db.query(Member).filter(Member.phone == data["phone"].strip(), Member.id != member_id).first()
        if existing:
            # Allow shared phones (family members)
            pass
        member.phone = data["phone"].strip()
    if "email" in data:
        member.email = (data["email"] or "").strip()
    if "address" in data:
        member.address = (data["address"] or "").strip()

    db.commit()
    db.refresh(member)

    if request:
        _log_admin_action(request, db, "update_member", "member", member_id, f"Updated member '{member.full_name}'")
        db.commit()

    return {
        "id": member.id,
        "full_name": member.full_name,
        "phone": member.phone,
        "email": member.email,
        "address": member.address
    }


@router.delete("/admin/members/{member_id}")
def admin_delete_member(member_id: int, request: Request = None, db: Session = Depends(get_db)):
    """Admin permanently deletes a member and all related records"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    member_name = member.full_name
    if request:
        _log_admin_action(request, db, "delete_member", "member", member_id, f"Permanently deleted member {member_name}")

    # Clear HOD references on departments
    db.query(Department).filter(Department.hod_member_id == member_id).update(
        {"hod_member_id": None}, synchronize_session=False
    )

    # Clear SET NULL references to avoid FK issues with SQLAlchemy
    from models import ServiceProgram, ServiceSchedule, Meeting, NotificationLog, PosterRequest
    db.query(ServiceProgram).filter(ServiceProgram.created_by_member_id == member_id).update(
        {"created_by_member_id": None}, synchronize_session=False
    )
    db.query(ServiceSchedule).filter(ServiceSchedule.service_manager_id == member_id).update(
        {"service_manager_id": None}, synchronize_session=False
    )
    db.query(Meeting).filter(Meeting.created_by_id == member_id).update(
        {"created_by_id": None}, synchronize_session=False
    )
    db.query(NotificationLog).filter(NotificationLog.recipient_id == member_id).update(
        {"recipient_id": None}, synchronize_session=False
    )
    db.query(PosterRequest).filter(PosterRequest.acknowledged_by_id == member_id).update(
        {"acknowledged_by_id": None}, synchronize_session=False
    )

    # Delete the member (CASCADE handles member_departments, appeals, meeting_rsvps, poster_requests)
    db.delete(member)
    db.commit()

    return {"success": True, "message": f"Member '{member_name}' has been permanently deleted"}


@router.post("/admin/members/{member_id}/assign")
def assign_department(
    member_id: int,
    data: AssignDepartmentRequest,
    request: Request = None,
    db: Session = Depends(get_db)
):
    """Admin assigns an additional department to a member"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    dept = db.query(Department).options(
        joinedload(Department.category)
    ).filter(Department.id == data.department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")

    # Check if already assigned
    existing = db.query(MemberDepartment).filter(
        MemberDepartment.member_id == member_id,
        MemberDepartment.department_id == data.department_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Department already assigned to this member")

    # Create admin-assigned selection
    md = MemberDepartment(
        member_id=member_id,
        department_id=data.department_id,
        source="admin",
        status="approved",
        admin_note=data.admin_note,
        status_changed_at=datetime.now()
    )
    db.add(md)
    if request:
        _log_admin_action(request, db, "assign_department", "member", member_id,
            f"Assigned {dept.name} to {member.full_name}")
    db.commit()
    db.refresh(md)

    # Dispatch notification
    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        dispatch_event(db, EventType.DEPARTMENT_ASSIGNED, {
            "member_id": member.id,
            "member_name": member.full_name,
            "member_email": member.email,
            "department_name": dept.name,
            "category_name": dept.category.name if dept.category else None,
            "admin_note": data.admin_note
        })
    except Exception as e:
        print(f"Failed to dispatch notification: {e}")

    return {"success": True, "id": md.id}


@router.post("/admin/reviews/bulk-approve")
def bulk_approve_pending(request: Request = None, db: Session = Depends(get_db)):
    """Approve all pending selections (including null status from before workflow)"""
    count = db.query(MemberDepartment).filter(
        or_(MemberDepartment.status == "pending", MemberDepartment.status.is_(None))
    ).update({
        MemberDepartment.status: "approved",
        MemberDepartment.status_changed_at: datetime.now()
    }, synchronize_session='fetch')
    if request:
        _log_admin_action(request, db, "bulk_approve", "member_department", None, f"Bulk approved {count} pending selections")
    db.commit()

    return {"success": True, "approved_count": count}


# ============ LEADERSHIP ROLES ============

@router.get("/admin/members/{member_id}/leadership-roles")
def get_member_leadership_roles(member_id: int, db: Session = Depends(get_db)):
    """Get leadership roles for a member"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    roles = []
    if member.leadership_roles:
        try:
            roles = json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
        except (ValueError, TypeError):
            roles = []

    # Check if member is HOD of any department
    hod_depts = db.query(Department).filter(Department.hod_member_id == member_id).all()
    is_hod = len(hod_depts) > 0

    return {
        "member_id": member_id,
        "roles": roles,
        "is_hod": is_hod,
        "hod_departments": [{"id": d.id, "name": d.name} for d in hod_depts]
    }


@router.put("/admin/members/{member_id}/leadership-roles")
def update_member_leadership_roles(
    member_id: int,
    roles: List[str] = Body(..., embed=True),
    request: Request = None,
    db: Session = Depends(get_db)
):
    """Update leadership roles for a member (deacon, elder, etc.)"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Validate roles
    valid_roles = ["pastor", "deacon", "elder", "service_manager", "admin", "dr", "mr", "mrs"]  # HOD is derived from departments
    invalid = [r for r in roles if r not in valid_roles]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid roles: {invalid}. Valid roles are: {valid_roles}")

    if request:
        _log_admin_action(request, db, "update_leadership_roles", "member", member_id,
            f"Updated roles for {member.full_name}: {', '.join(roles) if roles else 'none'}")
    member.leadership_roles = json.dumps(roles) if roles else None
    db.commit()

    return {"success": True, "roles": roles}


@router.get("/admin/leadership-roles")
def get_all_leadership_roles(db: Session = Depends(get_db)):
    """Get all members with their leadership roles for management"""
    members = db.query(Member).all()

    result = []
    for member in members:
        roles = []
        if member.leadership_roles:
            try:
                roles = json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
            except (ValueError, TypeError):
                roles = []

        # Check if member is HOD
        hod_depts = db.query(Department).filter(Department.hod_member_id == member.id).all()
        if hod_depts:
            roles = ["hod"] + roles  # HOD comes first

        result.append({
            "id": member.id,
            "full_name": member.full_name,
            "phone": member.phone,
            "email": member.email,
            "roles": roles,
            "hod_departments": [{"id": d.id, "name": d.name} for d in hod_depts]
        })

    # Sort by name
    result.sort(key=lambda x: x["full_name"])

    return {"members": result}


# ============ PUBLISH ENDPOINTS ============

@router.get("/admin/preview")
def preview_publish(db: Session = Depends(get_db)):
    """Preview what members will see after publishing"""
    members = db.query(Member).options(
        joinedload(Member.departments).joinedload(MemberDepartment.department)
    ).all()

    # Include null status as "pending" (for records created before approval workflow)
    pending_count = db.query(MemberDepartment).filter(
        or_(MemberDepartment.status == "pending", MemberDepartment.status.is_(None))
    ).count()
    total_approved = db.query(MemberDepartment).filter(MemberDepartment.status == "approved").count()

    members_preview = []
    for m in members:
        approved_depts = [
            md.department.name
            for md in m.departments
            if md.status == "approved"
        ]
        if approved_depts:
            members_preview.append({
                "id": m.id,
                "full_name": m.full_name,
                "phone": m.phone,
                "approved_departments": approved_depts
            })

    return {
        "total_members": len(members_preview),
        "total_approved_assignments": total_approved,
        "pending_count": pending_count,
        "members_preview": members_preview
    }


@router.post("/admin/publish")
def publish_results(request: Request = None, db: Session = Depends(get_db)):
    """Publish results - make approved selections visible to members"""
    if request:
        _log_admin_action(request, db, "publish_results", "settings", None, "Published department selection results")
    now = datetime.now().isoformat()

    # Update resultsPublished setting
    setting = db.query(Settings).filter(Settings.key == "resultsPublished").first()
    if setting:
        setting.value = "true"
    else:
        db.add(Settings(key="resultsPublished", value="true"))

    # Update publishedAt
    pub_setting = db.query(Settings).filter(Settings.key == "publishedAt").first()
    if pub_setting:
        pub_setting.value = now
    else:
        db.add(Settings(key="publishedAt", value=now))

    db.commit()

    # Dispatch notification to all members with approved selections
    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        # Get all members with at least one approved selection
        members_with_approved = db.query(Member).join(MemberDepartment).filter(
            MemberDepartment.status == "approved"
        ).distinct().all()

        # Get year
        year_setting = db.query(Settings).filter(Settings.key == "selectionYear").first()
        year = year_setting.value if year_setting else "2026"

        # Build recipients list
        recipients = [
            {"id": m.id, "email": m.email, "phone": m.phone}
            for m in members_with_approved
            if m.email  # Only include members with email
        ]

        if recipients:
            dispatch_event(db, EventType.RESULTS_PUBLISHED, {
                "year": year,
                "recipients": recipients
            })
    except Exception as e:
        print(f"Failed to dispatch notification: {e}")

    return {"success": True, "published_at": now}


@router.post("/admin/unpublish")
def unpublish_results(request: Request = None, db: Session = Depends(get_db)):
    """Unpublish results - hide from members"""
    if request:
        _log_admin_action(request, db, "unpublish_results", "settings", None, "Unpublished department selection results")
    setting = db.query(Settings).filter(Settings.key == "resultsPublished").first()
    if setting:
        setting.value = "false"
    db.commit()

    return {"success": True}


# ============ MEMBER RESULTS ENDPOINT ============

@router.get("/results")
def get_member_results(phone: str = Query(...), db: Session = Depends(get_db)):
    """Lookup results by phone number - returns all members with that phone (for families)"""
    # Check if results are published
    pub_setting = db.query(Settings).filter(Settings.key == "resultsPublished").first()
    is_published = pub_setting and pub_setting.value == "true"

    # Check appeal window
    appeal_setting = db.query(Settings).filter(Settings.key == "appealWindowOpen").first()
    appeal_open = appeal_setting and appeal_setting.value == "true"

    # Get year
    year_setting = db.query(Settings).filter(Settings.key == "selectionYear").first()
    year = year_setting.value if year_setting else "2026"

    # Normalize phone
    normalized = phone.strip().replace(" ", "").replace("-", "")

    # Find ALL members with this phone number (family members)
    all_members = db.query(Member).options(
        joinedload(Member.departments).joinedload(MemberDepartment.department).joinedload(Department.category)
    ).all()

    matching_members = []
    for m in all_members:
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            matching_members.append(m)

    if not matching_members:
        return {
            "published": is_published,
            "appeal_window_open": appeal_open,
            "year": year,
            "members": [],
            "message": "No submission found for this phone number."
        }

    # Build member data with all their selections
    members_data = []
    for member in matching_members:
        # Get all department selections with full status info
        selections = []
        for md in member.departments:
            selections.append({
                "id": md.id,
                "department_id": md.department.id,
                "department_name": md.department.name,
                "category_name": md.department.category.name if md.department.category else None,
                "status": md.status or "pending",
                "source": md.source or "member",  # "member" or "admin"
                "admin_note": md.admin_note,
                "created_at": md.created_at.isoformat() if md.created_at else None
            })

        # Separate by status for convenience
        approved = [s for s in selections if s["status"] == "approved"]
        pending = [s for s in selections if s["status"] == "pending" or s["status"] is None]
        rejected = [s for s in selections if s["status"] == "rejected"]

        # Check for admin-added departments that haven't been accepted (source=admin)
        admin_added = [s for s in selections if s["source"] == "admin"]

        # Parse leadership roles
        member_leadership_roles = []
        if member.leadership_roles:
            try:
                member_leadership_roles = json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
            except (ValueError, TypeError):
                member_leadership_roles = []

        members_data.append({
            "id": member.id,
            "full_name": member.full_name,
            "email": member.email,
            "leadership_roles": member_leadership_roles,
            "all_selections": selections,
            "approved_departments": approved,
            "pending_departments": pending,
            "rejected_departments": rejected,
            "admin_added_departments": admin_added
        })

    return {
        "published": is_published,
        "appeal_window_open": appeal_open,
        "year": year,
        "members": members_data,
        "is_family": len(members_data) > 1
    }


@router.post("/results/accept/{member_department_id}")
def accept_admin_assignment(
    member_department_id: int,
    request: Request,
    phone: str = Query(...),
    db: Session = Depends(get_db)
):
    """Member accepts an admin-added department assignment"""
    # Find the member department
    md = db.query(MemberDepartment).options(
        joinedload(MemberDepartment.member)
    ).filter(MemberDepartment.id == member_department_id).first()

    if not md:
        raise HTTPException(status_code=404, detail="Selection not found")

    # Verify phone matches
    normalized_input = phone.strip().replace(" ", "").replace("-", "")
    normalized_member = md.member.phone.strip().replace(" ", "").replace("-", "")

    if normalized_input != normalized_member:
        raise HTTPException(status_code=403, detail="Phone number does not match")

    # Only allow accepting admin-added departments
    if md.source != "admin":
        raise HTTPException(status_code=400, detail="This selection was not added by admin")

    # Mark as accepted by updating source to "accepted" or adding a note
    md.admin_note = (md.admin_note or "") + " [Accepted by member]"
    md.status_changed_at = datetime.now()
    db.commit()

    _log_member_action(request, db, md.member, "accept_assignment", "member_department", member_department_id,
                       f"Accepted admin-assigned department")
    db.commit()

    return {"success": True, "message": "Department assignment accepted"}


# ============ APPEAL ENDPOINTS ============

@router.post("/appeals")
def submit_appeal(data: AppealCreate, request: Request, db: Session = Depends(get_db)):
    """Submit an appeal (public endpoint)"""
    # Check results are published
    pub_setting = db.query(Settings).filter(Settings.key == "resultsPublished").first()
    if not pub_setting or pub_setting.value != "true":
        raise HTTPException(status_code=400, detail="Results are not yet published")

    # Check appeal window is open
    appeal_setting = db.query(Settings).filter(Settings.key == "appealWindowOpen").first()
    if not appeal_setting or appeal_setting.value != "true":
        raise HTTPException(status_code=400, detail="Appeal window is currently closed")

    # Find member - by ID if provided (for families/info desk), otherwise by phone
    member = None
    if data.member_id:
        member = db.query(Member).filter(Member.id == data.member_id).first()
        # Verify phone matches for security
        if member:
            normalized_input = data.phone.strip().replace(" ", "").replace("-", "")
            normalized_member = member.phone.strip().replace(" ", "").replace("-", "")
            if normalized_input != normalized_member:
                raise HTTPException(status_code=403, detail="Phone number does not match member")
    else:
        # Find member by phone only
        normalized = data.phone.strip().replace(" ", "").replace("-", "")
        members = db.query(Member).all()
        for m in members:
            m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
            if m_normalized == normalized or m.phone == data.phone:
                member = m
                break

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Get department names for notification
    unwanted_dept = None
    wanted_dept = None
    if data.unwanted_department_id:
        unwanted_dept = db.query(Department).filter(Department.id == data.unwanted_department_id).first()
    if data.wanted_department_id:
        wanted_dept = db.query(Department).filter(Department.id == data.wanted_department_id).first()

    # Create appeal
    appeal = Appeal(
        member_id=member.id,
        unwanted_department_id=data.unwanted_department_id,
        wanted_department_id=data.wanted_department_id,
        reason=data.reason,
        status="pending"
    )
    db.add(appeal)
    db.flush()
    unwanted_name = unwanted_dept.name if unwanted_dept else "none"
    wanted_name = wanted_dept.name if wanted_dept else "none"
    _log_member_action(request, db, member, "submit_appeal", "appeal", appeal.id,
        f"Appeal: remove {unwanted_name}, want {wanted_name}")
    db.commit()
    db.refresh(appeal)

    # Dispatch notification to admin
    try:
        from notifications.dispatcher import dispatch_to_admins
        from notifications.events import EventType

        dispatch_to_admins(db, EventType.APPEAL_SUBMITTED, {
            "appeal_id": appeal.id,
            "member_name": member.full_name,
            "member_phone": member.phone,
            "unwanted_department": unwanted_dept.name if unwanted_dept else None,
            "wanted_department": wanted_dept.name if wanted_dept else None,
            "reason": data.reason
        })
    except Exception as e:
        print(f"Failed to dispatch notification: {e}")

    return {"success": True, "appeal_id": appeal.id}


@router.get("/admin/appeals")
def get_all_appeals(db: Session = Depends(get_db)):
    """Get all appeals for admin review"""
    appeals = db.query(Appeal).options(
        joinedload(Appeal.member),
        joinedload(Appeal.unwanted_department),
        joinedload(Appeal.wanted_department)
    ).order_by(Appeal.created_at.desc()).all()

    return [
        {
            "id": a.id,
            "member_id": a.member_id,
            "member_name": a.member.full_name,
            "member_phone": a.member.phone,
            "unwanted_department_id": a.unwanted_department_id,
            "unwanted_department_name": a.unwanted_department.name if a.unwanted_department else None,
            "wanted_department_id": a.wanted_department_id,
            "wanted_department_name": a.wanted_department.name if a.wanted_department else None,
            "reason": a.reason,
            "status": a.status,
            "admin_response": a.admin_response,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None
        }
        for a in appeals
    ]


@router.put("/admin/appeals/{appeal_id}")
def resolve_appeal(
    appeal_id: int,
    data: AppealResolve,
    request: Request = None,
    db: Session = Depends(get_db)
):
    """Resolve an appeal (approve or reject)"""
    appeal = db.query(Appeal).options(
        joinedload(Appeal.member),
        joinedload(Appeal.unwanted_department),
        joinedload(Appeal.wanted_department)
    ).filter(Appeal.id == appeal_id).first()
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")

    if data.status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    appeal.status = data.status
    appeal.admin_response = data.admin_response
    appeal.resolved_at = datetime.now()
    if request:
        _log_admin_action(request, db, "resolve_appeal", "appeal", appeal_id,
            f"{'Approved' if data.status == 'approved' else 'Rejected'} appeal from {appeal.member.full_name}")

    # If approved, update the member's departments
    if data.status == "approved":
        # Remove unwanted department if specified
        if appeal.unwanted_department_id:
            md = db.query(MemberDepartment).filter(
                MemberDepartment.member_id == appeal.member_id,
                MemberDepartment.department_id == appeal.unwanted_department_id,
                MemberDepartment.status == "approved"
            ).first()
            if md:
                md.status = "rejected"
                md.admin_note = "Removed via approved appeal"
                md.status_changed_at = datetime.now()

        # Add wanted department if specified
        if appeal.wanted_department_id:
            # Check not already assigned
            existing = db.query(MemberDepartment).filter(
                MemberDepartment.member_id == appeal.member_id,
                MemberDepartment.department_id == appeal.wanted_department_id
            ).first()
            if not existing:
                new_md = MemberDepartment(
                    member_id=appeal.member_id,
                    department_id=appeal.wanted_department_id,
                    source="admin",
                    status="approved",
                    admin_note="Added via approved appeal",
                    status_changed_at=datetime.now()
                )
                db.add(new_md)

    db.commit()

    # Dispatch notification to member
    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        dispatch_event(db, EventType.APPEAL_RESOLVED, {
            "member_id": appeal.member.id,
            "member_name": appeal.member.full_name,
            "member_email": appeal.member.email,
            "status": data.status,
            "unwanted_department": appeal.unwanted_department.name if appeal.unwanted_department else None,
            "wanted_department": appeal.wanted_department.name if appeal.wanted_department else None,
            "admin_response": data.admin_response
        })
    except Exception as e:
        print(f"Failed to dispatch notification: {e}")

    return {"success": True, "appeal_id": appeal_id, "status": data.status}


@router.post("/admin/appeals/window")
def toggle_appeal_window(open: bool = Query(...), request: Request = None, db: Session = Depends(get_db)):
    """Open or close the appeal window"""
    setting = db.query(Settings).filter(Settings.key == "appealWindowOpen").first()
    if setting:
        setting.value = "true" if open else "false"
    else:
        db.add(Settings(key="appealWindowOpen", value="true" if open else "false"))

    if request:
        _log_admin_action(request, db, "toggle_appeal_window", "settings", None,
            f"{'Opened' if open else 'Closed'} the appeal window")
    db.commit()

    return {"success": True, "appeal_window_open": open}


# ============ HOD / ELDER ENDPOINTS ============


def _find_member_by_phone(db: Session, phone: str):
    """Find a member by phone number (normalized)"""
    normalized = phone.strip().replace(" ", "").replace("-", "")
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            return m
    return None


def _is_elder(member: "Member") -> bool:
    """Check if a member has the elder or pastor leadership role"""
    roles = member.leadership_roles or []
    return "elder" in roles or "pastor" in roles


def _get_accessible_departments(db: Session, member: "Member"):
    """Get departments a member can manage meetings for (HOD depts + all depts if elder)"""
    if _is_elder(member):
        # Elders can access all departments
        return db.query(Department).options(
            joinedload(Department.category),
            joinedload(Department.member_departments).joinedload(MemberDepartment.member)
        ).order_by(Department.name).all()
    else:
        # HODs can only access their own departments
        return db.query(Department).options(
            joinedload(Department.category),
            joinedload(Department.member_departments).joinedload(MemberDepartment.member)
        ).filter(Department.hod_member_id == member.id).order_by(Department.name).all()


def _can_manage_department(db: Session, member: "Member", department_id: int) -> bool:
    """Check if member can manage a department (is HOD of it or is an Elder)"""
    if _is_elder(member):
        return True
    department = db.query(Department).filter(Department.id == department_id).first()
    return department and department.hod_member_id == member.id

@router.post("/admin/departments/{department_id}/set-hod")
def set_department_hod(
    department_id: int,
    data: SetHODRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Assign a member as Head of Department"""
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    member = db.query(Member).filter(Member.id == data.member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    department.hod_member_id = member.id
    db.commit()

    _log_admin_action(request, db, "set_hod", "department", department_id, f"Set {member.full_name} as HOD of '{department.name}'")
    db.commit()

    return {
        "success": True,
        "message": "HOD assigned",
        "department": department.name,
        "hod_name": member.full_name
    }


@router.delete("/admin/departments/{department_id}/remove-hod")
def remove_department_hod(
    department_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Remove the HOD assignment from a department"""
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    department.hod_member_id = None
    db.commit()

    _log_admin_action(request, db, "remove_hod", "department", department_id, f"Removed HOD from '{department.name}'")
    db.commit()

    return {"success": True, "message": "HOD removed"}


@router.get("/hod/departments")
def get_hod_departments(phone: str = Query(...), db: Session = Depends(get_db)):
    """Get departments where this member is HOD, with member lists and statuses"""
    hod_member = _find_member_by_phone(db, phone)
    if not hod_member:
        raise HTTPException(status_code=404, detail="Member not found")

    is_elder = _is_elder(hod_member)

    # Always show only departments where member is actual HOD (not all for elders)
    # Elder access to all departments is only for meeting booking
    departments = db.query(Department).options(
        joinedload(Department.category),
        joinedload(Department.member_departments).joinedload(MemberDepartment.member)
    ).filter(Department.hod_member_id == hod_member.id).order_by(Department.name).all()

    if not departments:
        return {
            "hod_name": hod_member.full_name,
            "is_elder": is_elder,
            "role": "pastor" if "pastor" in (hod_member.leadership_roles or []) else ("elder" if is_elder else "hod"),
            "departments": []
        }

    dept_views = []
    for dept in departments:
        members_list = []
        approved_count = 0
        pending_count = 0
        rejected_count = 0

        for md in dept.member_departments:
            status = md.status or "pending"
            if status == "approved":
                approved_count += 1
            elif status == "rejected":
                rejected_count += 1
            else:
                pending_count += 1

            members_list.append({
                "id": md.member.id,
                "full_name": md.member.full_name,
                "phone": md.member.phone,
                "email": md.member.email,
                "status": status,
                "source": md.source or "member",
                "created_at": md.created_at.isoformat() if md.created_at else None
            })

        # Sort members: pending first, then approved, then rejected
        status_order = {"pending": 0, "approved": 1, "rejected": 2}
        members_list.sort(key=lambda x: (status_order.get(x["status"], 3), x["full_name"]))

        dept_views.append({
            "id": dept.id,
            "name": dept.name,
            "category_name": dept.category.name if dept.category else None,
            "members": members_list,
            "total_members": len(members_list),
            "approved_count": approved_count,
            "pending_count": pending_count,
            "rejected_count": rejected_count
        })

    return {
        "hod_name": hod_member.full_name,
        "is_elder": is_elder,
        "role": "pastor" if "pastor" in (hod_member.leadership_roles or []) else ("elder" if is_elder else "hod"),
        "departments": dept_views
    }


# ============ MEETING ENDPOINTS ============

def slot_to_time(slot: int) -> str:
    """Convert slot number (0-47) to HH:MM format"""
    hours = slot // 2
    minutes = (slot % 2) * 30
    return f"{hours:02d}:{minutes:02d}"


def get_week_dates(reference_date: date) -> Tuple[date, date]:
    """Get Monday and Sunday of the week containing reference_date"""
    # Get Monday of the week (weekday 0 = Monday)
    monday = reference_date - timedelta(days=reference_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def generate_recurring_dates(start_date: date, recurrence: str, end_date: date) -> List[date]:
    """
    Generate a list of dates for recurring meetings.

    Args:
        start_date: The first meeting date
        recurrence: One of 'daily', 'weekly', 'biweekly', 'monthly'
        end_date: The last possible date for meetings

    Returns:
        List of dates including the start_date
    """
    dates = [start_date]
    current = start_date

    # Limit to prevent runaway loops (max 52 occurrences)
    max_occurrences = 52

    while len(dates) < max_occurrences:
        if recurrence == 'daily':
            current = current + timedelta(days=1)
        elif recurrence == 'weekly':
            current = current + timedelta(weeks=1)
        elif recurrence == 'biweekly':
            current = current + timedelta(weeks=2)
        elif recurrence == 'monthly':
            # Add one month (handle month boundaries)
            month = current.month + 1
            year = current.year
            if month > 12:
                month = 1
                year += 1
            # Handle day overflow (e.g., Jan 31 -> Feb 28)
            day = min(current.day, 28)  # Safe for all months
            try:
                current = date(year, month, day)
            except ValueError:
                current = date(year, month, 28)
        else:
            break

        if current > end_date:
            break

        dates.append(current)

    return dates


def check_slot_availability(
    db: Session,
    department_id: int,
    meeting_date: date,
    start_slot: int,
    end_slot: int,
    exclude_meeting_id: Optional[int] = None
) -> Tuple[bool, Optional[str]]:
    """Check if time slots are available for a department's meeting.
    Returns (available, conflict_reason)"""

    # Find overlapping meetings on the same date
    query = db.query(Meeting).filter(
        Meeting.meeting_date == meeting_date,
        Meeting.start_slot < end_slot,
        Meeting.end_slot > start_slot
    )
    if exclude_meeting_id:
        query = query.filter(Meeting.id != exclude_meeting_id)

    conflicts = query.all()

    for meeting in conflicts:
        # Same department can't have overlapping meetings
        if meeting.department_id == department_id:
            return False, f"You already have a meeting scheduled at this time"

        # Get approved members of the booking department
        dept_members = set(
            md.member_id for md in db.query(MemberDepartment)
            .filter(
                MemberDepartment.department_id == department_id,
                MemberDepartment.status == "approved"
            ).all()
        )

        # Get approved members of the conflicting meeting's department
        other_members = set(
            md.member_id for md in db.query(MemberDepartment)
            .filter(
                MemberDepartment.department_id == meeting.department_id,
                MemberDepartment.status == "approved"
            ).all()
        )

        # Check for member intersection
        if dept_members & other_members:
            dept = db.query(Department).filter(Department.id == meeting.department_id).first()
            return False, f"Conflict with {dept.name if dept else 'another department'} - shared members"

    return True, None


def find_duplicate_meeting(
    db: Session,
    title: str,
    meeting_date: date,
    start_slot: int,
    end_slot: int,
    department_id: Optional[int] = None,
    target_department_ids: Optional[str] = None,
    target_member_ids: Optional[str] = None,
    target_leadership_roles: Optional[str] = None,
    is_general: int = 0,
    window_seconds: int = 120
) -> Optional[Meeting]:
    """Detect recent duplicate meeting.

    Returns an existing meeting created in the last `window_seconds` that matches
    on the dimensions that define a calendar event: title, date, slot range, and
    the target (department / multi-dept / individuals / roles / general). This
    catches double-click submissions and network retries without blocking
    legitimate identical meetings scheduled far apart.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)

    q = db.query(Meeting).filter(
        Meeting.title == title,
        Meeting.meeting_date == meeting_date,
        Meeting.start_slot == start_slot,
        Meeting.end_slot == end_slot,
        Meeting.created_at >= cutoff,
    )

    # Match on targeting dimension (all these fields are nullable in the schema)
    if department_id is not None:
        q = q.filter(Meeting.department_id == department_id)
    else:
        q = q.filter(Meeting.department_id.is_(None))

    if target_department_ids is not None:
        q = q.filter(Meeting.target_department_ids == target_department_ids)
    else:
        q = q.filter(Meeting.target_department_ids.is_(None))

    if target_member_ids is not None:
        q = q.filter(Meeting.target_member_ids == target_member_ids)
    else:
        q = q.filter(Meeting.target_member_ids.is_(None))

    if target_leadership_roles is not None:
        q = q.filter(Meeting.target_leadership_roles == target_leadership_roles)
    else:
        q = q.filter(Meeting.target_leadership_roles.is_(None))

    q = q.filter(Meeting.is_general == is_general)

    return q.order_by(Meeting.created_at.desc()).first()


def format_meeting_response(meeting: Meeting, db: Session, member_id: Optional[int] = None) -> dict:
    """Format a meeting for API response"""
    # Get RSVP counts
    rsvp_count = db.query(MeetingRSVP).filter(MeetingRSVP.meeting_id == meeting.id).count()
    attending_count = db.query(MeetingRSVP).filter(
        MeetingRSVP.meeting_id == meeting.id,
        MeetingRSVP.response == "attending"
    ).count()

    # Get member's RSVP if member_id provided
    my_rsvp = None
    if member_id:
        rsvp = db.query(MeetingRSVP).filter(
            MeetingRSVP.meeting_id == meeting.id,
            MeetingRSVP.member_id == member_id
        ).first()
        if rsvp:
            my_rsvp = rsvp.response

    # Parse target department IDs and get names
    target_dept_ids = None
    target_dept_names = None
    if meeting.target_department_ids:
        try:
            target_dept_ids = json.loads(meeting.target_department_ids) if isinstance(meeting.target_department_ids, str) else meeting.target_department_ids
            depts = db.query(Department).filter(Department.id.in_(target_dept_ids)).all()
            target_dept_names = [d.name for d in depts]
        except (ValueError, TypeError):
            pass

    # Parse target member IDs and get count
    target_member_ids = None
    target_member_count = 0
    if meeting.target_member_ids:
        try:
            target_member_ids = json.loads(meeting.target_member_ids) if isinstance(meeting.target_member_ids, str) else meeting.target_member_ids
            target_member_count = len(target_member_ids) if target_member_ids else 0
        except (ValueError, TypeError):
            pass

    # Parse target leadership roles
    target_leadership_roles = None
    if meeting.target_leadership_roles:
        try:
            target_leadership_roles = json.loads(meeting.target_leadership_roles) if isinstance(meeting.target_leadership_roles, str) else meeting.target_leadership_roles
        except (ValueError, TypeError):
            pass

    # Determine department name for display
    if meeting.is_general:
        dept_name = "All Leaders"
    elif target_leadership_roles:
        role_labels = {"hod": "HODs", "pastor": "Pastors", "deacon": "Deacons", "elder": "Elders", "service_manager": "Service Managers"}
        dept_name = ", ".join([role_labels.get(r, r.title()) for r in target_leadership_roles])
    elif target_member_ids:
        dept_name = f"{target_member_count} Selected Members"
    elif target_dept_ids:
        dept_name = ", ".join(target_dept_names) if target_dept_names else "Multiple Departments"
    elif meeting.department:
        dept_name = meeting.department.name
    else:
        dept_name = None

    return {
        "id": meeting.id,
        "department_id": meeting.department_id,
        "department_name": dept_name,
        "title": meeting.title,
        "description": meeting.description,
        "meeting_date": meeting.meeting_date.isoformat() if meeting.meeting_date else None,
        "start_slot": meeting.start_slot,
        "end_slot": meeting.end_slot,
        "start_time": slot_to_time(meeting.start_slot),
        "end_time": slot_to_time(meeting.end_slot),
        "location": meeting.location,
        "meeting_link": meeting.meeting_link,
        "created_by_id": meeting.created_by_id,
        "created_by_name": meeting.created_by.full_name if meeting.created_by else None,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
        "rsvp_count": rsvp_count,
        "attending_count": attending_count,
        "my_rsvp": my_rsvp,
        "is_general": bool(meeting.is_general),
        "target_department_ids": target_dept_ids,
        "target_department_names": target_dept_names,
        "target_leadership_roles": target_leadership_roles,
        "recurrence_group_id": meeting.recurrence_group_id
    }


# --- HOD Meeting Endpoints ---

@router.get("/hod/meetings")
def get_hod_meetings(phone: str = Query(...), db: Session = Depends(get_db)):
    """Get all meetings for departments where this member is HOD or Elder"""
    hod_member = _find_member_by_phone(db, phone)
    if not hod_member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Get accessible department IDs
    departments = _get_accessible_departments(db, hod_member)
    dept_ids = [d.id for d in departments]

    if not dept_ids:
        return {"meetings": [], "total": 0}

    # Get meetings for these departments
    meetings = db.query(Meeting).options(
        joinedload(Meeting.department),
        joinedload(Meeting.created_by)
    ).filter(
        Meeting.department_id.in_(dept_ids),
        Meeting.meeting_date >= date.today()
    ).order_by(Meeting.meeting_date, Meeting.start_slot).all()

    return {
        "meetings": [format_meeting_response(m, db) for m in meetings],
        "total": len(meetings)
    }


@router.get("/hod/calendar")
def get_hod_calendar(
    phone: str = Query(...),
    department_id: int = Query(...),
    week_date: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Get weekly calendar with availability for a department"""
    hod_member = _find_member_by_phone(db, phone)
    if not hod_member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Verify HOD/Elder access to this department
    if not _can_manage_department(db, hod_member, department_id):
        raise HTTPException(status_code=403, detail="You do not have access to this department")

    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # Parse week date or use today
    if week_date:
        try:
            ref_date = date.fromisoformat(week_date)
        except ValueError:
            ref_date = date.today()
    else:
        ref_date = date.today()

    week_start, week_end = get_week_dates(ref_date)

    # Get all meetings for this week
    all_meetings = db.query(Meeting).options(
        joinedload(Meeting.department),
        joinedload(Meeting.created_by)
    ).filter(
        Meeting.meeting_date >= week_start,
        Meeting.meeting_date <= week_end
    ).all()

    # Build calendar
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days = []

    for day_offset in range(7):
        current_date = week_start + timedelta(days=day_offset)
        day_meetings = [m for m in all_meetings if m.meeting_date == current_date]

        # Build slots (showing 6 AM to 10 PM = slots 12 to 44)
        slots = []
        for slot in range(12, 44):  # 6:00 to 22:00
            slot_time = slot_to_time(slot)

            # Check if this slot is taken by any meeting for this department
            dept_meeting = None
            for m in day_meetings:
                if m.department_id == department_id and m.start_slot <= slot < m.end_slot:
                    dept_meeting = m
                    break

            if dept_meeting:
                slots.append({
                    "slot": slot,
                    "time": slot_time,
                    "available": False,
                    "meeting": format_meeting_response(dept_meeting, db),
                    "conflict_reason": None
                })
            else:
                # Check availability (conflicts with other departments)
                available, conflict_reason = check_slot_availability(
                    db, department_id, current_date, slot, slot + 1
                )
                slots.append({
                    "slot": slot,
                    "time": slot_time,
                    "available": available,
                    "meeting": None,
                    "conflict_reason": conflict_reason
                })

        days.append({
            "date": current_date.isoformat(),
            "day_name": day_names[day_offset],
            "slots": slots
        })

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "department_id": department_id,
        "department_name": department.name,
        "days": days
    }


@router.post("/hod/meetings")
def create_hod_meeting(
    data: MeetingCreate,
    request: Request,
    phone: str = Query(...),
    db: Session = Depends(get_db)
):
    """Create a meeting (HOD or Elder) - supports recurring meetings"""
    hod_member = _find_member_by_phone(db, phone)
    if not hod_member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Verify HOD/Elder access
    department = db.query(Department).filter(Department.id == data.department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    if not _can_manage_department(db, hod_member, data.department_id):
        raise HTTPException(status_code=403, detail="You do not have access to this department")

    # Validate slots
    if data.start_slot < 0 or data.end_slot > 48 or data.start_slot >= data.end_slot:
        raise HTTPException(status_code=400, detail="Invalid time slots")

    # Generate meeting dates (single or recurring)
    meeting_dates = [data.meeting_date]
    recurrence_group_id = None

    if data.recurrence and data.recurrence != 'none' and data.recurrence_end_date:
        if data.recurrence_end_date <= data.meeting_date:
            raise HTTPException(status_code=400, detail="Recurrence end date must be after meeting date")

        meeting_dates = generate_recurring_dates(data.meeting_date, data.recurrence, data.recurrence_end_date)
        recurrence_group_id = str(uuid.uuid4())

    # Check availability for all dates
    conflicts = []
    for mtg_date in meeting_dates:
        available, conflict_reason = check_slot_availability(
            db, data.department_id, mtg_date, data.start_slot, data.end_slot
        )
        if not available:
            conflicts.append(f"{mtg_date.isoformat()}: {conflict_reason}")

    if conflicts:
        if len(conflicts) == 1:
            raise HTTPException(status_code=409, detail=conflicts[0])
        else:
            raise HTTPException(status_code=409, detail=f"Conflicts found: {'; '.join(conflicts[:3])}" +
                                (f" and {len(conflicts) - 3} more" if len(conflicts) > 3 else ""))

    # Idempotency: detect a recent duplicate of the anchor meeting (double-click,
    # network retry). Returns the existing series instead of creating another one.
    existing_dup = find_duplicate_meeting(
        db,
        title=data.title,
        meeting_date=data.meeting_date,
        start_slot=data.start_slot,
        end_slot=data.end_slot,
        department_id=data.department_id,
        is_general=0,
    )
    if existing_dup:
        existing_full = db.query(Meeting).options(
            joinedload(Meeting.department),
            joinedload(Meeting.created_by)
        ).filter(Meeting.id == existing_dup.id).first()
        occurrence_count = 1
        if existing_dup.recurrence_group_id:
            occurrence_count = db.query(Meeting).filter(
                Meeting.recurrence_group_id == existing_dup.recurrence_group_id
            ).count()
        return {
            "success": True,
            "meeting": format_meeting_response(existing_full, db, member_id=hod_member.id),
            "meetings_created": occurrence_count,
            "recurrence_group_id": existing_dup.recurrence_group_id,
            "duplicate_suppressed": True,
        }

    # Create meetings for all dates
    created_meetings = []
    for mtg_date in meeting_dates:
        meeting = Meeting(
            department_id=data.department_id,
            created_by_id=hod_member.id,
            title=data.title,
            description=data.description,
            meeting_date=mtg_date,
            start_slot=data.start_slot,
            end_slot=data.end_slot,
            location=data.location,
            meeting_link=data.meeting_link,
            recurrence_group_id=recurrence_group_id
        )
        db.add(meeting)
        created_meetings.append(meeting)

    db.commit()

    # Refresh all meetings
    for meeting in created_meetings:
        db.refresh(meeting)

    # Reload first meeting with relationships
    first_meeting = db.query(Meeting).options(
        joinedload(Meeting.department),
        joinedload(Meeting.created_by)
    ).filter(Meeting.id == created_meetings[0].id).first()

    # Dispatch notification to department members and HOD
    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        # Get approved members of this department
        dept_members = db.query(Member).join(MemberDepartment).filter(
            MemberDepartment.department_id == data.department_id,
            MemberDepartment.status == "approved"
        ).all()

        member_ids = set(m.id for m in dept_members)
        recipients = [{"id": m.id, "name": m.full_name, "email": m.email, "phone": m.phone} for m in dept_members if m.email]

        # Include HOD if not already in recipients
        if department.hod_member_id and department.hod_member_id not in member_ids:
            hod = db.query(Member).filter(Member.id == department.hod_member_id).first()
            if hod and hod.email:
                recipients.append({"id": hod.id, "name": hod.full_name, "email": hod.email, "phone": hod.phone})

        if recipients:
            recurrence_info = None
            if len(created_meetings) > 1:
                recurrence_info = f"This is a recurring meeting ({len(created_meetings)} occurrences until {meeting_dates[-1].isoformat()})"

            dispatch_event(db, EventType.MEETING_CREATED, {
                "meeting_id": first_meeting.id,
                "title": first_meeting.title,
                "description": first_meeting.description,
                "meeting_date": first_meeting.meeting_date.isoformat() if first_meeting.meeting_date else None,
                "start_time": slot_to_time(first_meeting.start_slot),
                "end_time": slot_to_time(first_meeting.end_slot),
                "location": first_meeting.location,
                "meeting_link": first_meeting.meeting_link,
                "department_name": department.name,
                "recurrence_info": recurrence_info,
                "recipients": recipients
            })
    except Exception as e:
        print(f"Failed to dispatch notification: {e}")

    _log_member_action(request, db, hod_member, "create_meeting", "meeting", created_meetings[0].id,
                       f"Created meeting '{data.title}' for {department.name} ({len(created_meetings)} occurrence(s))")
    db.commit()

    return {
        "success": True,
        "meeting": format_meeting_response(first_meeting, db),
        "meetings_created": len(created_meetings),
        "recurrence_group_id": recurrence_group_id
    }


@router.put("/hod/meetings/{meeting_id}")
def update_hod_meeting(
    meeting_id: int,
    data: MeetingUpdate,
    request: Request,
    phone: str = Query(...),
    db: Session = Depends(get_db)
):
    """Update a meeting (HOD who created it or Elder)"""
    hod_member = _find_member_by_phone(db, phone)
    if not hod_member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Find meeting
    meeting = db.query(Meeting).options(
        joinedload(Meeting.department),
        joinedload(Meeting.created_by)
    ).filter(Meeting.id == meeting_id).first()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify ownership (elders can edit any meeting, HODs only their own)
    if not _is_elder(hod_member) and meeting.created_by_id != hod_member.id:
        raise HTTPException(status_code=403, detail="You can only edit meetings you created")

    # Handle date/slot changes with conflict check
    new_date = data.meeting_date if data.meeting_date else meeting.meeting_date
    new_start = data.start_slot if data.start_slot is not None else meeting.start_slot
    new_end = data.end_slot if data.end_slot is not None else meeting.end_slot

    if new_start < 0 or new_end > 48 or new_start >= new_end:
        raise HTTPException(status_code=400, detail="Invalid time slots")

    # Check availability if date or slots changed
    if new_date != meeting.meeting_date or new_start != meeting.start_slot or new_end != meeting.end_slot:
        available, conflict_reason = check_slot_availability(
            db, meeting.department_id, new_date, new_start, new_end, exclude_meeting_id=meeting_id
        )
        if not available:
            raise HTTPException(status_code=409, detail=conflict_reason or "Time slot not available")

    # Update fields
    if data.title is not None:
        meeting.title = data.title
    if data.description is not None:
        meeting.description = data.description
    if data.meeting_date is not None:
        meeting.meeting_date = data.meeting_date
    if data.start_slot is not None:
        meeting.start_slot = data.start_slot
    if data.end_slot is not None:
        meeting.end_slot = data.end_slot
    if data.location is not None:
        meeting.location = data.location
    if data.meeting_link is not None:
        meeting.meeting_link = data.meeting_link

    db.commit()
    db.refresh(meeting)

    _log_member_action(request, db, hod_member, "update_meeting", "meeting", meeting_id,
                       f"Updated meeting '{meeting.title}'")
    db.commit()

    return {"success": True, "meeting": format_meeting_response(meeting, db)}


@router.delete("/hod/meetings/{meeting_id}")
def delete_hod_meeting(
    meeting_id: int,
    request: Request,
    phone: str = Query(...),
    delete_scope: str = Query("single", description="single, future, or all"),
    db: Session = Depends(get_db)
):
    """Delete meeting(s) - supports single, future (this and future), or all in recurring series"""
    hod_member = _find_member_by_phone(db, phone)
    if not hod_member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Find meeting
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify ownership (elders can delete any meeting, HODs only their own)
    if not _is_elder(hod_member) and meeting.created_by_id != hod_member.id:
        raise HTTPException(status_code=403, detail="You can only delete meetings you created")

    deleted_count = 1

    if delete_scope in ("future", "all") and meeting.recurrence_group_id:
        # Delete multiple meetings in the recurring series
        # Elders can delete all; HODs only those they created
        query = db.query(Meeting).filter(
            Meeting.recurrence_group_id == meeting.recurrence_group_id
        )
        if not _is_elder(hod_member):
            query = query.filter(Meeting.created_by_id == hod_member.id)

        if delete_scope == "future":
            # Delete this meeting and all future ones in the series
            query = query.filter(Meeting.meeting_date >= meeting.meeting_date)

        meetings_to_delete = query.all()
        deleted_count = len(meetings_to_delete)

        for m in meetings_to_delete:
            db.delete(m)
    else:
        # Delete only this single meeting
        db.delete(meeting)

    db.commit()

    _log_member_action(request, db, hod_member, "delete_meeting", "meeting", meeting_id,
                       f"Deleted {deleted_count} meeting(s) (scope: {delete_scope})")
    db.commit()

    message = f"Deleted {deleted_count} meeting(s)" if deleted_count > 1 else "Meeting deleted"
    return {"success": True, "message": message, "deleted_count": deleted_count}


# --- Member Meeting Endpoints ---

@router.get("/meetings")
def get_member_meetings(phone: str = Query(...), db: Session = Depends(get_db)):
    """Get meetings for the current month for member's approved departments, plus general meetings"""
    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Get approved department IDs
    approved_dept_ids = set(
        md.department_id for md in db.query(MemberDepartment).filter(
            MemberDepartment.member_id == member.id,
            MemberDepartment.status == "approved"
        ).all()
    )

    # Also include departments where user is HOD
    hod_dept_ids = set(
        d.id for d in db.query(Department).filter(
            Department.hod_member_id == member.id
        ).all()
    )

    # Combine both sets
    member_dept_ids = approved_dept_ids | hod_dept_ids

    # Get member's leadership roles
    member_roles = []
    if member.leadership_roles:
        try:
            member_roles = json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
        except (ValueError, TypeError):
            member_roles = []
    # Add "hod" role if member is HOD of any department
    if hod_dept_ids:
        member_roles = ["hod"] + member_roles

    # Get remaining meetings for current month (from today onwards)
    today = date.today()
    # Calculate last day of month
    if today.month == 12:
        month_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        month_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

    all_meetings = db.query(Meeting).options(
        joinedload(Meeting.department),
        joinedload(Meeting.created_by)
    ).filter(
        Meeting.meeting_date >= today,
        Meeting.meeting_date <= month_end
    ).order_by(Meeting.meeting_date, Meeting.start_slot).all()

    # Filter meetings that are relevant to this member
    relevant_meetings = []
    for meeting in all_meetings:
        # General meetings are for all approved members or HODs
        if meeting.is_general:
            if member_dept_ids:  # Only show if member has at least one department
                relevant_meetings.append(meeting)
            continue

        # Leadership role meetings - check if member has any of the target roles
        if meeting.target_leadership_roles:
            try:
                target_roles = json.loads(meeting.target_leadership_roles) if isinstance(meeting.target_leadership_roles, str) else meeting.target_leadership_roles
                if set(target_roles) & set(member_roles):  # Member has at least one target role
                    relevant_meetings.append(meeting)
            except (ValueError, TypeError):
                pass
            continue

        # Individual member meetings - check if member is in the target list
        if meeting.target_member_ids:
            try:
                target_member_list = json.loads(meeting.target_member_ids) if isinstance(meeting.target_member_ids, str) else meeting.target_member_ids
                if member.id in target_member_list:
                    relevant_meetings.append(meeting)
            except (ValueError, TypeError):
                pass
            continue

        # Multi-department meetings
        if meeting.target_department_ids:
            try:
                target_ids = json.loads(meeting.target_department_ids) if isinstance(meeting.target_department_ids, str) else meeting.target_department_ids
                if set(target_ids) & member_dept_ids:  # Member has at least one target department
                    relevant_meetings.append(meeting)
            except (ValueError, TypeError):
                pass
            continue

        # Single department meetings
        if meeting.department_id and meeting.department_id in member_dept_ids:
            relevant_meetings.append(meeting)

    return {
        "meetings": [format_meeting_response(m, db, member.id) for m in relevant_meetings],
        "total": len(relevant_meetings),
        "month": today.strftime("%B %Y"),
        "month_end": month_end.isoformat()
    }


@router.post("/meetings/{meeting_id}/rsvp")
def submit_rsvp(
    meeting_id: int,
    data: RSVPRequest,
    request: Request,
    phone: str = Query(...),
    db: Session = Depends(get_db)
):
    """Submit or update RSVP for a meeting"""
    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Find meeting
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify member is allowed to RSVP for this meeting
    is_allowed = False

    if meeting.is_general:
        # All Leaders meeting - member must have at least one approved department
        has_approved = db.query(MemberDepartment).filter(
            MemberDepartment.member_id == member.id,
            MemberDepartment.status == "approved"
        ).first()
        is_allowed = has_approved is not None
    elif meeting.target_leadership_roles:
        # Leadership role meeting - member must have one of the target roles
        try:
            target_roles = json.loads(meeting.target_leadership_roles) if isinstance(meeting.target_leadership_roles, str) else meeting.target_leadership_roles
        except:
            target_roles = []

        # Get member's leadership roles
        member_roles = []
        if member.leadership_roles:
            try:
                member_roles = json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
            except:
                member_roles = []

        # Check if member is HOD for "hod" role
        if "hod" in target_roles:
            is_hod = db.query(Department).filter(Department.hod_member_id == member.id).first()
            if is_hod:
                is_allowed = True

        # Check other roles
        if not is_allowed and set(target_roles) & set(member_roles):
            is_allowed = True
    elif meeting.target_member_ids:
        # Individual members meeting - member must be in the target list
        try:
            target_ids = json.loads(meeting.target_member_ids) if isinstance(meeting.target_member_ids, str) else meeting.target_member_ids
            is_allowed = member.id in target_ids
        except:
            is_allowed = False
    elif meeting.target_department_ids:
        # Multi-department meeting - member must be approved in one of the target departments
        try:
            target_ids = json.loads(meeting.target_department_ids) if isinstance(meeting.target_department_ids, str) else meeting.target_department_ids
        except:
            target_ids = []

        if target_ids:
            membership = db.query(MemberDepartment).filter(
                MemberDepartment.member_id == member.id,
                MemberDepartment.department_id.in_(target_ids),
                MemberDepartment.status == "approved"
            ).first()
            is_allowed = membership is not None
    elif meeting.department_id:
        # Single department meeting - member must be approved in that department OR be HOD
        membership = db.query(MemberDepartment).filter(
            MemberDepartment.member_id == member.id,
            MemberDepartment.department_id == meeting.department_id,
            MemberDepartment.status == "approved"
        ).first()

        # Also check if member is HOD of this department
        is_hod = db.query(Department).filter(
            Department.id == meeting.department_id,
            Department.hod_member_id == member.id
        ).first()

        is_allowed = membership is not None or is_hod is not None

    if not is_allowed:
        raise HTTPException(status_code=403, detail="You are not eligible to RSVP for this meeting")

    # Validate response
    if data.response not in ["attending", "not_attending"]:
        raise HTTPException(status_code=400, detail="Response must be 'attending' or 'not_attending'")

    # Find or create RSVP
    rsvp = db.query(MeetingRSVP).filter(
        MeetingRSVP.meeting_id == meeting_id,
        MeetingRSVP.member_id == member.id
    ).first()

    if rsvp:
        rsvp.response = data.response
        rsvp.updated_at = datetime.now()
    else:
        rsvp = MeetingRSVP(
            meeting_id=meeting_id,
            member_id=member.id,
            response=data.response
        )
        db.add(rsvp)

    _log_member_action(request, db, member, "meeting_rsvp", "meeting", meeting_id,
        f"RSVP: {data.response} for {meeting.title}")
    db.commit()

    return {"success": True, "response": data.response}


# --- Admin Meeting Endpoints ---

@router.get("/admin/meetings")
def get_all_meetings(
    department_id: Optional[int] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Get all meetings (admin)"""
    query = db.query(Meeting).options(
        joinedload(Meeting.department),
        joinedload(Meeting.created_by)
    )

    if department_id:
        query = query.filter(Meeting.department_id == department_id)

    if from_date:
        try:
            from_dt = date.fromisoformat(from_date)
            query = query.filter(Meeting.meeting_date >= from_dt)
        except ValueError:
            pass

    if to_date:
        try:
            to_dt = date.fromisoformat(to_date)
            query = query.filter(Meeting.meeting_date <= to_dt)
        except ValueError:
            pass

    meetings = query.order_by(Meeting.meeting_date.desc(), Meeting.start_slot).all()

    return {
        "meetings": [format_meeting_response(m, db) for m in meetings],
        "total": len(meetings)
    }


@router.post("/admin/meetings")
def create_admin_meeting(data: MeetingCreate, request: Request = None, db: Session = Depends(get_db)):
    """Create a meeting (admin) - supports single dept, all leaders, multi-dept, individuals, and recurring"""

    if data.start_slot < 0 or data.end_slot > 48 or data.start_slot >= data.end_slot:
        raise HTTPException(status_code=400, detail="Invalid time slots")

    # Determine meeting type and validate
    is_general = data.is_general
    target_dept_ids_str = None
    target_member_ids_str = None
    target_leadership_roles_str = None
    department_id = None

    if is_general:
        # All leaders meeting - no specific department
        pass
    elif data.target_leadership_roles and len(data.target_leadership_roles) > 0:
        # Leadership roles meeting (HODs, Deacons, Elders)
        valid_roles = ["hod", "pastor", "deacon", "elder", "service_manager"]
        invalid = [r for r in data.target_leadership_roles if r not in valid_roles]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid roles: {invalid}. Valid roles are: {valid_roles}")
        target_leadership_roles_str = json.dumps(data.target_leadership_roles)
    elif data.target_member_ids and len(data.target_member_ids) > 0:
        # Individual members meeting
        # Validate all members exist
        for member_id in data.target_member_ids:
            member = db.query(Member).filter(Member.id == member_id).first()
            if not member:
                raise HTTPException(status_code=404, detail=f"Member {member_id} not found")
        target_member_ids_str = json.dumps(data.target_member_ids)
    elif data.target_department_ids and len(data.target_department_ids) > 0:
        # Multi-department meeting
        # Validate all departments exist
        for dept_id in data.target_department_ids:
            dept = db.query(Department).filter(Department.id == dept_id).first()
            if not dept:
                raise HTTPException(status_code=404, detail=f"Department {dept_id} not found")
        target_dept_ids_str = json.dumps(data.target_department_ids)
    elif data.department_id:
        # Single department meeting
        department = db.query(Department).filter(Department.id == data.department_id).first()
        if not department:
            raise HTTPException(status_code=404, detail="Department not found")

        department_id = data.department_id
    else:
        raise HTTPException(status_code=400, detail="Meeting must target a department, multiple departments, leadership roles, specific members, or all leaders")

    # Generate meeting dates (single or recurring)
    meeting_dates = [data.meeting_date]
    recurrence_group_id = None

    if data.recurrence and data.recurrence != 'none' and data.recurrence_end_date:
        if data.recurrence_end_date <= data.meeting_date:
            raise HTTPException(status_code=400, detail="Recurrence end date must be after meeting date")

        meeting_dates = generate_recurring_dates(data.meeting_date, data.recurrence, data.recurrence_end_date)
        recurrence_group_id = str(uuid.uuid4())

    # Check availability for all dates (only for department-based meetings)
    conflicts = []
    dept_ids_to_check = []
    if is_general:
        dept_ids_to_check = []  # No specific department to check
    elif target_member_ids_str:
        dept_ids_to_check = []  # Individual meeting - no department conflicts
    elif data.target_department_ids:
        dept_ids_to_check = data.target_department_ids
    elif department_id:
        dept_ids_to_check = [department_id]

    for meeting_date in meeting_dates:
        for dept_id in dept_ids_to_check:
            available, conflict_reason = check_slot_availability(
                db, dept_id, meeting_date, data.start_slot, data.end_slot
            )
            if not available:
                dept = db.query(Department).filter(Department.id == dept_id).first()
                conflicts.append(f"{meeting_date.isoformat()} ({dept.name if dept else 'Unknown'}): {conflict_reason}")

    if conflicts:
        if len(conflicts) == 1:
            raise HTTPException(status_code=409, detail=conflicts[0])
        else:
            raise HTTPException(status_code=409, detail=f"Conflicts found: {'; '.join(conflicts[:3])}" +
                                (f" and {len(conflicts) - 3} more" if len(conflicts) > 3 else ""))

    # Idempotency: detect a recent duplicate of the anchor meeting (double-click,
    # network retry, impatient admin). Returns the existing series instead of
    # creating another one.
    existing_dup = find_duplicate_meeting(
        db,
        title=data.title,
        meeting_date=data.meeting_date,
        start_slot=data.start_slot,
        end_slot=data.end_slot,
        department_id=department_id,
        target_department_ids=target_dept_ids_str,
        target_member_ids=target_member_ids_str,
        target_leadership_roles=target_leadership_roles_str,
        is_general=1 if is_general else 0,
    )
    if existing_dup:
        existing_full = db.query(Meeting).options(
            joinedload(Meeting.department),
            joinedload(Meeting.created_by)
        ).filter(Meeting.id == existing_dup.id).first()
        # Count occurrences in the series (if it was a recurring series)
        occurrence_count = 1
        if existing_dup.recurrence_group_id:
            occurrence_count = db.query(Meeting).filter(
                Meeting.recurrence_group_id == existing_dup.recurrence_group_id
            ).count()
        return {
            "success": True,
            "meeting": format_meeting_response(existing_full, db),
            "meetings_created": occurrence_count,
            "recurrence_group_id": existing_dup.recurrence_group_id,
            "duplicate_suppressed": True,
        }

    # Create meetings for all dates
    created_meetings = []
    for mtg_date in meeting_dates:
        meeting = Meeting(
            department_id=department_id,
            created_by_id=None,  # Admin-created
            title=data.title,
            description=data.description,
            meeting_date=mtg_date,
            start_slot=data.start_slot,
            end_slot=data.end_slot,
            location=data.location,
            meeting_link=data.meeting_link,
            is_general=1 if is_general else 0,
            target_department_ids=target_dept_ids_str,
            target_member_ids=target_member_ids_str,
            target_leadership_roles=target_leadership_roles_str,
            recurrence_group_id=recurrence_group_id
        )
        db.add(meeting)
        created_meetings.append(meeting)

    db.commit()

    # Refresh all meetings
    for meeting in created_meetings:
        db.refresh(meeting)

    # Get the first meeting with relationships for response
    first_meeting = db.query(Meeting).options(
        joinedload(Meeting.department),
        joinedload(Meeting.created_by)
    ).filter(Meeting.id == created_meetings[0].id).first()

    # Dispatch notification for the first meeting only (or series)
    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        # Determine recipients based on meeting type
        member_ids = set()
        recipients = []
        dept_name = None
        target_dept_ids = []

        if is_general:
            # All approved members
            members = db.query(Member).join(MemberDepartment).filter(
                MemberDepartment.status == "approved"
            ).distinct().all()
            member_ids = set(m.id for m in members)
            recipients = [{"id": m.id, "name": m.full_name, "email": m.email, "phone": m.phone} for m in members if m.email]
            dept_name = "All Leaders"
            # Get all department IDs for HOD lookup
            all_depts = db.query(Department.id).all()
            target_dept_ids = [d[0] for d in all_depts]
        elif data.target_leadership_roles:
            # Members with specific leadership roles
            role_labels = {"hod": "HODs", "pastor": "Pastors", "deacon": "Deacons", "elder": "Elders", "service_manager": "Service Managers"}
            dept_name = ", ".join([role_labels.get(r, r.title()) for r in data.target_leadership_roles])
            members_set = set()

            for role in data.target_leadership_roles:
                if role == "hod":
                    # Get all HODs
                    hod_depts = db.query(Department).filter(Department.hod_member_id.isnot(None)).all()
                    hod_ids = [d.hod_member_id for d in hod_depts]
                    hods = db.query(Member).filter(Member.id.in_(hod_ids)).all()
                    for m in hods:
                        members_set.add(m)
                else:
                    # Get members with this role in leadership_roles JSON
                    all_members = db.query(Member).filter(Member.leadership_roles.isnot(None)).all()
                    for m in all_members:
                        try:
                            m_roles = json.loads(m.leadership_roles) if isinstance(m.leadership_roles, str) else m.leadership_roles
                            if role in m_roles:
                                members_set.add(m)
                        except (ValueError, TypeError):
                            pass

            member_ids = set(m.id for m in members_set)
            recipients = [{"id": m.id, "name": m.full_name, "email": m.email, "phone": m.phone} for m in members_set if m.email]
        elif data.target_member_ids:
            # Specific individual members
            members = db.query(Member).filter(Member.id.in_(data.target_member_ids)).all()
            member_ids = set(m.id for m in members)
            recipients = [{"id": m.id, "name": m.full_name, "email": m.email, "phone": m.phone} for m in members if m.email]
            dept_name = f"{len(members)} Selected Members"
        elif data.target_department_ids:
            # Members from specified departments
            members = db.query(Member).join(MemberDepartment).filter(
                MemberDepartment.department_id.in_(data.target_department_ids),
                MemberDepartment.status == "approved"
            ).distinct().all()
            member_ids = set(m.id for m in members)
            recipients = [{"id": m.id, "name": m.full_name, "email": m.email, "phone": m.phone} for m in members if m.email]
            depts = db.query(Department).filter(Department.id.in_(data.target_department_ids)).all()
            dept_name = ", ".join([d.name for d in depts])
            target_dept_ids = data.target_department_ids
        elif department_id:
            # Single department
            members = db.query(Member).join(MemberDepartment).filter(
                MemberDepartment.department_id == department_id,
                MemberDepartment.status == "approved"
            ).all()
            member_ids = set(m.id for m in members)
            recipients = [{"id": m.id, "name": m.full_name, "email": m.email, "phone": m.phone} for m in members if m.email]
            dept = db.query(Department).filter(Department.id == department_id).first()
            dept_name = dept.name if dept else None
            target_dept_ids = [department_id]

        # Include HODs of relevant departments if not already in recipients
        if target_dept_ids:
            hod_depts = db.query(Department).filter(
                Department.id.in_(target_dept_ids),
                Department.hod_member_id.isnot(None)
            ).all()
            hod_ids = [d.hod_member_id for d in hod_depts if d.hod_member_id not in member_ids]
            if hod_ids:
                hods = db.query(Member).filter(Member.id.in_(hod_ids)).all()
                for hod in hods:
                    if hod.email:
                        recipients.append({"id": hod.id, "name": hod.full_name, "email": hod.email, "phone": hod.phone})

        if recipients:
            # Include recurrence info in notification
            recurrence_info = None
            if len(created_meetings) > 1:
                recurrence_info = f"This is a recurring meeting ({len(created_meetings)} occurrences until {meeting_dates[-1].isoformat()})"

            dispatch_event(db, EventType.MEETING_CREATED, {
                "meeting_id": first_meeting.id,
                "title": first_meeting.title,
                "description": first_meeting.description,
                "meeting_date": first_meeting.meeting_date.isoformat() if first_meeting.meeting_date else None,
                "start_time": slot_to_time(first_meeting.start_slot),
                "end_time": slot_to_time(first_meeting.end_slot),
                "location": first_meeting.location,
                "meeting_link": first_meeting.meeting_link,
                "department_name": dept_name,
                "is_general": is_general,
                "recurrence_info": recurrence_info,
                "recipients": recipients
            })
    except Exception as e:
        print(f"Failed to dispatch notification: {e}")

    if request:
        _log_admin_action(request, db, "create_meeting", "meeting", created_meetings[0].id,
                          f"Created meeting '{data.title}' ({len(created_meetings)} occurrence(s))")
        db.commit()

    return {
        "success": True,
        "meeting": format_meeting_response(first_meeting, db),
        "meetings_created": len(created_meetings),
        "recurrence_group_id": recurrence_group_id
    }


@router.put("/admin/meetings/{meeting_id}")
def update_admin_meeting(
    meeting_id: int,
    data: MeetingUpdate,
    request: Request,
    db: Session = Depends(get_db)
):
    """Update any meeting (admin)"""
    meeting = db.query(Meeting).options(
        joinedload(Meeting.department),
        joinedload(Meeting.created_by)
    ).filter(Meeting.id == meeting_id).first()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Handle date/slot changes with conflict check
    new_date = data.meeting_date if data.meeting_date else meeting.meeting_date
    new_start = data.start_slot if data.start_slot is not None else meeting.start_slot
    new_end = data.end_slot if data.end_slot is not None else meeting.end_slot

    if new_start < 0 or new_end > 48 or new_start >= new_end:
        raise HTTPException(status_code=400, detail="Invalid time slots")

    if new_date != meeting.meeting_date or new_start != meeting.start_slot or new_end != meeting.end_slot:
        available, conflict_reason = check_slot_availability(
            db, meeting.department_id, new_date, new_start, new_end, exclude_meeting_id=meeting_id
        )
        if not available:
            raise HTTPException(status_code=409, detail=conflict_reason or "Time slot not available")

    if data.title is not None:
        meeting.title = data.title
    if data.description is not None:
        meeting.description = data.description
    if data.meeting_date is not None:
        meeting.meeting_date = data.meeting_date
    if data.start_slot is not None:
        meeting.start_slot = data.start_slot
    if data.end_slot is not None:
        meeting.end_slot = data.end_slot
    if data.location is not None:
        meeting.location = data.location
    if data.meeting_link is not None:
        meeting.meeting_link = data.meeting_link

    db.commit()
    db.refresh(meeting)

    _log_admin_action(request, db, "update_meeting", "meeting", meeting_id, f"Updated meeting '{meeting.title}'")
    db.commit()

    return {"success": True, "meeting": format_meeting_response(meeting, db)}


@router.delete("/admin/meetings/{meeting_id}")
def delete_admin_meeting(
    meeting_id: int,
    request: Request,
    delete_scope: str = Query("single", description="single, future, or all"),
    db: Session = Depends(get_db)
):
    """Delete meeting(s) - supports single, future (this and future), or all in recurring series"""
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    deleted_count = 1

    if delete_scope in ("future", "all") and meeting.recurrence_group_id:
        # Delete multiple meetings in the recurring series
        query = db.query(Meeting).filter(
            Meeting.recurrence_group_id == meeting.recurrence_group_id
        )

        if delete_scope == "future":
            # Delete this meeting and all future ones in the series
            query = query.filter(Meeting.meeting_date >= meeting.meeting_date)

        meetings_to_delete = query.all()
        deleted_count = len(meetings_to_delete)

        for m in meetings_to_delete:
            db.delete(m)
    else:
        # Delete only this single meeting
        db.delete(meeting)

    db.commit()

    _log_admin_action(request, db, "delete_meeting", "meeting", meeting_id,
                      f"Deleted {deleted_count} meeting(s) (scope: {delete_scope})")
    db.commit()

    message = f"Deleted {deleted_count} meeting(s)" if deleted_count > 1 else "Meeting deleted"
    return {"success": True, "message": message, "deleted_count": deleted_count}


@router.get("/admin/meetings/{meeting_id}/rsvps")
def get_meeting_rsvps(meeting_id: int, db: Session = Depends(get_db)):
    """Get all RSVPs for a meeting (admin)"""
    meeting = db.query(Meeting).options(
        joinedload(Meeting.department)
    ).filter(Meeting.id == meeting_id).first()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    rsvps = db.query(MeetingRSVP).options(
        joinedload(MeetingRSVP.member)
    ).filter(MeetingRSVP.meeting_id == meeting_id).all()

    return {
        "meeting_id": meeting_id,
        "meeting_title": meeting.title,
        "department_name": meeting.department.name if meeting.department else None,
        "rsvps": [
            {
                "id": r.id,
                "member_id": r.member_id,
                "member_name": r.member.full_name if r.member else None,
                "member_phone": r.member.phone if r.member else None,
                "response": r.response,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None
            }
            for r in rsvps
        ],
        "total": len(rsvps),
        "attending": sum(1 for r in rsvps if r.response == "attending"),
        "not_attending": sum(1 for r in rsvps if r.response == "not_attending")
    }


# ============ POSTER REQUEST ENDPOINTS ============

def format_poster_request(pr: PosterRequest) -> dict:
    """Format a poster request for API response"""
    import json

    # Parse speakers from JSON
    speakers = None
    if pr.speakers:
        try:
            speakers = json.loads(pr.speakers)
        except (json.JSONDecodeError, TypeError):
            speakers = None

    # Parse output_formats from JSON
    output_formats = None
    if pr.output_formats:
        try:
            output_formats = json.loads(pr.output_formats)
        except (json.JSONDecodeError, TypeError):
            output_formats = None

    return {
        "id": pr.id,
        "requester_id": pr.requester_id,
        "requester_name": pr.requester.full_name if pr.requester else None,
        "requester_email": pr.requester.email if pr.requester else None,
        "event_name": pr.event_name,
        "ministry_department": pr.ministry_department,
        "event_date": pr.event_date.isoformat() if pr.event_date else None,
        "event_time": pr.event_time,
        "venue_platform": pr.venue_platform,
        "speakers": speakers,
        "theme_tagline": pr.theme_tagline,
        "scripture": pr.scripture,
        "target_audience": pr.target_audience,
        "purpose": pr.purpose,
        "output_formats": output_formats,
        "additional_notes": pr.additional_notes,
        "status": pr.status,
        "acknowledged_by_id": pr.acknowledged_by_id,
        "acknowledged_by_name": pr.acknowledged_by.full_name if pr.acknowledged_by else None,
        "acknowledged_at": pr.acknowledged_at.isoformat() if pr.acknowledged_at else None,
        "created_at": pr.created_at.isoformat() if pr.created_at else None
    }


@router.post("/poster-requests")
def create_poster_request(
    data: PosterRequestCreate,
    request: Request,
    phone: str = Query(...),
    db: Session = Depends(get_db)
):
    """Submit a new poster request (requires member login)"""
    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Serialize speakers and output_formats to JSON
    import json
    speakers_json = None
    if data.speakers:
        speakers_json = json.dumps([s.model_dump() for s in data.speakers])

    output_formats_json = None
    if data.output_formats:
        output_formats_json = json.dumps(data.output_formats)

    # Create the poster request
    pr = PosterRequest(
        requester_id=member.id,
        event_name=data.event_name,
        ministry_department=data.ministry_department,
        event_date=data.event_date,
        event_time=data.event_time,
        venue_platform=data.venue_platform,
        speakers=speakers_json,
        theme_tagline=data.theme_tagline,
        scripture=data.scripture,
        target_audience=data.target_audience,
        purpose=data.purpose,
        output_formats=output_formats_json,
        additional_notes=data.additional_notes,
        status="pending"
    )
    db.add(pr)
    db.flush()
    _log_member_action(request, db, member, "create_poster_request", "poster_request", pr.id,
        f"Poster request: {data.event_name}")
    db.commit()
    db.refresh(pr)

    # Get configured department for poster requests
    dept_setting = db.query(Settings).filter(Settings.key == "poster_request_department_id").first()
    if dept_setting and dept_setting.value:
        try:
            dept_id = int(dept_setting.value)
            # Get all members of that department
            dept_members = db.query(Member).join(MemberDepartment).filter(
                MemberDepartment.department_id == dept_id,
                MemberDepartment.status == "approved"
            ).all()

            # Also include HOD
            dept = db.query(Department).filter(Department.id == dept_id).first()
            if dept and dept.hod_member_id:
                hod = db.query(Member).filter(Member.id == dept.hod_member_id).first()
                if hod and hod not in dept_members:
                    dept_members.append(hod)

            # Send email notifications
            try:
                from notifications.dispatcher import dispatch_event
                from notifications.events import EventType

                recipients = [{"id": m.id, "name": m.full_name, "email": m.email, "phone": m.phone}
                              for m in dept_members if m.email]

                # Format speakers for display
                speakers_display = None
                if speakers_json:
                    try:
                        speakers_list = json.loads(speakers_json)
                        speakers_display = ", ".join([
                            f"{s['name']} ({s['role']})" for s in speakers_list
                        ])
                    except:
                        pass

                # Format output formats for display
                output_formats_display = None
                if output_formats_json:
                    try:
                        formats_list = json.loads(output_formats_json)
                        format_labels = {'projector': 'Projector', 'social_media': 'Social Media', 'print': 'Print'}
                        output_formats_display = ", ".join([format_labels.get(f, f) for f in formats_list])
                    except:
                        pass

                if recipients:
                    dispatch_event(db, EventType.POSTER_REQUEST_SUBMITTED, {
                        "request_id": pr.id,
                        "event_name": pr.event_name,
                        "ministry_department": pr.ministry_department or "Not specified",
                        "event_date": pr.event_date.isoformat() if pr.event_date else None,
                        "event_time": pr.event_time,
                        "venue_platform": pr.venue_platform,
                        "requester_name": member.full_name,
                        "requester_email": member.email,
                        "purpose": pr.purpose,
                        "speakers_display": speakers_display,
                        "output_formats_display": output_formats_display,
                        "theme_tagline": pr.theme_tagline,
                        "scripture": pr.scripture,
                        "target_audience": pr.target_audience,
                        "additional_notes": pr.additional_notes,
                        "recipients": recipients
                    })
            except Exception as e:
                print(f"Failed to dispatch poster request notification: {e}")

        except (ValueError, TypeError):
            pass

    return {"success": True, "request": format_poster_request(pr)}


@router.get("/poster-requests")
def get_poster_requests(
    phone: str = Query(...),
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get poster requests - for design team members shows all pending, for others shows their own"""
    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check if member is in the poster request department
    dept_setting = db.query(Settings).filter(Settings.key == "poster_request_department_id").first()
    is_design_team = False

    if dept_setting and dept_setting.value:
        try:
            dept_id = int(dept_setting.value)
            # Check membership
            membership = db.query(MemberDepartment).filter(
                MemberDepartment.member_id == member.id,
                MemberDepartment.department_id == dept_id,
                MemberDepartment.status == "approved"
            ).first()
            if membership:
                is_design_team = True

            # Check if HOD
            dept = db.query(Department).filter(Department.id == dept_id).first()
            if dept and dept.hod_member_id == member.id:
                is_design_team = True
        except (ValueError, TypeError):
            pass

    # Build query
    query = db.query(PosterRequest).options(
        joinedload(PosterRequest.requester),
        joinedload(PosterRequest.acknowledged_by)
    )

    if is_design_team:
        # Design team sees all requests
        if status:
            query = query.filter(PosterRequest.status == status)
    else:
        # Others see only their own requests
        query = query.filter(PosterRequest.requester_id == member.id)
        if status:
            query = query.filter(PosterRequest.status == status)

    requests = query.order_by(PosterRequest.created_at.desc()).all()

    return {
        "requests": [format_poster_request(pr) for pr in requests],
        "is_design_team": is_design_team,
        "total": len(requests)
    }


@router.put("/poster-requests/{request_id}/acknowledge")
def acknowledge_poster_request(
    request_id: int,
    request: Request,
    phone: str = Query(...),
    db: Session = Depends(get_db)
):
    """Acknowledge a poster request (design team only)"""
    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check if member is in the poster request department
    dept_setting = db.query(Settings).filter(Settings.key == "poster_request_department_id").first()
    is_design_team = False

    if dept_setting and dept_setting.value:
        try:
            dept_id = int(dept_setting.value)
            membership = db.query(MemberDepartment).filter(
                MemberDepartment.member_id == member.id,
                MemberDepartment.department_id == dept_id,
                MemberDepartment.status == "approved"
            ).first()
            if membership:
                is_design_team = True

            dept = db.query(Department).filter(Department.id == dept_id).first()
            if dept and dept.hod_member_id == member.id:
                is_design_team = True
        except (ValueError, TypeError):
            pass

    if not is_design_team:
        raise HTTPException(status_code=403, detail="Only design team members can acknowledge requests")

    # Find the request
    pr = db.query(PosterRequest).options(
        joinedload(PosterRequest.requester)
    ).filter(PosterRequest.id == request_id).first()

    if not pr:
        raise HTTPException(status_code=404, detail="Request not found")

    if pr.status != "pending":
        raise HTTPException(status_code=400, detail="Request has already been acknowledged")

    # Update the request
    pr.status = "acknowledged"
    pr.acknowledged_by_id = member.id
    pr.acknowledged_at = datetime.now()
    db.commit()
    db.refresh(pr)

    # Notify the requester
    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        if pr.requester and pr.requester.email:
            dispatch_event(db, EventType.POSTER_REQUEST_ACKNOWLEDGED, {
                "request_id": pr.id,
                "event_name": pr.event_name,
                "acknowledged_by_name": member.full_name,
                "recipients": [{
                    "id": pr.requester.id,
                    "name": pr.requester.full_name,
                    "email": pr.requester.email,
                    "phone": pr.requester.phone
                }]
            })
    except Exception as e:
        print(f"Failed to dispatch acknowledgment notification: {e}")

    _log_member_action(request, db, member, "acknowledge_poster_request", "poster_request", request_id,
                       f"Acknowledged poster request '{pr.event_name}'")
    db.commit()

    return {"success": True, "request": format_poster_request(pr)}


@router.put("/poster-requests/{request_id}/complete")
def complete_poster_request(
    request_id: int,
    request: Request,
    phone: str = Query(...),
    db: Session = Depends(get_db)
):
    """Mark a poster request as complete (design team only)"""
    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check if member is in the poster request department
    dept_setting = db.query(Settings).filter(Settings.key == "poster_request_department_id").first()
    is_design_team = False

    if dept_setting and dept_setting.value:
        try:
            dept_id = int(dept_setting.value)
            membership = db.query(MemberDepartment).filter(
                MemberDepartment.member_id == member.id,
                MemberDepartment.department_id == dept_id,
                MemberDepartment.status == "approved"
            ).first()
            if membership:
                is_design_team = True

            dept = db.query(Department).filter(Department.id == dept_id).first()
            if dept and dept.hod_member_id == member.id:
                is_design_team = True
        except (ValueError, TypeError):
            pass

    if not is_design_team:
        raise HTTPException(status_code=403, detail="Only design team members can complete requests")

    # Find the request
    pr = db.query(PosterRequest).options(
        joinedload(PosterRequest.requester),
        joinedload(PosterRequest.acknowledged_by)
    ).filter(PosterRequest.id == request_id).first()

    if not pr:
        raise HTTPException(status_code=404, detail="Request not found")

    if pr.status != "acknowledged":
        raise HTTPException(status_code=400, detail="Request must be in 'processing' status to mark as done")

    # Update the request
    pr.status = "completed"
    pr.completed_at = datetime.now()
    db.commit()
    db.refresh(pr)

    # Notify the requester that poster is ready
    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        if pr.requester and pr.requester.email:
            dispatch_event(db, EventType.POSTER_REQUEST_COMPLETED, {
                "request_id": pr.id,
                "event_name": pr.event_name,
                "event_date": pr.event_date.isoformat() if pr.event_date else None,
                "completed_by_name": member.full_name,
                "recipients": [{
                    "id": pr.requester.id,
                    "name": pr.requester.full_name,
                    "email": pr.requester.email,
                    "phone": pr.requester.phone
                }]
            })
    except Exception as e:
        print(f"Failed to dispatch completion notification: {e}")

    _log_member_action(request, db, member, "complete_poster_request", "poster_request", request_id,
                       f"Completed poster request '{pr.event_name}'")
    db.commit()

    return {"success": True, "request": format_poster_request(pr)}


@router.get("/admin/poster-requests")
def get_all_poster_requests(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Admin: Get all poster requests"""
    query = db.query(PosterRequest).options(
        joinedload(PosterRequest.requester),
        joinedload(PosterRequest.acknowledged_by)
    )

    if status:
        query = query.filter(PosterRequest.status == status)

    requests = query.order_by(PosterRequest.created_at.desc()).all()

    return {
        "requests": [format_poster_request(pr) for pr in requests],
        "total": len(requests)
    }


@router.get("/admin/settings/poster-request-department")
def get_poster_request_department(db: Session = Depends(get_db)):
    """Get the configured department for handling poster requests"""
    setting = db.query(Settings).filter(Settings.key == "poster_request_department_id").first()
    dept_id = setting.value if setting else None

    dept = None
    if dept_id:
        try:
            dept = db.query(Department).filter(Department.id == int(dept_id)).first()
        except (ValueError, TypeError):
            pass

    return {
        "department_id": int(dept_id) if dept_id else None,
        "department_name": dept.name if dept else None
    }


@router.put("/admin/settings/poster-request-department")
def set_poster_request_department(
    department_id: int = Body(..., embed=True),
    request: Request = None,
    db: Session = Depends(get_db)
):
    """Set the department that handles poster requests"""
    # Validate department exists
    dept = db.query(Department).filter(Department.id == department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")

    # Update or create setting
    setting = db.query(Settings).filter(Settings.key == "poster_request_department_id").first()
    if setting:
        setting.value = str(department_id)
    else:
        setting = Settings(key="poster_request_department_id", value=str(department_id))
        db.add(setting)

    db.commit()

    if request:
        _log_admin_action(request, db, "update_settings", "settings", None, f"Set poster request department to '{dept.name}'")
        db.commit()

    return {"success": True, "department_id": department_id, "department_name": dept.name}


# ============ NOTIFICATION ENDPOINTS ============

@router.get("/admin/notifications/email-settings")
def get_email_settings(db: Session = Depends(get_db)):
    """Get all email settings (SMTP and Resend), passwords masked"""
    all_keys = [
        'smtp_enabled', 'smtp_host', 'smtp_port',
        'smtp_username', 'smtp_password',
        'smtp_from_name', 'smtp_from_email',
        'resend_enabled', 'resend_api_key',
        'resend_from_name', 'resend_from_email'
    ]
    settings = db.query(Settings).filter(Settings.key.in_(all_keys)).all()
    result = {s.key: s.value for s in settings}

    # Mask secrets
    if result.get('smtp_password'):
        result['smtp_password'] = '********'
    if result.get('resend_api_key'):
        result['resend_api_key'] = '********'

    return result


@router.get("/admin/notifications/smtp-settings")
def get_smtp_settings(db: Session = Depends(get_db)):
    """Get SMTP settings (password is masked) - legacy endpoint"""
    smtp_keys = [
        'smtp_enabled', 'smtp_host', 'smtp_port',
        'smtp_username', 'smtp_password',
        'smtp_from_name', 'smtp_from_email'
    ]
    settings = db.query(Settings).filter(Settings.key.in_(smtp_keys)).all()
    result = {s.key: s.value for s in settings}

    # Mask the password
    if result.get('smtp_password'):
        result['smtp_password'] = '********' if result['smtp_password'] else ''

    return result


@router.put("/admin/notifications/smtp-settings")
def update_smtp_settings(data: SMTPSettingsUpdate, db: Session = Depends(get_db)):
    """Update SMTP settings"""
    updates = data.model_dump(exclude_none=True)

    for key, value in updates.items():
        # Skip password update if it's the masked value
        if key == 'smtp_password' and value == '********':
            continue

        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))

    db.commit()
    return {"success": True}


@router.post("/admin/notifications/test-email")
def send_test_email(data: TestEmailRequest, db: Session = Depends(get_db)):
    """Send a test email using the active channel (Resend or SMTP)"""
    from notifications.dispatcher import get_email_settings, get_email_channel

    # Get all email settings
    settings = get_email_settings(db)

    # Get the active channel
    channel, channel_name = get_email_channel(settings)

    if not channel:
        raise HTTPException(
            status_code=400,
            detail="No email channel configured. Enable either Resend or SMTP."
        )

    # First test connection
    conn_success, conn_error = channel.test_connection()
    if not conn_success:
        raise HTTPException(status_code=400, detail=f"Connection failed: {conn_error}")

    # Send test email
    success, error = channel.send_test_email(data.to_email)

    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to send: {error}")

    return {"success": True, "message": f"Test email sent via {channel_name} to {data.to_email}"}


@router.get("/admin/notifications/config")
def get_notification_configs(db: Session = Depends(get_db)):
    """Get all notification event configurations"""
    from notifications.events import EventType, EVENT_LABELS, EVENT_DESCRIPTIONS

    configs = db.query(NotificationConfig).all()
    config_map = {c.event_type: c for c in configs}

    result = []
    for event_type in EventType:
        config = config_map.get(event_type.value)
        result.append({
            "event_type": event_type.value,
            "label": EVENT_LABELS.get(event_type, event_type.value),
            "description": EVENT_DESCRIPTIONS.get(event_type, ""),
            "email_enabled": bool(config.email_enabled) if config else True,
            "sms_enabled": bool(config.sms_enabled) if config else False,
            "push_enabled": bool(config.push_enabled) if config else False
        })

    return result


@router.put("/admin/notifications/config/{event_type}")
def update_notification_config(
    event_type: str,
    data: NotificationConfigUpdate,
    db: Session = Depends(get_db)
):
    """Update notification config for a specific event type"""
    from notifications.events import EventType

    # Validate event type
    valid_types = [e.value for e in EventType]
    if event_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid event type: {event_type}")

    config = db.query(NotificationConfig).filter(
        NotificationConfig.event_type == event_type
    ).first()

    if not config:
        config = NotificationConfig(event_type=event_type)
        db.add(config)

    if data.email_enabled is not None:
        config.email_enabled = data.email_enabled
    if data.sms_enabled is not None:
        config.sms_enabled = data.sms_enabled
    if data.push_enabled is not None:
        config.push_enabled = data.push_enabled

    db.commit()
    return {"success": True, "event_type": event_type}


@router.get("/admin/notifications/logs")
def get_notification_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Get notification logs with pagination and filtering"""
    query = db.query(NotificationLog)

    if status:
        query = query.filter(NotificationLog.status == status)
    if event_type:
        query = query.filter(NotificationLog.event_type == event_type)

    total = query.count()
    logs = query.order_by(NotificationLog.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "logs": [
            {
                "id": log.id,
                "event_type": log.event_type,
                "channel": log.channel,
                "recipient_email": log.recipient_email,
                "recipient_phone": log.recipient_phone,
                "subject": log.subject,
                "status": log.status,
                "error_message": log.error_message,
                "sent_at": log.sent_at.isoformat() if log.sent_at else None,
                "created_at": log.created_at.isoformat() if log.created_at else None
            }
            for log in logs
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


# ============ SCHEDULER / REMINDERS ============

@router.get("/admin/scheduler/status")
def get_scheduler_status():
    """Get the current status of the background scheduler"""
    from scheduler import get_scheduler_status
    return get_scheduler_status()


@router.post("/admin/scheduler/reminders/trigger")
def trigger_meeting_reminders(meeting_ids: Optional[List[int]] = Query(None)):
    """
    Manually trigger meeting reminders.

    Args:
        meeting_ids: Optional list of specific meeting IDs to include.
                    If not provided, sends for all upcoming meetings (today + tomorrow).
    """
    from scheduler import send_meeting_reminders
    result = send_meeting_reminders(meeting_ids=meeting_ids)
    return result


@router.post("/admin/scheduler/reminders/preview")
def preview_meeting_reminders(db: Session = Depends(get_db)):
    """
    Preview which meetings would get reminders.
    Shows today's meetings.
    Does not send any emails, just returns the list.
    """
    from scheduler import get_meeting_recipients, slot_to_time

    now = datetime.now()
    today = now.date()
    today_str = today.isoformat()

    # Find today's meetings
    meetings = db.query(Meeting).options(
        joinedload(Meeting.department)
    ).filter(
        Meeting.meeting_date == today_str
    ).order_by(Meeting.start_slot).all()

    preview = []
    total_recipients = 0

    for meeting in meetings:
        recipients = get_meeting_recipients(db, meeting)
        total_recipients += len(recipients)

        preview.append({
            "id": meeting.id,
            "title": meeting.title,
            "date": meeting.meeting_date,
            "time": f"{slot_to_time(meeting.start_slot)} - {slot_to_time(meeting.end_slot)}",
            "location": meeting.location,
            "department": meeting.department.name if meeting.department else "All Leaders",
            "is_general": meeting.is_general,
            "recipient_count": len(recipients),
            "recipients": [
                {"name": r["name"], "email": r["email"]}
                for r in recipients[:10]  # Limit preview to first 10
            ],
            "more_recipients": max(0, len(recipients) - 10)
        })

    return {
        "today": today_str,
        "meetings_count": len(meetings),
        "total_recipients": total_recipients,
        "meetings": preview
    }


@router.post("/admin/meetings/{meeting_id}/send-invite")
def send_meeting_invite(meeting_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Manually send meeting invite (RSVP request) to all members of the meeting's department.
    """
    from scheduler import get_meeting_recipients, slot_to_time
    from notifications.dispatcher import dispatch_event
    from notifications.events import EventType

    meeting = db.query(Meeting).options(
        joinedload(Meeting.department)
    ).filter(Meeting.id == meeting_id).first()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    recipients = get_meeting_recipients(db, meeting)

    if not recipients:
        return {
            "success": False,
            "message": "No recipients found for this meeting",
            "emails_sent": 0
        }

    # Prepare meeting data
    meeting_data = {
        "meeting_id": meeting.id,
        "title": meeting.title,
        "description": meeting.description,
        "meeting_date": meeting.meeting_date,
        "start_time": slot_to_time(meeting.start_slot),
        "end_time": slot_to_time(meeting.end_slot),
        "location": meeting.location,
        "meeting_link": meeting.meeting_link,
        "department_name": meeting.department.name if meeting.department else "All Leaders",
        "is_general": meeting.is_general
    }

    # Dispatch invite notification
    dispatch_event(
        db=db,
        event_type=EventType.MEETING_CREATED,
        data=meeting_data,
        recipients=recipients
    )

    _log_admin_action(request, db, "send_meeting_invite", "meeting", meeting_id,
                      f"Sent invite for '{meeting.title}' to {len(recipients)} recipient(s)")
    db.commit()

    return {
        "success": True,
        "message": f"Meeting invite sent to {len(recipients)} recipient(s)",
        "emails_sent": len(recipients)
    }


@router.post("/admin/meetings/{meeting_id}/send-reminder")
def send_meeting_reminder(meeting_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Manually send meeting reminder to all members of the meeting's department.
    """
    from scheduler import get_meeting_recipients, slot_to_time
    from notifications.dispatcher import dispatch_event
    from notifications.events import EventType

    meeting = db.query(Meeting).options(
        joinedload(Meeting.department)
    ).filter(Meeting.id == meeting_id).first()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    recipients = get_meeting_recipients(db, meeting)

    if not recipients:
        return {
            "success": False,
            "message": "No recipients found for this meeting",
            "emails_sent": 0
        }

    # Prepare meeting data
    meeting_data = {
        "title": meeting.title,
        "meeting_date": meeting.meeting_date,
        "start_time": slot_to_time(meeting.start_slot),
        "end_time": slot_to_time(meeting.end_slot),
        "location": meeting.location,
        "department_name": meeting.department.name if meeting.department else "All Leaders",
        "meeting_link": meeting.meeting_link,
        "description": meeting.description
    }

    # Dispatch reminder notification
    dispatch_event(
        db=db,
        event_type=EventType.MEETING_REMINDER,
        data=meeting_data,
        recipients=recipients
    )

    _log_admin_action(request, db, "send_meeting_reminder", "meeting", meeting_id,
                      f"Sent reminder for '{meeting.title}' to {len(recipients)} recipient(s)")
    db.commit()

    return {
        "success": True,
        "message": f"Meeting reminder sent to {len(recipients)} recipient(s)",
        "emails_sent": len(recipients)
    }


# ============ SERVICE PROGRAM ENDPOINTS ============

def _title_case_name(name: str) -> str:
    """Normalize a name to title case (first letter uppercase, rest lowercase per word)."""
    if not name:
        return name
    return " ".join(w.capitalize() for w in name.split())


def _get_titled_name(member: "Member") -> str:
    """Get member's full name with leadership title prefix (e.g., 'Pastor John Smith')"""
    name = _title_case_name(member.full_name)
    roles = member.leadership_roles or []
    if "pastor" in roles:
        return f"Pastor {name}"
    elif "elder" in roles:
        return f"Elder {name}"
    elif "deacon" in roles:
        return f"Deacon {name}"
    elif "dr" in roles:
        return f"Dr {name}"
    elif "mr" in roles:
        return f"Mr {name}"
    elif "mrs" in roles:
        return f"Mrs {name}"
    return name


def _program_to_dict(program: ServiceProgram, public: bool = False, db: Session = None) -> dict:
    """Convert a ServiceProgram model to response dict.
    If public=True, the Close marker item is stripped and its time is used
    to calculate the duration of the last real program item.
    If db is provided, participant names are enriched with leadership titles."""
    # Hash is the updated_at unix timestamp - changes on every edit
    ts = program.updated_at or program.created_at
    hashcode = str(int(ts.timestamp())) if ts else "0"

    def _parse_json(val):
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return []
        return val or []

    # Get creator info with title
    created_by_name = None
    created_by_id = getattr(program, 'created_by_member_id', None)
    if created_by_id and hasattr(program, 'created_by') and program.created_by:
        created_by_name = _get_titled_name(program.created_by)

    items = _parse_json(program.program_items)

    if public and items:
        # Find and remove the Close marker, use its time for last item duration
        close_item = None
        real_items = []
        for it in items:
            if isinstance(it, dict) and (it.get("item") or "").lower() == "close":
                close_item = it
            else:
                real_items.append(it)
        # Calculate duration for the last real item using close time
        if close_item and real_items and close_item.get("time"):
            last = real_items[-1]
            if last.get("time"):
                try:
                    from datetime import datetime as _dt
                    start = _dt.strptime(last["time"], "%H:%M")
                    end = _dt.strptime(close_item["time"], "%H:%M")
                    diff_min = int((end - start).total_seconds() // 60)
                    if diff_min > 0:
                        last["duration_minutes"] = diff_min
                except (ValueError, TypeError):
                    pass
        items = real_items

    participants = _parse_json(program.participants)

    # Enrich participant names with leadership titles
    if db and participants:
        # Collect unique plain names (strip existing titles for lookup)
        title_prefixes = ("pastor ", "elder ", "deacon ", "dr ", "mr ", "mrs ")
        plain_names = set()
        for pt in participants:
            name = (pt.get("name") or "").strip()
            name_lower = name.lower()
            # Strip existing title prefix for DB lookup
            for prefix in title_prefixes:
                if name_lower.startswith(prefix):
                    name = name[len(prefix):]
                    break
            if name:
                plain_names.add(name.lower())
        if plain_names:
            members = db.query(Member).filter(
                func.lower(Member.full_name).in_(list(plain_names))
            ).all()
            title_map = {m.full_name.lower(): _get_titled_name(m) for m in members}
            for pt in participants:
                name = (pt.get("name") or "").strip()
                name_lower = name.lower()
                # Strip existing title for lookup
                lookup_name = name_lower
                for prefix in title_prefixes:
                    if lookup_name.startswith(prefix):
                        lookup_name = lookup_name[len(prefix):]
                        break
                if lookup_name in title_map:
                    pt["name"] = title_map[lookup_name]
                else:
                    # Apply title case even for non-member names
                    pt["name"] = _title_case_name(name)

    return {
        "id": program.id,
        "hash": hashcode,
        "title": program.title,
        "service_date": program.service_date.isoformat(),
        "location_type": program.location_type or "onsite",
        "program_items": items,
        "participants": participants,
        "admin_announcements": _parse_json(program.admin_announcements),
        "pastors_announcements": _parse_json(program.pastors_announcements),
        "prayer_points": _parse_json(program.prayer_points),
        "status": getattr(program, 'status', 'draft') or 'draft',
        "created_by_member_id": created_by_id,
        "created_by_name": created_by_name,
        "created_at": program.created_at.isoformat() if program.created_at else None,
        "updated_at": program.updated_at.isoformat() if program.updated_at else None
    }


def _cleanup_past_programs(db: Session):
    """Delete programs with service_date before today"""
    today = date.today()
    deleted = db.query(ServiceProgram).filter(ServiceProgram.service_date < today).delete()
    if deleted:
        db.commit()
        print(f"[Cleanup] Deleted {deleted} past service program(s)")
    return deleted


@router.get("/programs/mine")
def get_my_programs(phone: str, db: Session = Depends(get_db)):
    """Get programs where the member (by phone) is listed as a participant"""
    _cleanup_past_programs(db)

    if not phone:
        return []

    # Find all members with this phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member_names = set()
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member_names.add(m.full_name.lower())

    if not member_names:
        return []

    # Get all upcoming programs and filter by participant name match
    from sqlalchemy.orm import joinedload
    all_programs = db.query(ServiceProgram).options(
        joinedload(ServiceProgram.created_by)
    ).order_by(ServiceProgram.service_date).all()

    result = []
    for p in all_programs:
        participants = json.loads(p.participants) if isinstance(p.participants, str) else (p.participants or [])
        for pt in participants:
            if pt.get("name", "").lower() in member_names:
                result.append(_program_to_dict(p, db=db))
                break

    return result


@router.get("/programs/today")
def get_todays_programs(db: Session = Depends(get_db)):
    """Public endpoint: get today's service program(s). Auto-cleans past programs."""
    _cleanup_past_programs(db)

    today = date.today()
    # Only return published onsite programs via public API
    programs = db.query(ServiceProgram).filter(
        ServiceProgram.service_date == today,
        ServiceProgram.location_type.in_(["onsite", None]),
        ServiceProgram.status == "published"
    ).order_by(ServiceProgram.id).all()

    return {
        "date": today.isoformat(),
        "programs": [_program_to_dict(p, public=True, db=db) for p in programs]
    }


@router.get("/admin/programs")
def get_all_programs(db: Session = Depends(get_db)):
    """Admin: list all programs (upcoming and today)"""
    _cleanup_past_programs(db)

    from sqlalchemy.orm import joinedload
    programs = db.query(ServiceProgram).options(joinedload(ServiceProgram.created_by)).order_by(ServiceProgram.service_date).all()
    return [_program_to_dict(p, db=db) for p in programs]


@router.get("/admin/programs/{program_id}")
def get_program(program_id: int, db: Session = Depends(get_db)):
    """Admin: get a single program by ID"""
    from sqlalchemy.orm import joinedload
    program = db.query(ServiceProgram).options(joinedload(ServiceProgram.created_by)).filter(ServiceProgram.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    return _program_to_dict(program, db=db)


def _get_prayer_points_for_role(prayer_points: list, role: str) -> list:
    """Get prayer points linked to a specific activity/role.
    Returns list of dicts {text, linked_activity} to preserve the link info for display.
    Prayer points can be plain strings (no link) or dicts with {text, linked_activity}."""
    if not prayer_points or not role:
        return []
    result = []
    role_lower = role.lower()
    for pp in prayer_points:
        if isinstance(pp, dict):
            linked = (pp.get("linked_activity") or "").lower()
            if linked and linked == role_lower:
                text = pp.get("text", "")
                if text:
                    result.append({"text": text, "linked_activity": pp.get("linked_activity", "")})
    return result


def _notify_program_participants(db: Session, program_title: str, service_date, participant_names: list, participant_roles: dict, created_by_name: str = None, program_id: int = None, prayer_points: list = None, admin_announcements: list = None, pastors_announcements: list = None):
    """Notify participants who are members in the database about their program roles.
    participant_roles is a dict mapping lowercase name -> list of role strings.
    A person with multiple roles gets one email summarising all their roles.
    Admin-role participants get admin_announcements; Preacher-role participants get pastors_announcements."""
    from notifications.dispatcher import dispatch_event
    from notifications.events import EventType

    if not participant_names:
        return

    # Strip title prefixes for DB lookup
    title_prefixes = ("pastor ", "elder ", "deacon ", "dr ", "mr ", "mrs ")
    def _strip_title(name):
        lower = name.lower()
        for prefix in title_prefixes:
            if lower.startswith(prefix):
                return name[len(prefix):]
        return name

    # Build lookup names (both titled and plain) for matching
    plain_names = [_strip_title(n).lower() for n in participant_names]

    # Find members whose names match participants (case-insensitive, using plain names)
    members = db.query(Member).filter(
        func.lower(Member.full_name).in_(plain_names)
    ).all()

    if not members:
        return

    date_str = service_date.strftime("%A, %d %B %Y") if hasattr(service_date, 'strftime') else str(service_date)

    # Build a role lookup that works with both titled and plain name keys
    def _get_roles_for_member(member):
        name_lower = member.full_name.lower()
        titled_lower = _get_titled_name(member).lower()
        return (participant_roles.get(name_lower)
                or participant_roles.get(titled_lower)
                or ["Participant"])

    # Role keywords that qualify for announcements
    admin_keywords = {"admin", "administrator", "mc", "emcee"}
    preacher_keywords = {"preach", "preacher", "preaching", "sermon", "pastor", "word", "minister", "ministering"}

    for member in members:
        if not member.email:
            continue
        roles = _get_roles_for_member(member)
        if isinstance(roles, str):
            roles = [roles]

        # Collect prayer points linked to any of this participant's roles
        linked_prayer_points = []
        for role in roles:
            linked_prayer_points.extend(_get_prayer_points_for_role(prayer_points or [], role))
        # Deduplicate by text (prayer points are now dicts with text + linked_activity)
        seen_texts = set()
        unique_prayer_points = []
        for pp in linked_prayer_points:
            text = pp.get("text", "") if isinstance(pp, dict) else pp
            if text and text not in seen_texts:
                seen_texts.add(text)
                unique_prayer_points.append(pp)

        # Determine which announcements this participant should receive
        roles_lower = {r.lower() for r in roles}
        member_admin_ann = []
        member_pastor_ann = []
        if admin_announcements and any(kw in rl for rl in roles_lower for kw in admin_keywords):
            member_admin_ann = admin_announcements
        if pastors_announcements and any(kw in rl for rl in roles_lower for kw in preacher_keywords):
            member_pastor_ann = pastors_announcements

        titled_name = _get_titled_name(member)
        try:
            dispatch_event(
                db=db,
                event_type=EventType.PROGRAM_PARTICIPANT_ADDED,
                data={
                    "title": program_title,
                    "service_date": date_str,
                    "roles": roles,
                    "role": ", ".join(roles),
                    "member_name": titled_name,
                    "member_email": member.email,
                    "member_id": member.id,
                    "member_phone": member.phone,
                    "created_by": created_by_name or "Admin",
                    "program_id": program_id,
                    "prayer_points": unique_prayer_points,
                    "admin_announcements": member_admin_ann,
                    "pastors_announcements": member_pastor_ann,
                },
                recipients=[{
                    "id": member.id,
                    "name": titled_name,
                    "email": member.email,
                    "phone": member.phone,
                }]
            )
        except Exception as e:
            print(f"Failed to notify participant {member.full_name}: {e}")


@router.post("/admin/programs")
def create_program(data: ServiceProgramCreate, request: Request, db: Session = Depends(get_db)):
    """Admin: create a new service program"""
    if not data.title:
        raise HTTPException(status_code=400, detail="Title is required")
    if not data.program_items:
        raise HTTPException(status_code=400, detail="At least one program item is required")

    program = ServiceProgram(
        title=data.title,
        service_date=data.service_date,
        location_type=data.location_type or "onsite",
        program_items=json.dumps([item.model_dump() for item in data.program_items]),
        participants=json.dumps([p.model_dump() for p in (data.participants or [])]),
        admin_announcements=json.dumps(data.admin_announcements or []),
        pastors_announcements=json.dumps(data.pastors_announcements or []),
        prayer_points=json.dumps(data.prayer_points or []),
        created_by_member_id=data.created_by_member_id
    )
    db.add(program)
    db.commit()
    db.refresh(program)
    # Eager-load creator for dict conversion
    if program.created_by_member_id:
        from sqlalchemy.orm import joinedload
        program = db.query(ServiceProgram).options(joinedload(ServiceProgram.created_by)).filter(ServiceProgram.id == program.id).first()

    _log_admin_action(request, db, "create_program", "program", program.id, f"Created program '{program.title}' for {program.service_date}")
    db.commit()

    # Emails are NOT sent on create — only when the program is published
    return _program_to_dict(program, db=db)


def _check_program_edit_permission(db: Session, program: ServiceProgram, editor_member_id: int = None):
    """Check if a member has permission to edit a program.
    Returns None if allowed, raises HTTPException if not.
    If editor_member_id is None (admin panel), always allows."""
    if editor_member_id is None:
        return  # Admin panel - no restrictions

    member = db.query(Member).filter(Member.id == editor_member_id).first()
    if not member:
        raise HTTPException(status_code=403, detail="Editor not found")

    roles = []
    if member.leadership_roles:
        try:
            roles = json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
        except (ValueError, TypeError):
            roles = []

    # Admin role has full access
    if "admin" in roles:
        return

    # Creator can edit their own program
    if program.created_by_member_id == editor_member_id:
        return

    raise HTTPException(status_code=403, detail="You can only edit programs you created")


@router.put("/admin/programs/{program_id}")
def update_program(program_id: int, data: ServiceProgramUpdate, request: Request, editor_member_id: int = None, db: Session = Depends(get_db)):
    """Admin: update an existing program"""
    program = db.query(ServiceProgram).filter(ServiceProgram.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    _check_program_edit_permission(db, program, editor_member_id)

    if data.title is not None:
        program.title = data.title
    if data.service_date is not None:
        program.service_date = data.service_date
    if data.location_type is not None:
        program.location_type = data.location_type
    if data.program_items is not None:
        program.program_items = json.dumps([item.model_dump() for item in data.program_items])
    if data.participants is not None:
        program.participants = json.dumps([p.model_dump() for p in data.participants])
    if data.admin_announcements is not None:
        program.admin_announcements = json.dumps(data.admin_announcements)
    if data.pastors_announcements is not None:
        program.pastors_announcements = json.dumps(data.pastors_announcements)
    if data.prayer_points is not None:
        program.prayer_points = json.dumps(data.prayer_points)

    db.commit()
    db.refresh(program)

    # Eager-load creator for dict conversion
    from sqlalchemy.orm import joinedload
    program = db.query(ServiceProgram).options(joinedload(ServiceProgram.created_by)).filter(ServiceProgram.id == program.id).first()

    _log_admin_action(request, db, "update_program", "program", program_id, f"Updated program '{program.title}'")
    db.commit()

    # Emails are NOT sent on update — use publish or re-notify instead
    return _program_to_dict(program, db=db)


@router.delete("/admin/programs/{program_id}")
def delete_program(program_id: int, request: Request, editor_member_id: int = None, db: Session = Depends(get_db)):
    """Admin: delete a program"""
    program = db.query(ServiceProgram).filter(ServiceProgram.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    _check_program_edit_permission(db, program, editor_member_id)

    prog_title = program.title
    db.delete(program)
    db.commit()

    _log_admin_action(request, db, "delete_program", "program", program_id, f"Deleted program '{prog_title}'")
    db.commit()

    return {"success": True}


def _send_program_notifications(db: Session, program: ServiceProgram):
    """Send email notifications to all participants of a program."""
    participants = json.loads(program.participants) if isinstance(program.participants, str) else (program.participants or [])
    if not participants:
        return

    names = list({p.get("name", "") for p in participants})
    roles = {}
    for p in participants:
        roles.setdefault((p.get("name") or "").lower(), []).append(p.get("role", ""))

    from sqlalchemy.orm import joinedload
    prog = db.query(ServiceProgram).options(joinedload(ServiceProgram.created_by)).filter(ServiceProgram.id == program.id).first()
    creator_name = _get_titled_name(prog.created_by) if prog and prog.created_by_member_id and prog.created_by else None

    pp = json.loads(program.prayer_points or "[]") if isinstance(program.prayer_points, str) else (program.prayer_points or [])
    admin_ann = json.loads(program.admin_announcements or "[]") if isinstance(program.admin_announcements, str) else (program.admin_announcements or [])
    pastor_ann = json.loads(program.pastors_announcements or "[]") if isinstance(program.pastors_announcements, str) else (program.pastors_announcements or [])

    _notify_program_participants(
        db, program.title, program.service_date, names, roles,
        created_by_name=creator_name, program_id=program.id,
        prayer_points=pp, admin_announcements=admin_ann, pastors_announcements=pastor_ann
    )


def _send_published_copy_to_manager(db: Session, program: ServiceProgram):
    """Send the service manager (creator) a copy of the finalised program."""
    if not program.created_by_member_id:
        return
    from sqlalchemy.orm import joinedload
    prog = db.query(ServiceProgram).options(joinedload(ServiceProgram.created_by)).filter(ServiceProgram.id == program.id).first()
    if not prog or not prog.created_by or not prog.created_by.email:
        return

    manager = prog.created_by
    manager_name = _get_titled_name(manager)
    manager_email = manager.email

    # Build program summary
    items = json.loads(program.program_items or "[]") if isinstance(program.program_items, str) else (program.program_items or [])
    participants = json.loads(program.participants or "[]") if isinstance(program.participants, str) else (program.participants or [])
    admin_ann = json.loads(program.admin_announcements or "[]") if isinstance(program.admin_announcements, str) else (program.admin_announcements or [])
    pastor_ann = json.loads(program.pastors_announcements or "[]") if isinstance(program.pastors_announcements, str) else (program.pastors_announcements or [])
    prayer_pts = json.loads(program.prayer_points or "[]") if isinstance(program.prayer_points, str) else (program.prayer_points or [])

    svc_date = program.service_date
    if hasattr(svc_date, 'strftime'):
        date_str = svc_date.strftime('%A, %d %B %Y')
    else:
        date_str = str(svc_date)

    FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    TEXT = "#111827"
    MUTED = "#9ca3af"
    BORDER = "#f3f4f6"

    # Build order of service rows
    items_html = ""
    for it in items:
        t = it.get("time", "")
        label = it.get("item", "")
        if label.lower() == "close":
            continue
        # Format time
        if t:
            try:
                from datetime import datetime as _dt
                items_html += f'<tr><td style="padding:4px 12px 4px 0;color:{MUTED};font-family:monospace;font-size:13px;vertical-align:top;white-space:nowrap;">{_dt.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0")}</td><td style="padding:4px 0;color:{TEXT};font-size:14px;">{label}</td></tr>'
            except ValueError:
                items_html += f'<tr><td style="padding:4px 12px 4px 0;color:{MUTED};font-family:monospace;font-size:13px;vertical-align:top;">{t}</td><td style="padding:4px 0;color:{TEXT};font-size:14px;">{label}</td></tr>'
        else:
            items_html += f'<tr><td></td><td style="padding:4px 0;color:{TEXT};font-size:14px;">{label}</td></tr>'

    # Build participants rows
    parts_html = ""
    for pt in participants:
        parts_html += f'<tr><td style="padding:3px 12px 3px 0;color:{MUTED};font-size:13px;vertical-align:top;white-space:nowrap;">{pt.get("role", "")}</td><td style="padding:3px 0;color:{TEXT};font-size:14px;">{pt.get("name", "")}</td></tr>'

    # Build announcements
    ann_html = ""
    if admin_ann:
        ann_html += f'<p style="margin:16px 0 6px 0;font-size:11px;font-weight:600;color:{MUTED};text-transform:uppercase;letter-spacing:0.5px;">Admin Announcements</p>'
        for a in admin_ann:
            ann_html += f'<p style="margin:0 0 4px 0;color:{TEXT};font-size:14px;padding-left:12px;border-left:2px solid {BORDER};">{a}</p>'
    if pastor_ann:
        ann_html += f'<p style="margin:16px 0 6px 0;font-size:11px;font-weight:600;color:{MUTED};text-transform:uppercase;letter-spacing:0.5px;">Pastor\'s Announcements</p>'
        for a in pastor_ann:
            ann_html += f'<p style="margin:0 0 4px 0;color:{TEXT};font-size:14px;padding-left:12px;border-left:2px solid {BORDER};">{a}</p>'

    # Build prayer points
    prayer_html = ""
    if prayer_pts:
        prayer_html += f'<p style="margin:16px 0 6px 0;font-size:11px;font-weight:600;color:{MUTED};text-transform:uppercase;letter-spacing:0.5px;">Prayer Points</p>'
        for pp in prayer_pts:
            if isinstance(pp, dict):
                text = pp.get("text", "")
                linked = pp.get("linked_activity", "")
                linked_tag = f' <span style="font-size:12px;color:{MUTED};font-style:italic;">({linked})</span>' if linked else ""
                prayer_html += f'<p style="margin:0 0 4px 0;color:{TEXT};font-size:14px;padding-left:12px;border-left:2px solid {BORDER};">{text}{linked_tag}</p>'
            elif pp:
                prayer_html += f'<p style="margin:0 0 4px 0;color:{TEXT};font-size:14px;padding-left:12px;border-left:2px solid {BORDER};">{pp}</p>'

    html = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:{FONT};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;">
  <tr><td>
    <h2 style="margin:0 0 4px 0;color:{TEXT};font-size:18px;font-weight:700;">{program.title}</h2>
    <p style="margin:0 0 6px 0;color:{MUTED};font-size:14px;">{date_str}</p>
    <p style="margin:0 0 20px 0;color:#059669;font-size:13px;font-weight:600;">Published — notifications sent to all participants</p>

    <p style="margin:0 0 6px 0;font-size:11px;font-weight:600;color:{MUTED};text-transform:uppercase;letter-spacing:0.5px;">Order of Service</p>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:8px;">{items_html}</table>

    <hr style="border:none;border-top:1px solid {BORDER};margin:16px 0;">
    <p style="margin:0 0 6px 0;font-size:11px;font-weight:600;color:{MUTED};text-transform:uppercase;letter-spacing:0.5px;">Participants</p>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0">{parts_html}</table>

    {ann_html}
    {prayer_html}

    <hr style="border:none;border-top:1px solid {BORDER};margin:20px 0 16px 0;">
    <p style="margin:0;color:{MUTED};font-size:12px;">Revival Fire Ministries &middot; Stellenbosch</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>'''

    subject = f"Program Published: {program.title} — {date_str}"

    # Send via configured channel
    try:
        from notifications.dispatcher import get_email_settings
        settings = get_email_settings(db)
        is_resend = (os.getenv('RESEND_ENABLED', '').lower() == 'true') or settings.get('resend_enabled', 'false').lower() == 'true'
        is_smtp = (os.getenv('SMTP_ENABLED', '').lower() == 'true') or settings.get('smtp_enabled', 'false').lower() == 'true'

        if is_resend:
            from notifications.channels.resend import ResendChannel
            channel = ResendChannel(settings)
            if channel.is_configured():
                success, error = channel.send(manager_email, subject, html)
                if success:
                    print(f"Sent published program copy to service manager {manager_name} ({manager_email})")
                else:
                    print(f"Failed to send published copy to {manager_email}: {error}")
        elif is_smtp:
            from notifications.channels.email import EmailChannel
            channel = EmailChannel(settings)
            if channel.is_configured():
                success, error = channel.send(manager_email, subject, html)
                if success:
                    print(f"Sent published program copy to service manager {manager_name} ({manager_email})")
                else:
                    print(f"Failed to send published copy to {manager_email}: {error}")
    except Exception as e:
        print(f"Failed to email program copy to service manager: {e}")


@router.post("/admin/programs/{program_id}/publish")
def publish_program(program_id: int, request: Request, editor_member_id: int = None, db: Session = Depends(get_db)):
    """Admin: publish a program and send notifications to all participants"""
    program = db.query(ServiceProgram).filter(ServiceProgram.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    _check_program_edit_permission(db, program, editor_member_id)

    program.status = "published"
    db.commit()
    db.refresh(program)

    # Send notifications to all participants
    _send_program_notifications(db, program)

    # Send service manager a copy of the final program
    _send_published_copy_to_manager(db, program)

    _log_admin_action(request, db, "publish_program", "program", program_id, f"Published program '{program.title}'")
    db.commit()

    return _program_to_dict(program, db=db)


@router.post("/admin/programs/{program_id}/unpublish")
def unpublish_program(program_id: int, request: Request, editor_member_id: int = None, db: Session = Depends(get_db)):
    """Admin: unpublish a program (back to draft)"""
    program = db.query(ServiceProgram).filter(ServiceProgram.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    _check_program_edit_permission(db, program, editor_member_id)

    program.status = "draft"
    db.commit()
    db.refresh(program)

    _log_admin_action(request, db, "unpublish_program", "program", program_id, f"Unpublished program '{program.title}'")
    db.commit()

    return _program_to_dict(program, db=db)


@router.post("/admin/programs/{program_id}/notify")
def renotify_program(program_id: int, request: Request, editor_member_id: int = None, db: Session = Depends(get_db)):
    """Admin: re-send notifications to all participants of a published program"""
    program = db.query(ServiceProgram).filter(ServiceProgram.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    _check_program_edit_permission(db, program, editor_member_id)

    _send_program_notifications(db, program)

    _log_admin_action(request, db, "notify_program", "program", program_id, f"Re-sent notifications for program '{program.title}'")
    db.commit()

    return {"success": True, "message": "Notifications sent to all participants"}


# ============ PROGRAM TEMPLATE ENDPOINTS ============

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _template_to_dict(template: ProgramTemplate) -> dict:
    """Convert a ProgramTemplate model to response dict"""
    ts = template.updated_at or template.created_at
    hashcode = str(int(ts.timestamp())) if ts else "0"

    def _parse_json(val):
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return []
        return val or []

    return {
        "id": template.id,
        "hash": hashcode,
        "title": template.title,
        "day_of_week": template.day_of_week,
        "day_name": DAY_NAMES[template.day_of_week] if 0 <= template.day_of_week <= 6 else "Unknown",
        "program_items": _parse_json(template.program_items),
        "participants": _parse_json(template.participants),
        "admin_announcements": _parse_json(template.admin_announcements),
        "pastors_announcements": _parse_json(template.pastors_announcements),
        "prayer_points": _parse_json(template.prayer_points),
        "support_roles": _parse_json(template.support_roles),
        "location_type": template.location_type or "onsite",
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None
    }


@router.get("/programs/templates")
def get_public_templates(day: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Public endpoint: get program templates, optionally filtered by day name or number.
    Examples: /api/programs/templates?day=sunday, /api/programs/templates?day=6
    If no day param, returns all templates."""
    query = db.query(ProgramTemplate)

    if day is not None:
        # Try as day name first
        day_lower = day.strip().lower()
        day_num = None
        for i, name in enumerate(DAY_NAMES):
            if name.lower() == day_lower:
                day_num = i
                break
        # Try as number
        if day_num is None:
            try:
                day_num = int(day)
            except ValueError:
                pass

        if day_num is not None and 0 <= day_num <= 6:
            query = query.filter(ProgramTemplate.day_of_week == day_num)
        else:
            return {"templates": []}

    templates = query.order_by(ProgramTemplate.day_of_week, ProgramTemplate.title).all()
    return {"templates": [_template_to_dict(t) for t in templates]}


@router.get("/programs/templates/today")
def get_todays_templates(db: Session = Depends(get_db)):
    """Public endpoint: get templates for today's day of week."""
    today_dow = date.today().weekday()  # 0=Monday ... 6=Sunday
    templates = db.query(ProgramTemplate).filter(
        ProgramTemplate.day_of_week == today_dow
    ).order_by(ProgramTemplate.title).all()

    return {
        "date": date.today().isoformat(),
        "day_of_week": today_dow,
        "day_name": DAY_NAMES[today_dow],
        "templates": [_template_to_dict(t) for t in templates]
    }


@router.get("/admin/templates")
def get_all_templates(db: Session = Depends(get_db)):
    """Admin: list all program templates"""
    templates = db.query(ProgramTemplate).order_by(
        ProgramTemplate.day_of_week, ProgramTemplate.title
    ).all()
    return [_template_to_dict(t) for t in templates]


@router.get("/admin/templates/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db)):
    """Admin: get a single template"""
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return _template_to_dict(template)


@router.post("/admin/templates")
def create_template(data: ProgramTemplateCreate, request: Request, db: Session = Depends(get_db)):
    """Admin: create a new program template"""
    if not data.title:
        raise HTTPException(status_code=400, detail="Title is required")
    if data.day_of_week < 0 or data.day_of_week > 6:
        raise HTTPException(status_code=400, detail="day_of_week must be 0 (Monday) to 6 (Sunday)")

    template = ProgramTemplate(
        title=data.title,
        day_of_week=data.day_of_week,
        location_type=data.location_type or "onsite",
        program_items=json.dumps([item.model_dump() for item in (data.program_items or [])]),
        participants=json.dumps([p.model_dump() for p in (data.participants or [])]),
        admin_announcements=json.dumps(data.admin_announcements or []),
        pastors_announcements=json.dumps(data.pastors_announcements or []),
        prayer_points=json.dumps(data.prayer_points or []),
        support_roles=json.dumps(data.support_roles or [])
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    _log_admin_action(request, db, "create_template", "template", template.id, f"Created template '{template.title}'")
    db.commit()

    return _template_to_dict(template)


@router.put("/admin/templates/{template_id}")
def update_template(template_id: int, data: ProgramTemplateUpdate, request: Request, db: Session = Depends(get_db)):
    """Admin: update a template"""
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if data.title is not None:
        template.title = data.title
    if data.day_of_week is not None:
        if data.day_of_week < 0 or data.day_of_week > 6:
            raise HTTPException(status_code=400, detail="day_of_week must be 0 (Monday) to 6 (Sunday)")
        template.day_of_week = data.day_of_week
    if data.location_type is not None:
        template.location_type = data.location_type
    if data.program_items is not None:
        template.program_items = json.dumps([item.model_dump() for item in data.program_items])
    if data.participants is not None:
        template.participants = json.dumps([p.model_dump() for p in data.participants])
    if data.admin_announcements is not None:
        template.admin_announcements = json.dumps(data.admin_announcements)
    if data.pastors_announcements is not None:
        template.pastors_announcements = json.dumps(data.pastors_announcements)
    if data.prayer_points is not None:
        template.prayer_points = json.dumps(data.prayer_points)
    if data.support_roles is not None:
        template.support_roles = json.dumps(data.support_roles)

    db.commit()
    db.refresh(template)

    _log_admin_action(request, db, "update_template", "template", template_id, f"Updated template '{template.title}'")
    db.commit()

    return _template_to_dict(template)


@router.delete("/admin/templates/{template_id}")
def delete_template(template_id: int, request: Request, db: Session = Depends(get_db)):
    """Admin: delete a template"""
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    tmpl_title = template.title
    db.delete(template)
    db.commit()

    _log_admin_action(request, db, "delete_template", "template", template_id, f"Deleted template '{tmpl_title}'")
    db.commit()

    return {"success": True}


# ============ SERVICE SCHEDULE ENDPOINTS ============

DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _schedule_to_dict(schedule: ServiceSchedule) -> dict:
    """Convert a ServiceSchedule model to response dict."""
    svc_date = schedule.service_date
    template = schedule.template
    manager = schedule.service_manager
    program = schedule.program

    return {
        "id": schedule.id,
        "service_date": svc_date.isoformat(),
        "day_of_week": DAY_LABELS[svc_date.weekday()] if svc_date else None,
        "template_id": schedule.template_id,
        "template_name": template.title if template else None,
        "service_manager_id": schedule.service_manager_id,
        "service_manager_name": _get_titled_name(manager) if manager else None,
        "notes": schedule.notes,
        "notified_at": schedule.notified_at.isoformat() if schedule.notified_at else None,
        "reminded_at": schedule.reminded_at.isoformat() if schedule.reminded_at else None,
        "program_id": schedule.program_id,
        "program_status": program.status if program else None,
        "has_program": schedule.program_id is not None,
        "created_at": schedule.created_at.isoformat() if schedule.created_at else None,
    }


@router.get("/admin/schedules")
def get_schedules(db: Session = Depends(get_db)):
    """Admin: list all upcoming service schedules"""
    today = date.today()
    schedules = db.query(ServiceSchedule).options(
        joinedload(ServiceSchedule.template),
        joinedload(ServiceSchedule.service_manager),
        joinedload(ServiceSchedule.program)
    ).filter(ServiceSchedule.service_date >= today).order_by(ServiceSchedule.service_date).all()
    return [_schedule_to_dict(s) for s in schedules]


@router.get("/admin/schedules/all")
def get_all_schedules(
    from_date: str = None,
    to_date: str = None,
    template_id: int = None,
    manager_id: int = None,
    status: str = None,
    db: Session = Depends(get_db)
):
    """Admin: list all service schedules with optional filters"""
    query = db.query(ServiceSchedule).options(
        joinedload(ServiceSchedule.template),
        joinedload(ServiceSchedule.service_manager),
        joinedload(ServiceSchedule.program)
    )

    if from_date:
        query = query.filter(ServiceSchedule.service_date >= from_date)
    if to_date:
        query = query.filter(ServiceSchedule.service_date <= to_date)
    if template_id:
        query = query.filter(ServiceSchedule.template_id == template_id)
    if manager_id:
        query = query.filter(ServiceSchedule.service_manager_id == manager_id)
    if status == "has_program":
        query = query.filter(ServiceSchedule.program_id != None)
    elif status == "no_program":
        query = query.filter(ServiceSchedule.program_id == None)
    elif status == "published":
        query = query.join(ServiceProgram, ServiceSchedule.program_id == ServiceProgram.id).filter(ServiceProgram.status == "published")
    elif status == "draft":
        query = query.join(ServiceProgram, ServiceSchedule.program_id == ServiceProgram.id).filter(ServiceProgram.status == "draft")

    schedules = query.order_by(ServiceSchedule.service_date.desc()).all()
    return [_schedule_to_dict(s) for s in schedules]


@router.post("/admin/schedules")
def create_schedule(data: ServiceScheduleCreate, request: Request, db: Session = Depends(get_db)):
    """Admin: schedule a future service"""
    # Validate service manager exists
    manager = db.query(Member).filter(Member.id == data.service_manager_id).first()
    if not manager:
        raise HTTPException(status_code=404, detail="Service manager not found")

    # Validate template if provided
    if data.template_id:
        template = db.query(ProgramTemplate).filter(ProgramTemplate.id == data.template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

    # Check for duplicate date
    existing = db.query(ServiceSchedule).filter(ServiceSchedule.service_date == data.service_date).first()
    if existing:
        raise HTTPException(status_code=400, detail="A service is already scheduled for this date")

    schedule = ServiceSchedule(
        service_date=data.service_date,
        template_id=data.template_id,
        service_manager_id=data.service_manager_id,
        notes=data.notes
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    # Eager-load relationships
    schedule = db.query(ServiceSchedule).options(
        joinedload(ServiceSchedule.template),
        joinedload(ServiceSchedule.service_manager),
        joinedload(ServiceSchedule.program)
    ).filter(ServiceSchedule.id == schedule.id).first()

    _log_admin_action(request, db, "create_schedule", "schedule", schedule.id, f"Scheduled service for {data.service_date}")
    db.commit()

    return _schedule_to_dict(schedule)


@router.put("/admin/schedules/{schedule_id}")
def update_schedule(schedule_id: int, data: ServiceScheduleUpdate, request: Request, db: Session = Depends(get_db)):
    """Admin: update a service schedule"""
    schedule = db.query(ServiceSchedule).filter(ServiceSchedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if data.service_date is not None:
        # Check duplicate
        existing = db.query(ServiceSchedule).filter(
            ServiceSchedule.service_date == data.service_date,
            ServiceSchedule.id != schedule_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="A service is already scheduled for this date")
        schedule.service_date = data.service_date

    if data.template_id is not None:
        schedule.template_id = data.template_id
    if data.service_manager_id is not None:
        manager = db.query(Member).filter(Member.id == data.service_manager_id).first()
        if not manager:
            raise HTTPException(status_code=404, detail="Service manager not found")
        schedule.service_manager_id = data.service_manager_id
        # Reset notification state if manager changed
        schedule.notified_at = None
        schedule.reminded_at = None
    if data.notes is not None:
        schedule.notes = data.notes

    db.commit()
    db.refresh(schedule)

    schedule = db.query(ServiceSchedule).options(
        joinedload(ServiceSchedule.template),
        joinedload(ServiceSchedule.service_manager),
        joinedload(ServiceSchedule.program)
    ).filter(ServiceSchedule.id == schedule.id).first()

    _log_admin_action(request, db, "update_schedule", "schedule", schedule_id, f"Updated schedule for {schedule.service_date}")
    db.commit()

    return _schedule_to_dict(schedule)


@router.delete("/admin/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, request: Request, db: Session = Depends(get_db)):
    """Admin: delete a service schedule"""
    schedule = db.query(ServiceSchedule).filter(ServiceSchedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    svc_date = str(schedule.service_date)
    db.delete(schedule)
    db.commit()

    _log_admin_action(request, db, "delete_schedule", "schedule", schedule_id, f"Deleted schedule for {svc_date}")
    db.commit()

    return {"success": True}


@router.get("/api/my-schedules")
def get_my_schedules(phone: str, db: Session = Depends(get_db)):
    """Get upcoming scheduled services for a service manager by phone"""
    members = db.query(Member).filter(Member.phone == phone).all()
    if not members:
        return []
    member_ids = [m.id for m in members]
    today = date.today()
    schedules = db.query(ServiceSchedule).options(
        joinedload(ServiceSchedule.template),
        joinedload(ServiceSchedule.service_manager),
        joinedload(ServiceSchedule.program)
    ).filter(
        ServiceSchedule.service_manager_id.in_(member_ids),
        ServiceSchedule.service_date >= today
    ).order_by(ServiceSchedule.service_date).all()
    return [_schedule_to_dict(s) for s in schedules]



# ============================================================================
# HOME CHURCH ROSTER
# ============================================================================

# Home Church dept matching — case-insensitive, tolerates variants like
# "Home Church Leadership" vs "Home Church Leaders", "Home Church committee"
# (lowercase c). We match by keyword so church renaming doesn't break things.
HOME_CHURCH_ROLE_KEYWORDS = {
    # role -> list of keywords that must appear (any one) alongside "home church"
    "committee": ["committee"],
    "leaders": ["leader", "leadership"],
    "preachers": ["preacher", "preaching"],
}


def _home_church_dept_ids(db: Session, role: str) -> List[int]:
    """Return department IDs for a given home-church role (committee/leaders/preachers).
    Case-insensitive match against 'home church <keyword>'."""
    keywords = HOME_CHURCH_ROLE_KEYWORDS.get(role, [])
    if not keywords:
        return []
    # Fetch all departments once and filter in Python so we can do case-insensitive
    # substring matching that works across SQLite/Postgres collations.
    ids = []
    for d in db.query(Department).all():
        name = (d.name or "").lower()
        if "home church" in name and any(k in name for k in keywords):
            ids.append(d.id)
    return ids


def _preacher_pool_dept_ids(db: Session) -> List[int]:
    """Preachers may come from any of the three Home Church departments."""
    seen = set()
    out = []
    for role in ("preachers", "committee", "leaders"):
        for d_id in _home_church_dept_ids(db, role):
            if d_id not in seen:
                seen.add(d_id)
                out.append(d_id)
    return out


def _is_committee_member(db: Session, member: Member) -> bool:
    if not member:
        return False
    dept_ids = _home_church_dept_ids(db, "committee")
    if not dept_ids:
        return False
    count = db.query(MemberDepartment).filter(
        MemberDepartment.member_id == member.id,
        MemberDepartment.department_id.in_(dept_ids),
        MemberDepartment.status == "approved",
    ).count()
    return count > 0


def _require_committee_or_admin(request: Request, db: Session) -> Optional[Member]:
    """Return acting member or raise 401/403. Admins pass through; otherwise requires committee."""
    from routers.pages import get_admin_identity, is_authenticated
    if is_authenticated(request):
        identity = get_admin_identity(request)
        if identity and identity.get("member_id"):
            m = db.query(Member).filter(Member.id == identity["member_id"]).first()
            if m:
                return m
        return None
    from routers.pages import MEMBER_COOKIE_NAME, _verify_member_session
    token = request.cookies.get(MEMBER_COOKIE_NAME)
    member_id = _verify_member_session(token)
    if not member_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member or not _is_committee_member(db, member):
        raise HTTPException(status_code=403, detail="Home Church Committee access required")
    return member


def _preacher_pool_member_ids(db: Session) -> List[int]:
    dept_ids = _preacher_pool_dept_ids(db)
    if not dept_ids:
        return []
    rows = db.query(MemberDepartment.member_id).filter(
        MemberDepartment.department_id.in_(dept_ids),
        MemberDepartment.status == "approved",
    ).distinct().all()
    return [r[0] for r in rows]


def _home_church_to_dict(hc: HomeChurch, db: Session) -> dict:
    leader = None
    if hc.leader_member_id:
        lm = db.query(Member).filter(Member.id == hc.leader_member_id).first()
        if lm:
            leader = {"id": lm.id, "full_name": lm.full_name, "phone": lm.phone, "email": lm.email}
    return {
        "id": hc.id,
        "name": hc.name,
        "leader_member_id": hc.leader_member_id,
        "leader": leader,
        "address": hc.address,
        "suburb": hc.suburb,
        "meeting_day": hc.meeting_day,
        "meeting_time": hc.meeting_time,
        "whatsapp_link": hc.whatsapp_link,
        "notes": hc.notes,
        "is_active": hc.is_active,
    }


def _program_type_to_dict(pt: HomeChurchProgramType) -> dict:
    return {
        "id": pt.id,
        "name": pt.name,
        "requires_preacher": pt.requires_preacher,
        "color": pt.color,
        "icon": pt.icon,
        "sort_order": pt.sort_order,
        "is_active": pt.is_active,
    }


def _roster_entry_to_dict(entry: HomeChurchRoster, db: Session) -> dict:
    preacher = None
    if entry.preacher_member_id:
        pm = db.query(Member).filter(Member.id == entry.preacher_member_id).first()
        if pm:
            preacher = {"id": pm.id, "full_name": pm.full_name, "phone": pm.phone, "email": pm.email}
    program_type = None
    if entry.program_type_id:
        pt = db.query(HomeChurchProgramType).filter(HomeChurchProgramType.id == entry.program_type_id).first()
        if pt:
            program_type = _program_type_to_dict(pt)
    return {
        "id": entry.id,
        "home_church_id": entry.home_church_id,
        "roster_date": entry.roster_date.isoformat() if entry.roster_date else None,
        "program_type_id": entry.program_type_id,
        "program_type": program_type,
        "preacher_member_id": entry.preacher_member_id,
        "preacher": preacher,
        "notes": entry.notes,
        "status": entry.status,
        "published_at": entry.published_at.isoformat() if entry.published_at else None,
    }


def _next_weekday(d: Optional[date] = None, weekday: int = 0) -> date:
    if d is None:
        d = date.today()
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta)


# ---- PROGRAM TYPES ----

@router.get("/home-church/program-types")
def list_program_types_public(db: Session = Depends(get_db)):
    types = db.query(HomeChurchProgramType).filter(
        HomeChurchProgramType.is_active == True
    ).order_by(HomeChurchProgramType.sort_order).all()
    return [_program_type_to_dict(t) for t in types]


@router.get("/admin/home-church/program-types")
def admin_list_program_types(request: Request, db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    types = db.query(HomeChurchProgramType).order_by(HomeChurchProgramType.sort_order).all()
    return [_program_type_to_dict(t) for t in types]


@router.post("/admin/home-church/program-types")
def admin_create_program_type(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    pt = HomeChurchProgramType(
        name=name,
        requires_preacher=bool(data.get("requires_preacher", False)),
        color=data.get("color") or "gray",
        icon=data.get("icon") or "\U0001F4CC",
        sort_order=int(data.get("sort_order", 99)),
        is_active=bool(data.get("is_active", True)),
    )
    db.add(pt)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="A program type with that name already exists")
    db.refresh(pt)
    _log_admin_action(request, db, "create_home_church_program_type", "home_church_program_type", pt.id, f"Created program type '{pt.name}'")
    db.commit()
    return _program_type_to_dict(pt)


@router.put("/admin/home-church/program-types/{pt_id}")
def admin_update_program_type(pt_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    pt = db.query(HomeChurchProgramType).filter(HomeChurchProgramType.id == pt_id).first()
    if not pt:
        raise HTTPException(status_code=404, detail="Program type not found")
    for field in ("name", "color", "icon"):
        if field in data and data[field] is not None:
            setattr(pt, field, data[field])
    if "requires_preacher" in data:
        pt.requires_preacher = bool(data["requires_preacher"])
    if "sort_order" in data:
        pt.sort_order = int(data["sort_order"])
    if "is_active" in data:
        pt.is_active = bool(data["is_active"])
    db.commit()
    _log_admin_action(request, db, "update_home_church_program_type", "home_church_program_type", pt.id, f"Updated program type '{pt.name}'")
    db.commit()
    return _program_type_to_dict(pt)


@router.delete("/admin/home-church/program-types/{pt_id}")
def admin_delete_program_type(pt_id: int, request: Request, db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    pt = db.query(HomeChurchProgramType).filter(HomeChurchProgramType.id == pt_id).first()
    if not pt:
        raise HTTPException(status_code=404, detail="Program type not found")
    name = pt.name
    used = db.query(HomeChurchRoster).filter(HomeChurchRoster.program_type_id == pt_id).count()
    if used > 0:
        pt.is_active = False
        db.commit()
        _log_admin_action(request, db, "deactivate_home_church_program_type", "home_church_program_type", pt_id, f"Deactivated '{name}' (referenced by {used} roster entries)")
        db.commit()
        return {"success": True, "deactivated": True}
    db.delete(pt)
    db.commit()
    _log_admin_action(request, db, "delete_home_church_program_type", "home_church_program_type", pt_id, f"Deleted program type '{name}'")
    db.commit()
    return {"success": True, "deleted": True}


# ---- HOME CHURCHES ----

@router.get("/admin/home-churches")
def admin_list_home_churches(request: Request, db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    churches = db.query(HomeChurch).order_by(HomeChurch.name).all()
    return [_home_church_to_dict(c, db) for c in churches]


@router.post("/admin/home-churches")
def admin_create_home_church(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Home church name is required")
    hc = HomeChurch(
        name=name,
        leader_member_id=data.get("leader_member_id"),
        address=data.get("address"),
        suburb=data.get("suburb"),
        meeting_day=int(data.get("meeting_day", 0)),
        meeting_time=data.get("meeting_time") or "19:00",
        whatsapp_link=data.get("whatsapp_link"),
        notes=data.get("notes"),
        is_active=bool(data.get("is_active", True)),
    )
    db.add(hc)
    db.commit()
    db.refresh(hc)
    _log_admin_action(request, db, "create_home_church", "home_church", hc.id, f"Created home church '{hc.name}'")
    db.commit()
    return _home_church_to_dict(hc, db)


@router.put("/admin/home-churches/{hc_id}")
def admin_update_home_church(hc_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    hc = db.query(HomeChurch).filter(HomeChurch.id == hc_id).first()
    if not hc:
        raise HTTPException(status_code=404, detail="Home church not found")
    for field in ("name", "address", "suburb", "meeting_time", "whatsapp_link", "notes"):
        if field in data:
            setattr(hc, field, data[field])
    if "leader_member_id" in data:
        hc.leader_member_id = data["leader_member_id"]
    if "meeting_day" in data and data["meeting_day"] is not None:
        hc.meeting_day = int(data["meeting_day"])
    if "is_active" in data:
        hc.is_active = bool(data["is_active"])
    db.commit()
    _log_admin_action(request, db, "update_home_church", "home_church", hc.id, f"Updated '{hc.name}'")
    db.commit()
    return _home_church_to_dict(hc, db)


@router.delete("/admin/home-churches/{hc_id}")
def admin_delete_home_church(hc_id: int, request: Request, db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    hc = db.query(HomeChurch).filter(HomeChurch.id == hc_id).first()
    if not hc:
        raise HTTPException(status_code=404, detail="Home church not found")
    name = hc.name
    db.delete(hc)
    db.commit()
    _log_admin_action(request, db, "delete_home_church", "home_church", hc_id, f"Deleted home church '{name}'")
    db.commit()
    return {"success": True}


# ---- PREACHER POOL ----

@router.get("/admin/home-church/preachers")
def admin_list_preachers(request: Request, db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    member_ids = _preacher_pool_member_ids(db)
    if not member_ids:
        return []
    members = db.query(Member).filter(Member.id.in_(member_ids), Member.is_active == True).order_by(Member.full_name).all()
    return [{"id": m.id, "full_name": m.full_name, "phone": m.phone, "email": m.email} for m in members]


# ---- ROSTER ----

@router.get("/admin/home-church/roster")
def admin_get_roster(
    request: Request,
    start_date: Optional[str] = Query(None),
    weeks: int = Query(8, ge=1, le=26),
    db: Session = Depends(get_db),
):
    _require_committee_or_admin(request, db)
    if start_date:
        try:
            start = date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date")
    else:
        start = _next_weekday()

    dates = [start + timedelta(days=7 * i) for i in range(weeks)]
    end_date = dates[-1]

    churches = db.query(HomeChurch).filter(HomeChurch.is_active == True).order_by(HomeChurch.name).all()
    entries = db.query(HomeChurchRoster).filter(
        HomeChurchRoster.roster_date >= start,
        HomeChurchRoster.roster_date <= end_date,
    ).all()

    entry_map = {}
    for e in entries:
        entry_map[(e.home_church_id, e.roster_date)] = e

    program_types = db.query(HomeChurchProgramType).filter(HomeChurchProgramType.is_active == True).order_by(HomeChurchProgramType.sort_order).all()

    matrix = []
    for c in churches:
        row = {"home_church": _home_church_to_dict(c, db), "cells": []}
        for d in dates:
            entry = entry_map.get((c.id, d))
            row["cells"].append({
                "date": d.isoformat(),
                "entry": _roster_entry_to_dict(entry, db) if entry else None,
            })
        matrix.append(row)

    return {
        "start_date": start.isoformat(),
        "end_date": end_date.isoformat(),
        "dates": [d.isoformat() for d in dates],
        "weeks": weeks,
        "home_churches": [_home_church_to_dict(c, db) for c in churches],
        "program_types": [_program_type_to_dict(t) for t in program_types],
        "matrix": matrix,
    }


@router.put("/admin/home-church/roster/cell")
def admin_upsert_roster_cell(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    try:
        hc_id = int(data["home_church_id"])
        roster_date = date.fromisoformat(data["roster_date"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="home_church_id and roster_date required")

    program_type_id = data.get("program_type_id")
    preacher_id = data.get("preacher_member_id")
    notes = data.get("notes")

    program_type = None
    if program_type_id:
        program_type = db.query(HomeChurchProgramType).filter(HomeChurchProgramType.id == program_type_id).first()
        if not program_type:
            raise HTTPException(status_code=404, detail="Program type not found")
        if not program_type.requires_preacher:
            preacher_id = None

    if preacher_id:
        preacher = db.query(Member).filter(Member.id == preacher_id).first()
        if not preacher:
            raise HTTPException(status_code=404, detail="Preacher not found")

    entry = db.query(HomeChurchRoster).filter(
        HomeChurchRoster.home_church_id == hc_id,
        HomeChurchRoster.roster_date == roster_date,
    ).first()

    is_new = entry is None
    if is_new:
        entry = HomeChurchRoster(
            home_church_id=hc_id,
            roster_date=roster_date,
            program_type_id=program_type_id,
            preacher_member_id=preacher_id,
            notes=notes,
            status="draft",
        )
        db.add(entry)
    else:
        entry.program_type_id = program_type_id
        entry.preacher_member_id = preacher_id
        if "notes" in data:
            entry.notes = notes

    db.commit()
    db.refresh(entry)

    _log_admin_action(
        request, db,
        "upsert_home_church_roster",
        "home_church_roster",
        entry.id,
        f"{'Created' if is_new else 'Updated'} roster for home church {hc_id} on {roster_date.isoformat()}"
    )
    db.commit()

    return _roster_entry_to_dict(entry, db)


@router.delete("/admin/home-church/roster/cell")
def admin_clear_roster_cell(
    request: Request,
    home_church_id: int = Query(...),
    roster_date: str = Query(...),
    db: Session = Depends(get_db),
):
    _require_committee_or_admin(request, db)
    try:
        d = date.fromisoformat(roster_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    entry = db.query(HomeChurchRoster).filter(
        HomeChurchRoster.home_church_id == home_church_id,
        HomeChurchRoster.roster_date == d,
    ).first()
    if not entry:
        return {"success": True, "removed": False}
    db.delete(entry)
    db.commit()
    _log_admin_action(request, db, "clear_home_church_roster", "home_church_roster", entry.id, f"Cleared roster for home church {home_church_id} on {d.isoformat()}")
    db.commit()
    return {"success": True, "removed": True}


@router.put("/admin/home-church/roster/week")
def admin_set_week_program(
    request: Request,
    data: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Cascade a single program type to every active home church for a given
    date. The common case: all home churches share the same programme each
    week. Creates missing roster entries and updates existing ones. If the
    new type doesn't require a preacher, preacher assignments are cleared.
    Already-published rows are left alone unless force=true."""
    _require_committee_or_admin(request, db)

    try:
        roster_date = date.fromisoformat(data["roster_date"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="roster_date required")

    program_type_id = data.get("program_type_id")
    force = bool(data.get("force", False))

    program_type = None
    if program_type_id:
        program_type = db.query(HomeChurchProgramType).filter(
            HomeChurchProgramType.id == program_type_id
        ).first()
        if not program_type:
            raise HTTPException(status_code=404, detail="Program type not found")

    churches = db.query(HomeChurch).filter(HomeChurch.is_active == True).all()
    existing = {
        e.home_church_id: e
        for e in db.query(HomeChurchRoster).filter(
            HomeChurchRoster.roster_date == roster_date
        ).all()
    }

    updated_count = 0
    created_count = 0
    skipped_published = 0

    for c in churches:
        entry = existing.get(c.id)
        if entry is None:
            entry = HomeChurchRoster(
                home_church_id=c.id,
                roster_date=roster_date,
                program_type_id=program_type_id,
                status="draft",
            )
            # Non-preaching type: no preacher
            if program_type and not program_type.requires_preacher:
                entry.preacher_member_id = None
            db.add(entry)
            created_count += 1
        else:
            if entry.status == "published" and not force:
                skipped_published += 1
                continue
            entry.program_type_id = program_type_id
            if program_type and not program_type.requires_preacher:
                entry.preacher_member_id = None
            updated_count += 1

    db.commit()

    _log_admin_action(
        request, db,
        "set_week_program",
        "home_church_roster",
        None,
        f"Set week {roster_date.isoformat()} to "
        f"{program_type.name if program_type else 'none'} "
        f"({created_count} created, {updated_count} updated, {skipped_published} published skipped)"
    )
    db.commit()

    return {
        "success": True,
        "created": created_count,
        "updated": updated_count,
        "skipped_published": skipped_published,
    }


@router.post("/admin/home-church/roster/auto-fill")
def admin_auto_fill_roster(request: Request, data: dict = Body(default={}), db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    start_date_str = data.get("start_date")
    weeks = int(data.get("weeks", 4))
    if start_date_str:
        try:
            start = date.fromisoformat(start_date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date")
    else:
        start = _next_weekday()
    end_date = start + timedelta(days=7 * weeks)

    entries = db.query(HomeChurchRoster).join(HomeChurchProgramType).filter(
        HomeChurchRoster.roster_date >= start,
        HomeChurchRoster.roster_date < end_date,
        HomeChurchProgramType.requires_preacher == True,
        HomeChurchRoster.preacher_member_id.is_(None),
    ).order_by(HomeChurchRoster.roster_date, HomeChurchRoster.home_church_id).all()

    if not entries:
        return {"success": True, "filled": 0, "message": "No empty preacher slots. Set program types first."}

    member_ids = _preacher_pool_member_ids(db)
    if not member_ids:
        raise HTTPException(status_code=400, detail="No preachers available in the pool")
    preachers = db.query(Member).filter(Member.id.in_(member_ids), Member.is_active == True).all()
    if not preachers:
        raise HTTPException(status_code=400, detail="No active preachers available")

    recent_assignments = {}
    existing = db.query(HomeChurchRoster).filter(
        HomeChurchRoster.roster_date >= start - timedelta(days=56),
        HomeChurchRoster.preacher_member_id.isnot(None),
    ).all()
    for e in existing:
        recent_assignments.setdefault(e.preacher_member_id, []).append(e.roster_date)

    hc_leader = {hc.id: hc.leader_member_id for hc in db.query(HomeChurch).all()}

    filled = 0
    for entry in entries:
        leader_id = hc_leader.get(entry.home_church_id)

        def score(p):
            count = len(recent_assignments.get(p.id, []))
            self_preach = 1 if p.id == leader_id else 0
            return (self_preach, count)

        candidates = sorted(preachers, key=score)
        chosen = candidates[0]
        entry.preacher_member_id = chosen.id
        recent_assignments.setdefault(chosen.id, []).append(entry.roster_date)
        filled += 1

    db.commit()
    _log_admin_action(request, db, "auto_fill_home_church_roster", "home_church_roster", None, f"Auto-filled {filled} preacher slots from {start.isoformat()} for {weeks} weeks")
    db.commit()
    return {"success": True, "filled": filled}


@router.post("/admin/home-church/roster/publish")
def admin_publish_roster(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    roster_date_str = data.get("roster_date")
    if not roster_date_str:
        raise HTTPException(status_code=400, detail="roster_date required")
    try:
        d = date.fromisoformat(roster_date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")

    entries = db.query(HomeChurchRoster).options(
        joinedload(HomeChurchRoster.home_church).joinedload(HomeChurch.leader),
        joinedload(HomeChurchRoster.program_type),
        joinedload(HomeChurchRoster.preacher),
    ).filter(HomeChurchRoster.roster_date == d).all()

    if not entries:
        raise HTTPException(status_code=404, detail="No roster entries found for that date")

    now = datetime.utcnow()
    published_count = 0
    for e in entries:
        if e.status != "published":
            published_count += 1
        e.status = "published"
        e.published_at = now
    db.commit()

    try:
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        for e in entries:
            hc = e.home_church
            if not hc or not hc.leader or not hc.leader.email:
                continue
            dispatch_event(db, EventType.HOME_CHURCH_ROSTER_PUBLISHED, {
                "leader_id": hc.leader.id,
                "leader_name": hc.leader.full_name,
                "leader_email": hc.leader.email,
                "home_church_name": hc.name,
                "roster_date": d.isoformat(),
                "meeting_time": hc.meeting_time,
                "program_type_name": e.program_type.name if e.program_type else "Not set",
                "program_type_icon": e.program_type.icon if e.program_type else "\U0001F4CC",
                "requires_preacher": e.program_type.requires_preacher if e.program_type else False,
                "preacher_name": e.preacher.full_name if e.preacher else None,
                "preacher_phone": e.preacher.phone if e.preacher else None,
                "recipients": [{"id": hc.leader.id, "name": hc.leader.full_name, "email": hc.leader.email, "phone": hc.leader.phone}],
            })

        for e in entries:
            if not e.preacher or not e.preacher.email:
                continue
            hc = e.home_church
            dispatch_event(db, EventType.HOME_CHURCH_PREACHER_ASSIGNED, {
                "preacher_id": e.preacher.id,
                "preacher_name": e.preacher.full_name,
                "preacher_email": e.preacher.email,
                "home_church_name": hc.name if hc else "",
                "home_church_address": hc.address if hc else "",
                "leader_name": hc.leader.full_name if (hc and hc.leader) else "",
                "leader_phone": hc.leader.phone if (hc and hc.leader) else "",
                "roster_date": d.isoformat(),
                "meeting_time": hc.meeting_time if hc else "19:00",
                "recipients": [{"id": e.preacher.id, "name": e.preacher.full_name, "email": e.preacher.email, "phone": e.preacher.phone}],
            })
    except Exception as exc:
        print(f"Failed to dispatch home church roster notifications: {exc}")

    _log_admin_action(request, db, "publish_home_church_roster", "home_church_roster", None, f"Published roster for {d.isoformat()} ({len(entries)} entries)")
    db.commit()

    return {"success": True, "date": d.isoformat(), "entries_published": len(entries), "newly_published": published_count}


# ---- PUBLIC (portal) ----

@router.get("/home-church/my-upcoming")
def my_upcoming_home_church(
    phone: str = Query(...),
    weeks: int = Query(8, ge=1, le=26),
    db: Session = Depends(get_db),
):
    member = _find_member_by_phone(db, phone)
    if not member:
        return {"as_leader": [], "as_preacher": []}

    today = date.today()
    horizon = today + timedelta(days=7 * weeks)

    led_churches = db.query(HomeChurch).filter(HomeChurch.leader_member_id == member.id).all()
    led_ids = [c.id for c in led_churches]
    as_leader = []
    if led_ids:
        entries = db.query(HomeChurchRoster).options(
            joinedload(HomeChurchRoster.home_church),
            joinedload(HomeChurchRoster.program_type),
            joinedload(HomeChurchRoster.preacher),
        ).filter(
            HomeChurchRoster.home_church_id.in_(led_ids),
            HomeChurchRoster.roster_date >= today,
            HomeChurchRoster.roster_date <= horizon,
            HomeChurchRoster.status == "published",
        ).order_by(HomeChurchRoster.roster_date).all()
        as_leader = [_roster_entry_to_dict(e, db) for e in entries]
        # attach home church info
        for i, e in enumerate(entries):
            as_leader[i]["home_church"] = _home_church_to_dict(e.home_church, db) if e.home_church else None

    entries = db.query(HomeChurchRoster).options(
        joinedload(HomeChurchRoster.home_church).joinedload(HomeChurch.leader),
        joinedload(HomeChurchRoster.program_type),
    ).filter(
        HomeChurchRoster.preacher_member_id == member.id,
        HomeChurchRoster.roster_date >= today,
        HomeChurchRoster.roster_date <= horizon,
        HomeChurchRoster.status == "published",
    ).order_by(HomeChurchRoster.roster_date).all()
    as_preacher = []
    for e in entries:
        d = _roster_entry_to_dict(e, db)
        hc = e.home_church
        d["home_church"] = _home_church_to_dict(hc, db) if hc else None
        as_preacher.append(d)

    return {"as_leader": as_leader, "as_preacher": as_preacher}



# ============================================================================
# HOME CHURCH ATTENDANCE (committee captures; leaders submit via WhatsApp)
# ============================================================================

def _attendance_to_dict(a: HomeChurchAttendance, db: Session) -> dict:
    submitted_by = None
    if a.submitted_by_member_id:
        m = db.query(Member).filter(Member.id == a.submitted_by_member_id).first()
        if m:
            submitted_by = {"id": m.id, "full_name": m.full_name}
    try:
        offering = float(a.offering_amount) if a.offering_amount else 0.0
    except (TypeError, ValueError):
        offering = 0.0
    return {
        "id": a.id,
        "home_church_id": a.home_church_id,
        "roster_date": a.roster_date.isoformat() if a.roster_date else None,
        "did_not_meet": bool(a.did_not_meet),
        "attendance_count": a.attendance_count or 0,
        "adults_count": a.adults_count,
        "children_count": a.children_count,
        "new_visitors_count": a.new_visitors_count or 0,
        "offering_amount": offering,
        "notes": a.notes,
        "submitted_by": submitted_by,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        "reminder_sent_at": a.reminder_sent_at.isoformat() if a.reminder_sent_at else None,
    }


@router.get("/admin/home-church/attendance")
def admin_list_attendance(
    request: Request,
    start_date: Optional[str] = Query(None),
    weeks: int = Query(8, ge=1, le=52),
    db: Session = Depends(get_db),
):
    """Return the attendance matrix for the given range: each active home church
    x each Monday, with its report (if captured) or a null placeholder."""
    _require_committee_or_admin(request, db)

    if start_date:
        try:
            start = date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date")
    else:
        # Default: start one week in the PAST so the committee can fill
        # in last Monday's numbers, working forward.
        start = _next_weekday() - timedelta(days=7)

    dates = [start + timedelta(days=7 * i) for i in range(weeks)]
    end_date = dates[-1]

    churches = db.query(HomeChurch).filter(HomeChurch.is_active == True).order_by(HomeChurch.name).all()
    reports = db.query(HomeChurchAttendance).filter(
        HomeChurchAttendance.roster_date >= start,
        HomeChurchAttendance.roster_date <= end_date,
    ).all()

    index = {(r.home_church_id, r.roster_date): r for r in reports}

    matrix = []
    for c in churches:
        row = {"home_church": _home_church_to_dict(c, db), "cells": []}
        for d in dates:
            r = index.get((c.id, d))
            row["cells"].append({
                "date": d.isoformat(),
                "report": _attendance_to_dict(r, db) if r else None,
            })
        matrix.append(row)

    return {
        "start_date": start.isoformat(),
        "end_date": end_date.isoformat(),
        "dates": [d.isoformat() for d in dates],
        "weeks": weeks,
        "home_churches": [_home_church_to_dict(c, db) for c in churches],
        "matrix": matrix,
    }


@router.put("/admin/home-church/attendance")
def admin_upsert_attendance(
    request: Request,
    data: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Committee captures (or updates) attendance for a given home church / date."""
    actor = _require_committee_or_admin(request, db)

    try:
        hc_id = int(data["home_church_id"])
        d = date.fromisoformat(data["roster_date"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="home_church_id and roster_date required")

    hc = db.query(HomeChurch).filter(HomeChurch.id == hc_id).first()
    if not hc:
        raise HTTPException(status_code=404, detail="Home church not found")

    did_not_meet = bool(data.get("did_not_meet", False))
    attendance_count = 0
    adults = None
    children = None
    new_visitors = 0
    offering_str = "0"
    notes = data.get("notes")

    if not did_not_meet:
        try:
            attendance_count = int(data.get("attendance_count") or 0)
            if data.get("adults_count") not in (None, ""):
                adults = int(data["adults_count"])
            if data.get("children_count") not in (None, ""):
                children = int(data["children_count"])
            new_visitors = int(data.get("new_visitors_count") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Counts must be whole numbers")
        # Offering as plain text (we store and display; avoids float rounding)
        offering_raw = data.get("offering_amount", "0")
        if offering_raw is None:
            offering_raw = "0"
        try:
            # Validate parseable
            float(str(offering_raw).replace(",", "").strip() or "0")
        except ValueError:
            raise HTTPException(status_code=400, detail="Offering must be a number")
        offering_str = str(offering_raw)

    entry = db.query(HomeChurchAttendance).filter(
        HomeChurchAttendance.home_church_id == hc_id,
        HomeChurchAttendance.roster_date == d,
    ).first()

    is_new = entry is None
    if is_new:
        entry = HomeChurchAttendance(
            home_church_id=hc_id,
            roster_date=d,
            did_not_meet=did_not_meet,
            attendance_count=attendance_count,
            adults_count=adults,
            children_count=children,
            new_visitors_count=new_visitors,
            offering_amount=offering_str,
            notes=notes,
            submitted_by_member_id=actor.id if actor else None,
        )
        db.add(entry)
    else:
        entry.did_not_meet = did_not_meet
        entry.attendance_count = attendance_count
        entry.adults_count = adults
        entry.children_count = children
        entry.new_visitors_count = new_visitors
        entry.offering_amount = offering_str
        entry.notes = notes
        if actor:
            entry.submitted_by_member_id = actor.id

    db.commit()
    db.refresh(entry)

    _log_admin_action(
        request, db,
        "upsert_home_church_attendance",
        "home_church_attendance",
        entry.id,
        f"{'Captured' if is_new else 'Updated'} {hc.name} attendance for {d.isoformat()} (attendance={attendance_count}, offering={offering_str})"
    )
    db.commit()

    return _attendance_to_dict(entry, db)


@router.delete("/admin/home-church/attendance/{attendance_id}")
def admin_delete_attendance(attendance_id: int, request: Request, db: Session = Depends(get_db)):
    _require_committee_or_admin(request, db)
    entry = db.query(HomeChurchAttendance).filter(HomeChurchAttendance.id == attendance_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Not found")
    hc = entry.home_church
    d = entry.roster_date
    db.delete(entry)
    db.commit()
    _log_admin_action(request, db, "delete_home_church_attendance", "home_church_attendance", attendance_id,
                     f"Deleted attendance for {hc.name if hc else '?'} on {d.isoformat() if d else '?'}")
    db.commit()
    return {"success": True}


@router.get("/admin/home-church/attendance/stats")
def admin_attendance_stats(
    request: Request,
    end_date: Optional[str] = Query(None, description="ISO date; defaults to today. Analyses the 8 weeks ending on the Monday <= this date."),
    db: Session = Depends(get_db),
):
    """Weekly totals, monthly averages, and improving / attention flags.

    Compares the most recent 4 Mondays against the prior 4 for each home church.
    """
    _require_committee_or_admin(request, db)

    if end_date:
        try:
            end = date.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date")
    else:
        end = date.today()

    # Anchor on the most recent Monday <= end
    # Python: weekday() returns 0 for Monday
    anchor_monday = end - timedelta(days=end.weekday())

    # Get the 8 recent Mondays (anchor going backwards)
    recent = [anchor_monday - timedelta(days=7 * i) for i in range(8)]  # [0]=latest
    recent.reverse()  # oldest first
    first_day = recent[0]
    last_day = recent[-1]

    churches = db.query(HomeChurch).filter(HomeChurch.is_active == True).order_by(HomeChurch.name).all()
    reports = db.query(HomeChurchAttendance).filter(
        HomeChurchAttendance.roster_date >= first_day,
        HomeChurchAttendance.roster_date <= last_day,
    ).all()
    by_hc = {}
    for r in reports:
        by_hc.setdefault(r.home_church_id, {})[r.roster_date] = r

    # Weekly totals across all churches
    weekly_totals = []
    for d in recent:
        week = {"date": d.isoformat(), "attendance": 0, "offering": 0.0, "reporting": 0, "total": len(churches)}
        for c in churches:
            r = by_hc.get(c.id, {}).get(d)
            if r and not r.did_not_meet:
                week["attendance"] += r.attendance_count or 0
                try:
                    week["offering"] += float(r.offering_amount or 0)
                except (TypeError, ValueError):
                    pass
            if r:
                week["reporting"] += 1
        weekly_totals.append(week)

    # Per-church stats: 4-week recent vs 4-week prior, monthly average, flags
    per_church = []
    def _safe_avg(vals):
        return (sum(vals) / len(vals)) if vals else 0.0

    last4 = recent[-4:]   # most recent 4
    prior4 = recent[:4]   # the 4 before

    for c in churches:
        hc_reports = by_hc.get(c.id, {})
        # Attendance figures for last 4 (ignoring did_not_meet rows and missing rows)
        last4_att = [hc_reports[d].attendance_count for d in last4 if d in hc_reports and not hc_reports[d].did_not_meet]
        prior4_att = [hc_reports[d].attendance_count for d in prior4 if d in hc_reports and not hc_reports[d].did_not_meet]
        last4_off = []
        for d in last4:
            if d in hc_reports and not hc_reports[d].did_not_meet:
                try:
                    last4_off.append(float(hc_reports[d].offering_amount or 0))
                except (TypeError, ValueError):
                    pass

        missing_last4 = sum(1 for d in last4 if d not in hc_reports)
        last4_avg = _safe_avg(last4_att)
        prior4_avg = _safe_avg(prior4_att)

        # Change % (only meaningful if we have a prior baseline)
        change_pct = None
        if prior4_avg > 0:
            change_pct = ((last4_avg - prior4_avg) / prior4_avg) * 100

        # Flag
        flag = "steady"
        reason = None
        if missing_last4 >= 2:
            flag = "attention"
            reason = f"{missing_last4} reports missing in last 4 weeks"
        elif change_pct is not None and change_pct >= 15:
            flag = "improving"
            reason = f"+{change_pct:.0f}% vs previous 4 weeks"
        elif change_pct is not None and change_pct <= -15:
            flag = "attention"
            reason = f"{change_pct:.0f}% vs previous 4 weeks"
        elif change_pct is not None:
            reason = f"{change_pct:+.0f}% vs previous 4 weeks"

        per_church.append({
            "home_church": _home_church_to_dict(c, db),
            "last4_avg_attendance": round(last4_avg, 1),
            "prior4_avg_attendance": round(prior4_avg, 1),
            "last4_total_offering": round(sum(last4_off), 2),
            "last4_avg_offering": round(_safe_avg(last4_off), 2),
            "missing_last4": missing_last4,
            "change_pct": round(change_pct, 1) if change_pct is not None else None,
            "flag": flag,
            "reason": reason,
            # Sparkline-friendly: last 8 weeks of attendance (None when no report)
            "trend_attendance": [
                (hc_reports[d].attendance_count if d in hc_reports and not hc_reports[d].did_not_meet else None)
                for d in recent
            ],
        })

    # Grand totals for the most recent week (anchor) and last 4 weeks combined
    latest_week = weekly_totals[-1] if weekly_totals else None
    last4_total_attendance = sum(w["attendance"] for w in weekly_totals[-4:])
    last4_total_offering = sum(w["offering"] for w in weekly_totals[-4:])
    last4_avg_attendance = round(last4_total_attendance / 4, 1)
    last4_avg_offering = round(last4_total_offering / 4, 2)

    return {
        "anchor_monday": anchor_monday.isoformat(),
        "range": {"from": first_day.isoformat(), "to": last_day.isoformat()},
        "weekly_totals": weekly_totals,
        "latest_week": latest_week,
        "month_summary": {
            "total_attendance": last4_total_attendance,
            "total_offering": round(last4_total_offering, 2),
            "avg_attendance_per_week": last4_avg_attendance,
            "avg_offering_per_week": last4_avg_offering,
            "reporting_rate": round(
                100 * sum(w["reporting"] for w in weekly_totals[-4:]) / max(1, 4 * len(churches)),
                1,
            ),
        },
        "per_church": per_church,
    }


@router.get("/admin/home-church/{home_church_id}/history")
def admin_home_church_history(
    home_church_id: int,
    request: Request,
    weeks: int = Query(12, ge=4, le=52),
    db: Session = Depends(get_db),
):
    """Per-home-church history view: N weeks of attendance + offering with
    headline stats (current vs prior month, group average comparison)."""
    _require_committee_or_admin(request, db)

    hc = db.query(HomeChurch).filter(HomeChurch.id == home_church_id).first()
    if not hc:
        raise HTTPException(status_code=404, detail="Home church not found")

    today = date.today()
    # Anchor on most recent Monday <= today
    anchor = today - timedelta(days=today.weekday())
    mondays = [anchor - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]  # oldest first
    first = mondays[0]
    last = mondays[-1]

    # This home church's reports
    own_reports = {
        r.roster_date: r
        for r in db.query(HomeChurchAttendance).filter(
            HomeChurchAttendance.home_church_id == home_church_id,
            HomeChurchAttendance.roster_date >= first,
            HomeChurchAttendance.roster_date <= last,
        ).all()
    }

    # Group reports (for average comparison)
    all_reports = db.query(HomeChurchAttendance).filter(
        HomeChurchAttendance.roster_date >= first,
        HomeChurchAttendance.roster_date <= last,
    ).all()
    group_by_date = {}
    for r in all_reports:
        if not r.did_not_meet and (r.attendance_count or 0) > 0:
            group_by_date.setdefault(r.roster_date, []).append(r)

    series = []
    for d in mondays:
        r = own_reports.get(d)
        row = {
            "date": d.isoformat(),
            "attendance": None,
            "offering": None,
            "did_not_meet": False,
            "group_avg_attendance": None,
        }
        if r:
            row["did_not_meet"] = bool(r.did_not_meet)
            if not r.did_not_meet:
                row["attendance"] = r.attendance_count or 0
                try:
                    row["offering"] = float(r.offering_amount or 0)
                except (TypeError, ValueError):
                    row["offering"] = 0.0
        # Group average for that week (across all reporting churches)
        grp = group_by_date.get(d, [])
        if grp:
            row["group_avg_attendance"] = round(sum(x.attendance_count or 0 for x in grp) / len(grp), 1)
        series.append(row)

    # Recent 4 weeks vs prior 4 weeks (same logic as the main stats)
    last4 = [s for s in series[-4:] if s["attendance"] is not None]
    prior4 = [s for s in series[-8:-4] if s["attendance"] is not None] if len(series) >= 8 else []

    def _avg(xs, field):
        vals = [x[field] for x in xs if x[field] is not None]
        return (sum(vals) / len(vals)) if vals else 0.0

    last4_avg = _avg(last4, "attendance")
    prior4_avg = _avg(prior4, "attendance")
    change_pct = None
    if prior4_avg > 0:
        change_pct = ((last4_avg - prior4_avg) / prior4_avg) * 100

    last4_total_offering = sum((s["offering"] or 0) for s in series[-4:])
    prior4_total_offering = sum((s["offering"] or 0) for s in series[-8:-4]) if len(series) >= 8 else 0

    missing_last4 = sum(1 for s in series[-4:] if s["attendance"] is None and not s["did_not_meet"])

    # Recent notes (last 4 weeks of captures with notes)
    notes = []
    for d in reversed(mondays[-4:]):
        r = own_reports.get(d)
        if r and r.notes:
            notes.append({"date": d.isoformat(), "notes": r.notes})

    return {
        "home_church": _home_church_to_dict(hc, db),
        "weeks": weeks,
        "series": series,
        "summary": {
            "last4_avg_attendance": round(last4_avg, 1),
            "prior4_avg_attendance": round(prior4_avg, 1),
            "change_pct": round(change_pct, 1) if change_pct is not None else None,
            "last4_total_offering": round(last4_total_offering, 2),
            "prior4_total_offering": round(prior4_total_offering, 2),
            "missing_last4": missing_last4,
        },
        "recent_notes": notes,
    }
