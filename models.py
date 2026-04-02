from sqlalchemy import Column, Integer, String, DateTime, Date, Text, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    max_selections = Column(Integer, nullable=False, default=1)  # Max departments selectable from this category
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    departments = relationship("Department", back_populates="category")


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    hod_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    category = relationship("Category", back_populates="departments")
    hod = relationship("Member", foreign_keys=[hod_member_id])
    member_departments = relationship("MemberDepartment", back_populates="department", cascade="all, delete-orphan")
    meetings = relationship("Meeting", back_populates="department", cascade="all, delete-orphan")


class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False, default="")
    address = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Leadership roles: JSON array e.g. ["deacon", "elder"] - "hod" is derived from departments
    leadership_roles = Column(Text, nullable=True)

    departments = relationship("MemberDepartment", back_populates="member", cascade="all, delete-orphan")
    appeals = relationship("Appeal", back_populates="member", cascade="all, delete-orphan")


class MemberDepartment(Base):
    __tablename__ = "member_departments"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Approval workflow fields (nullable with server defaults for migration compatibility)
    source = Column(String, nullable=True, server_default="member")  # "member" or "admin"
    status = Column(String, nullable=True, server_default="pending")  # "pending", "approved", "rejected"
    replaced_by_id = Column(Integer, ForeignKey("member_departments.id"), nullable=True)
    admin_note = Column(String, nullable=True)
    status_changed_at = Column(DateTime(timezone=True), nullable=True)

    member = relationship("Member", back_populates="departments")
    department = relationship("Department", back_populates="member_departments")
    replaced_by = relationship("MemberDepartment", remote_side=[id], foreign_keys=[replaced_by_id])

    __table_args__ = (
        UniqueConstraint("member_id", "department_id", name="unique_member_department"),
    )


class Appeal(Base):
    __tablename__ = "appeals"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)

    # What they're appealing against (department they don't want)
    unwanted_department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=True)

    # What they want instead
    wanted_department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=True)

    # Member's reason for appeal
    reason = Column(String, nullable=True)

    # Status: "pending", "approved", "rejected"
    status = Column(String, nullable=False, default="pending")

    # Admin response
    admin_response = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    member = relationship("Member", back_populates="appeals")
    unwanted_department = relationship("Department", foreign_keys=[unwanted_department_id])
    wanted_department = relationship("Department", foreign_keys=[wanted_department_id])


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, nullable=False)


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=True)  # Nullable for general meetings
    created_by_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    meeting_date = Column(Date, nullable=False)
    start_slot = Column(Integer, nullable=False)  # 0-47 (30-min slots, 0=00:00, 1=00:30, etc.)
    end_slot = Column(Integer, nullable=False)    # Exclusive end slot
    location = Column(String(200), nullable=True)
    meeting_link = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Meeting type fields
    is_general = Column(Integer, nullable=True, server_default="0")  # 1 = all leaders meeting
    target_department_ids = Column(Text, nullable=True)  # JSON array of department IDs for multi-dept meetings
    target_member_ids = Column(Text, nullable=True)  # JSON array of member IDs for individual invites
    target_leadership_roles = Column(Text, nullable=True)  # JSON array of roles e.g. ["hod", "deacon", "elder"]

    # Recurrence fields
    recurrence_group_id = Column(String(36), nullable=True, index=True)  # UUID linking recurring meetings

    department = relationship("Department", back_populates="meetings")
    created_by = relationship("Member", foreign_keys=[created_by_id])
    rsvps = relationship("MeetingRSVP", back_populates="meeting", cascade="all, delete-orphan")


class MeetingRSVP(Base):
    __tablename__ = "meeting_rsvps"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    response = Column(String(20), server_default="pending")  # pending, attending, not_attending
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('meeting_id', 'member_id', name='uq_meeting_member'),
    )

    meeting = relationship("Meeting", back_populates="rsvps")
    member = relationship("Member")


class NotificationConfig(Base):
    """Configuration for each notification event type"""
    __tablename__ = "notification_configs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(50), unique=True, nullable=False)  # e.g., "meeting_created"
    email_enabled = Column(Integer, nullable=True, server_default="1")  # 1=enabled, 0=disabled
    email_template = Column(Text, nullable=True)  # Custom template override
    sms_enabled = Column(Integer, nullable=True, server_default="0")  # Future
    push_enabled = Column(Integer, nullable=True, server_default="0")  # Future
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NotificationLog(Base):
    """Audit log of sent notifications"""
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False)  # "email", "sms", "push"
    recipient_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    recipient_email = Column(String(200), nullable=True)
    recipient_phone = Column(String(50), nullable=True)
    subject = Column(String(200), nullable=True)
    body = Column(Text, nullable=True)
    status = Column(String(20), nullable=True, server_default="pending")  # pending, sent, failed
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recipient = relationship("Member", foreign_keys=[recipient_id])


