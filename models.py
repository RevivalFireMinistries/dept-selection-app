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
    # Link to the central rfm-database department record. Same pattern we use
    # for members — gated by RFM_API_INTEGRATION_ENABLED, populated on first
    # sync, kept in lockstep on every CRUD edit.
    external_department_id = Column(String(36), nullable=True, index=True)
    external_synced_at = Column(DateTime(timezone=True), nullable=True)

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
    # Authentication
    password_hash = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    reset_token = Column(String, nullable=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)
    # Link to the central rfm-database (the source of truth for member info).
    # external_member_id holds the API's UUID once matched; null means we
    # haven't matched this local member to the API yet (legacy or orphan).
    external_member_id = Column(String(36), nullable=True, index=True)
    # 'matched' / 'ambiguous' / 'unmatched' / 'manual' / null
    external_match_status = Column(String(20), nullable=True)
    # When we last pulled fresh data from the API for this member
    external_synced_at = Column(DateTime(timezone=True), nullable=True)
    # Assembly UUID this member belongs to (drives multi-tenant scoping when
    # the central API has more than one church).
    external_assembly_id = Column(String(36), nullable=True, index=True)
    # Surveys: members granted this flag by an admin can build and share surveys.
    can_create_surveys = Column(Boolean, nullable=False, server_default="false")

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
    status = Column(String(20), nullable=False, server_default="draft")  # "draft", "published"
    # Source template (if this program was created from one). Lets the editor
    # surface support_roles defined on the template even after the program has
    # been saved & re-opened.
    template_id = Column(Integer, ForeignKey("program_templates.id", ondelete="SET NULL"), nullable=True)
    created_by_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    created_by = relationship("Member", foreign_keys=[created_by_member_id])
    template = relationship("ProgramTemplate", foreign_keys=[template_id])


class ServiceSchedule(Base):
    """Scheduled future service — assigns a date, template, and service manager"""
    __tablename__ = "service_schedules"

    id = Column(Integer, primary_key=True, index=True)
    service_date = Column(Date, nullable=False, index=True)
    template_id = Column(Integer, ForeignKey("program_templates.id", ondelete="SET NULL"), nullable=True)
    service_manager_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    notes = Column(Text, nullable=True)
    # Tracking notification state
    notified_at = Column(DateTime(timezone=True), nullable=True)  # Week-start notification sent
    reminded_at = Column(DateTime(timezone=True), nullable=True)  # Reminder sent (no program yet)
    program_id = Column(Integer, ForeignKey("service_programs.id", ondelete="SET NULL"), nullable=True)  # Linked program once created
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    template = relationship("ProgramTemplate", foreign_keys=[template_id])
    service_manager = relationship("Member", foreign_keys=[service_manager_id])
    program = relationship("ServiceProgram", foreign_keys=[program_id])


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


class SyncedSong(Base):
    """
    Song catalog pushed from FirePresenter.
    The music team picks from these when building a service song list.
    """
    __tablename__ = "synced_songs"

    id = Column(Integer, primary_key=True)  # matches FirePresenter song.id
    title = Column(String, nullable=False)
    author = Column(String, nullable=True)
    category = Column(String, nullable=True)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())


class SongListSubmission(Base):
    """
    Service song list submitted by the music/worship team.
    Each row is one song pick linked to a synced_songs entry.
    """
    __tablename__ = "song_list_submissions"

    id = Column(Integer, primary_key=True, index=True)
    submitter_name = Column(String, nullable=False)
    service_date = Column(Date, nullable=False, index=True)
    song_id = Column(Integer, nullable=False)   # FK to synced_songs.id
    song_title = Column(String, nullable=False)  # denormalized for display
    notes = Column(String, nullable=True)        # "key of G", "acoustic", etc.
    song_order = Column(Integer, nullable=False, server_default="0")
    fetched = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AdminAuditLog(Base):
    """Tracks all system activity — admin actions, member actions, and system events"""
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    admin_name = Column(String, nullable=False)  # actor name (admin or member), denormalized
    actor_type = Column(String(20), nullable=False, server_default="admin")  # "admin", "member", "system"
    action = Column(String(100), nullable=False)  # e.g. "approve_member", "submit_selection", "create_poster_request"
    entity_type = Column(String(50), nullable=True)  # e.g. "member", "department", "appeal", "poster_request"
    entity_id = Column(Integer, nullable=True)  # ID of the affected entity
    details = Column(Text, nullable=True)  # JSON or human-readable description
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NewSongSubmission(Base):
    """
    Brand-new songs submitted by the music team with full lyrics.
    FirePresenter imports these into its local database.
    """
    __tablename__ = "new_song_submissions"

    id = Column(Integer, primary_key=True, index=True)
    submitter_name = Column(String, nullable=False)
    title = Column(String, nullable=False)
    author = Column(String, nullable=True)
    category = Column(String, nullable=True, server_default="General")
    sections_json = Column(Text, nullable=False)  # JSON: [{type, label, lyrics, orderIndex}]
    fetched = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============ HOME CHURCH ROSTER ============

class HomeChurch(Base):
    """A home church venue/group that meets weekly (typically Mondays)."""
    __tablename__ = "home_churches"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)  # e.g. "Eersterivier West"
    leader_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    address = Column(String(500), nullable=True)
    suburb = Column(String(100), nullable=True)
    meeting_day = Column(Integer, nullable=False, server_default="0")  # 0=Mon..6=Sun
    meeting_time = Column(String(5), nullable=False, server_default="19:00")  # HH:MM
    whatsapp_link = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    leader = relationship("Member", foreign_keys=[leader_member_id])
    roster_entries = relationship("HomeChurchRoster", back_populates="home_church", cascade="all, delete-orphan")