class PosterRequest(Base):
    """Poster/design request submitted by members"""
    __tablename__ = "poster_requests"

    id = Column(Integer, primary_key=True, index=True)
    requester_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)

    # Form fields
    event_name = Column(String(200), nullable=False)
    ministry_department = Column(String(200), nullable=True)  # Optional
    event_date = Column(Date, nullable=False)
    event_time = Column(String(100), nullable=False)
    venue_platform = Column(String(200), nullable=False)
    # Speakers stored as JSON array: [{"name": "John", "role": "host"}, {"name": "Mary", "role": "guest"}]
    speakers = Column(Text, nullable=True)
    theme_tagline = Column(String(300), nullable=True)
    scripture = Column(String(500), nullable=True)
    target_audience = Column(String(200), nullable=True)
    purpose = Column(String(50), nullable=False)  # invitation, information, reminder, registration
    # Output formats stored as JSON array: ["projector", "social_media", "print"]
    output_formats = Column(Text, nullable=True)
    additional_notes = Column(Text, nullable=True)

    # Workflow
    status = Column(String(20), nullable=False, server_default="pending")  # pending, acknowledged, completed
    acknowledged_by_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    requester = relationship("Member", foreign_keys=[requester_id])
    acknowledged_by = relationship("Member", foreign_keys=[acknowledged_by_id])


class ProgramTemplate(Base):
    """Reusable program template tied to a day of week"""
    __tablename__ = "program_templates"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)  # e.g. "SUNDAY SERVICE"
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 1=Tuesday, ... 6=Sunday (Python weekday)
    location_type = Column(String(20), nullable=False, server_default="onsite")  # "onsite" or "online"
    # JSON array: [{"time": "09:30", "item": "Prayer"}, ...]
    program_items = Column(Text, nullable=False)
    # JSON array: [{"role": "Prayer", "name": "Dcns Gohodo"}, ...]
    participants = Column(Text, nullable=False)
    # JSON array of announcement strings
    admin_announcements = Column(Text, nullable=False, server_default="[]")
    pastors_announcements = Column(Text, nullable=False, server_default="[]")
    prayer_points = Column(Text, nullable=False, server_default="[]")
    # JSON array of support role names: ["Projector", "Livestreaming"]
    support_roles = Column(Text, nullable=False, server_default="[]")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ServiceProgram(Base):
    """Church service program/order of service"""
    __tablename__ = "service_programs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)  # e.g. "SUNDAY SERVICE"
    service_date = Column(Date, nullable=False, index=True)
    location_type = Column(String(20), nullable=False, server_default="onsite")  # "onsite" or "online"
    # JSON array: [{"time": "09:30", "item": "Prayer"}, ...]
    program_items = Column(Text, nullable=False)
    # JSON array: [{"role": "Prayer", "name": "Dcns Gohodo"}, ...]
    participants = Column(Text, nullable=False)
    # JSON array of announcement strings: ["Announcement 1", "Announcement 2"]
    admin_announcements = Column(Text, nullable=False, server_default="[]")
    pastors_announcements = Column(Text, nullable=False, server_default="[]")
    prayer_points = Column(Text, nullable=False, server_default="[]")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DisplaySubmission(Base):
    """
    Media/content submissions for FirePresenter display.
    Any team leader can submit content to be displayed during services.
    """
    __tablename__ = "display_submissions"

    id = Column(Integer, primary_key=True, index=True)

    # Who submitted
    submitter_name = Column(String, nullable=False)
    submitter_dept = Column(String, nullable=True)
    submitter_phone = Column(String, nullable=True)

    # What to display
    content_type = Column(String, nullable=False)            # sermon_info, scripture, nugget, image, video, powerpoint, announcement
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)                       # Main text content
    subtitle = Column(String, nullable=True)
    comments = Column(Text, nullable=True)                   # Instructions for the operator

    # File attachment
    file_path = Column(String, nullable=True)
    file_name = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)

    # When to display
    service_date = Column(Date, nullable=False)
    display_slot = Column(String, nullable=False, server_default="announcements")
    display_duration = Column(Integer, nullable=True)
    display_order = Column(Integer, nullable=True)

    # Workflow
    status = Column(String, nullable=False, server_default="pending")
    reviewed_by = Column(String, nullable=True)
    review_note = Column(String, nullable=True)
    fetched = Column(Boolean, nullable=False, server_default="false")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