class HomeChurchProgramType(Base):
    """Types of program a home church can have on a given week.
    requires_preacher controls whether the roster entry needs a preacher assigned."""
    __tablename__ = "home_church_program_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)  # e.g. "Preaching Service"
    requires_preacher = Column(Boolean, nullable=False, server_default="false")
    color = Column(String(20), nullable=True)  # Tailwind color name e.g. "blue", "purple"
    icon = Column(String(10), nullable=True)  # Single emoji
    sort_order = Column(Integer, nullable=False, server_default="0")
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class HomeChurchRoster(Base):
    """One roster entry per (home_church, date). Preacher optional based on program type."""
    __tablename__ = "home_church_roster"

    id = Column(Integer, primary_key=True, index=True)
    home_church_id = Column(Integer, ForeignKey("home_churches.id", ondelete="CASCADE"), nullable=False)
    roster_date = Column(Date, nullable=False)  # The Monday (or meeting_day) date
    program_type_id = Column(Integer, ForeignKey("home_church_program_types.id", ondelete="SET NULL"), nullable=True)
    preacher_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, server_default="draft")  # draft | published
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("home_church_id", "roster_date", name="uq_home_church_roster_date"),
    )

    home_church = relationship("HomeChurch", back_populates="roster_entries")
    program_type = relationship("HomeChurchProgramType")
    preacher = relationship("Member", foreign_keys=[preacher_member_id])


class HomeChurchAttendance(Base):
    """One report per home church per meeting date (Monday). Captures attendance
    counts and offering. Leaders submit from the portal after the meeting; the
    committee can view, override, and receive reminders for missing reports."""
    __tablename__ = "home_church_attendance"

    id = Column(Integer, primary_key=True, index=True)
    home_church_id = Column(Integer, ForeignKey("home_churches.id", ondelete="CASCADE"), nullable=False)
    roster_date = Column(Date, nullable=False)
    # Main fields
    did_not_meet = Column(Boolean, nullable=False, server_default="false")  # true = home church was cancelled that week
    attendance_count = Column(Integer, nullable=False, server_default="0")
    adults_count = Column(Integer, nullable=True)
    children_count = Column(Integer, nullable=True)
    new_visitors_count = Column(Integer, nullable=False, server_default="0")
    offering_amount = Column(String(20), nullable=False, server_default="0")  # store as string to avoid float issues; convert client-side
    notes = Column(Text, nullable=True)
    # Audit
    submitted_by_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # Reminder tracking so we don't spam
    reminder_sent_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("home_church_id", "roster_date", name="uq_attendance_per_meeting"),
    )

    home_church = relationship("HomeChurch")
    submitted_by = relationship("Member", foreign_keys=[submitted_by_member_id])


# ============ SURVEYS ============

class Survey(Base):
    """A survey built by an authorised creator. Shared via a public slug URL.
    When is_anonymous is true, responses MUST never carry respondent identity —
    the API enforces this server-side regardless of any client cookie state."""
    __tablename__ = "surveys"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    slug = Column(String(40), unique=True, nullable=False, index=True)  # public token
    is_anonymous = Column(Boolean, nullable=False, server_default="true")
    is_active = Column(Boolean, nullable=False, server_default="true")
    # When true, the public response page offers a "Submit another response"
    # button on the thank-you screen so the same device can submit repeatedly.
    allow_multiple_responses = Column(Boolean, nullable=False, server_default="false")
    created_by_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)

    questions = relationship(
        "SurveyQuestion",
        back_populates="survey",
        cascade="all, delete-orphan",
        order_by="SurveyQuestion.position",
    )
    responses = relationship(
        "SurveyResponse",
        back_populates="survey",
        cascade="all, delete-orphan",
    )
    created_by = relationship("Member", foreign_keys=[created_by_member_id])


class SurveyQuestion(Base):
    __tablename__ = "survey_questions"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False, server_default="0")
    question_text = Column(Text, nullable=False)
    # text | long_text | single_choice | multi_choice | rating | yes_no
    question_type = Column(String(20), nullable=False)
    options = Column(Text, nullable=True)  # JSON list of strings (for choice types)
    required = Column(Boolean, nullable=False, server_default="false")

    survey = relationship("Survey", back_populates="questions")


class SurveyResponse(Base):
    __tablename__ = "survey_responses"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False)
    # ALWAYS null for anonymous surveys. The API never sets these for an
    # is_anonymous survey, even if a logged-in cookie is present.
    respondent_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    respondent_name = Column(String(200), nullable=True)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())

    survey = relationship("Survey", back_populates="responses")
    answers = relationship(
        "SurveyAnswer",
        back_populates="response",
        cascade="all, delete-orphan",
    )


class SurveyAnswer(Base):
    __tablename__ = "survey_answers"

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(Integer, ForeignKey("survey_responses.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(Integer, ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False)
    answer_text = Column(Text, nullable=True)       # text/long_text/single_choice/rating/yes_no
    answer_options = Column(Text, nullable=True)    # JSON array for multi_choice

    response = relationship("SurveyResponse", back_populates="answers")
    question = relationship("SurveyQuestion")
