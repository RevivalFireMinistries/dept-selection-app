from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, date


# Category schemas
class CategoryBase(BaseModel):
    name: str
    max_selections: int = 1  # How many departments can be selected from this category

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(BaseModel):
    id: int
    name: str
    max_selections: int = 1

class DepartmentInCategory(BaseModel):
    id: int
    name: str
    category_id: Optional[int] = None

    class Config:
        from_attributes = True

class CategoryResponse(CategoryBase):
    id: int
    created_at: datetime
    departments: List[DepartmentInCategory] = []

    class Config:
        from_attributes = True


# Department schemas
class DepartmentBase(BaseModel):
    name: str
    category_id: Optional[int] = None

class DepartmentCreate(DepartmentBase):
    pass

class DepartmentUpdate(BaseModel):
    id: int
    name: str
    category_id: Optional[int] = None

class CategoryInDepartment(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True

class HODInfo(BaseModel):
    id: int
    full_name: str
    phone: str

    class Config:
        from_attributes = True

class DepartmentResponse(DepartmentBase):
    id: int
    created_at: datetime
    category: Optional[CategoryInDepartment] = None
    hod: Optional[HODInfo] = None

    class Config:
        from_attributes = True


# Member schemas
class MemberSubmission(BaseModel):
    full_name: str
    phone: str
    email: str = ""
    address: str
    selected_departments: List[int]

class MemberDepartmentInfo(BaseModel):
    id: int
    department_id: int
    department: DepartmentResponse

    class Config:
        from_attributes = True

class MemberResponse(BaseModel):
    id: int
    full_name: str
    phone: str
    email: str
    address: str
    created_at: datetime
    departments: List[MemberDepartmentInfo] = []

    class Config:
        from_attributes = True


# Settings schemas
class SettingUpdate(BaseModel):
    key: str
    value: str

class SettingsResponse(BaseModel):
    maxDepartments: str = "3"
    adminPassword: str = "admin123"


# API response schemas
class DepartmentsGroupedResponse(BaseModel):
    categories: List[CategoryResponse]
    uncategorized: List[DepartmentInCategory]


# ============ APPROVAL WORKFLOW SCHEMAS ============

# Review/Approval schemas
class ReviewStatusUpdate(BaseModel):
    status: str  # "approved" or "rejected"
    admin_note: Optional[str] = None

class ReplaceDepartmentRequest(BaseModel):
    new_department_id: int
    admin_note: Optional[str] = None

class AssignDepartmentRequest(BaseModel):
    department_id: int
    admin_note: Optional[str] = None

class MemberDepartmentReviewResponse(BaseModel):
    id: int
    member_id: int
    department_id: int
    department_name: str
    category_name: Optional[str] = None
    source: str
    status: str
    admin_note: Optional[str] = None
    created_at: datetime
    status_changed_at: Optional[datetime] = None
    replaced_by_id: Optional[int] = None

class MemberReviewResponse(BaseModel):
    id: int
    full_name: str
    phone: str
    email: str
    address: str
    created_at: datetime
    selections: List[MemberDepartmentReviewResponse] = []


# Appeal schemas
class AppealCreate(BaseModel):
    phone: str
    member_id: Optional[int] = None  # Optional: specify member directly (for info desk with families)
    unwanted_department_id: Optional[int] = None
    wanted_department_id: Optional[int] = None
    reason: Optional[str] = None

class AppealResolve(BaseModel):
    status: str  # "approved" or "rejected"
    admin_response: Optional[str] = None

class AppealResponse(BaseModel):
    id: int
    member_id: int
    member_name: str
    member_phone: str
    unwanted_department_id: Optional[int] = None
    unwanted_department_name: Optional[str] = None
    wanted_department_id: Optional[int] = None
    wanted_department_name: Optional[str] = None
    reason: Optional[str] = None
    status: str
    admin_response: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


# Results schemas (for member-facing lookup)
class ApprovedDepartmentResponse(BaseModel):
    id: int
    name: str
    category_name: Optional[str] = None

class PublicResultsResponse(BaseModel):
    published: bool
    message: Optional[str] = None
    year: Optional[str] = None
    member_name: Optional[str] = None
    approved_departments: Optional[List[ApprovedDepartmentResponse]] = None
    appeal_window_open: Optional[bool] = None


# Publish preview schemas
class MemberPreview(BaseModel):
    id: int
    full_name: str
    phone: str
    approved_departments: List[str] = []

class PublishPreviewResponse(BaseModel):
    total_members: int
    total_approved_assignments: int
    pending_count: int
    members_preview: List[MemberPreview] = []


# ============ HOD SCHEMAS ============

class SetHODRequest(BaseModel):
    member_id: int

class HODDepartmentMember(BaseModel):
    id: int
    full_name: str
    phone: str
    email: str
    status: str
    source: str
    created_at: datetime

class HODDepartmentView(BaseModel):
    id: int
    name: str
    category_name: Optional[str] = None
    members: List[HODDepartmentMember] = []
    total_members: int = 0
    approved_count: int = 0
    pending_count: int = 0
    rejected_count: int = 0

class HODDashboardResponse(BaseModel):
    hod_name: str
    departments: List[HODDepartmentView] = []


# ============ MEETING SCHEMAS ============

class MeetingCreate(BaseModel):
    department_id: Optional[int] = None  # Nullable for general/multi-dept meetings
    title: str
    description: Optional[str] = None
    meeting_date: date
    start_slot: int  # 0-47 (30-min slots)
    end_slot: int    # Exclusive end slot, must be > start_slot
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    # Meeting type options
    is_general: bool = False  # True = meeting for all leaders
    target_department_ids: Optional[List[int]] = None  # List of department IDs for multi-dept meetings
    target_member_ids: Optional[List[int]] = None  # List of member IDs for individual invites
    target_leadership_roles: Optional[List[str]] = None  # ["hod", "deacon", "elder"]
    # Recurrence options
    recurrence: Optional[str] = None  # none, daily, weekly, biweekly, monthly
    recurrence_end_date: Optional[date] = None  # When to stop creating recurring meetings

class MeetingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    meeting_date: Optional[date] = None
    start_slot: Optional[int] = None
    end_slot: Optional[int] = None
    location: Optional[str] = None
    meeting_link: Optional[str] = None

class RSVPRequest(BaseModel):
    response: str  # "attending" or "not_attending"

class RSVPResponse(BaseModel):
    id: int
    member_id: int
    member_name: str
    response: str
    updated_at: datetime

class MeetingResponse(BaseModel):
    id: int
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    title: str
    description: Optional[str] = None
    meeting_date: date
    start_slot: int
    end_slot: int
    start_time: str  # Formatted "HH:MM"
    end_time: str    # Formatted "HH:MM"
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    created_by_id: Optional[int] = None
    created_by_name: Optional[str] = None
    created_at: datetime
    rsvp_count: int = 0
    attending_count: int = 0
    my_rsvp: Optional[str] = None  # For member view
    # Meeting type fields
    is_general: bool = False
    target_department_ids: Optional[List[int]] = None
    target_department_names: Optional[List[str]] = None  # For display

class CalendarSlot(BaseModel):
    slot: int
    time: str  # "HH:MM"
    available: bool
    meeting: Optional[MeetingResponse] = None
    conflict_reason: Optional[str] = None

class CalendarDay(BaseModel):
    date: date
    day_name: str
    slots: List[CalendarSlot] = []

class WeeklyCalendarResponse(BaseModel):
    week_start: date
    week_end: date
    department_id: int
    department_name: str
    days: List[CalendarDay] = []

class MeetingListResponse(BaseModel):
    meetings: List[MeetingResponse] = []
    total: int = 0


# ============ NOTIFICATION SCHEMAS ============

class SMTPSettingsUpdate(BaseModel):
    smtp_enabled: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[str] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_name: Optional[str] = None
    smtp_from_email: Optional[str] = None

class NotificationConfigUpdate(BaseModel):
    email_enabled: Optional[int] = None
    sms_enabled: Optional[int] = None
    push_enabled: Optional[int] = None

class NotificationConfigResponse(BaseModel):
    event_type: str
    label: str
    description: str
    email_enabled: bool = True
    sms_enabled: bool = False
    push_enabled: bool = False

class NotificationLogResponse(BaseModel):
    id: int
    event_type: str
    channel: str
    recipient_email: Optional[str] = None
    recipient_phone: Optional[str] = None
    subject: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    sent_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True

class TestEmailRequest(BaseModel):
    to_email: str


# ============ POSTER REQUEST SCHEMAS ============

class SpeakerInfo(BaseModel):
    name: str
    role: str  # "host" or "guest"

class PosterRequestCreate(BaseModel):
    event_name: str
    ministry_department: Optional[str] = None  # Now optional
    event_date: date
    event_time: str
    venue_platform: str
    speakers: Optional[List[SpeakerInfo]] = None  # Multiple speakers with roles
    theme_tagline: Optional[str] = None
    scripture: Optional[str] = None
    target_audience: Optional[str] = None
    purpose: str  # invitation, information, reminder, registration
    output_formats: Optional[List[str]] = None  # ["projector", "social_media", "print"]
    additional_notes: Optional[str] = None

# ============ SERVICE PROGRAM SCHEMAS ============

class ProgramItem(BaseModel):
    time: str  # "09:30"
    item: str  # "Prayer"

class Participant(BaseModel):
    role: str  # "Prayer"
    name: str  # "Dcns Gohodo"

class ProgramTemplateCreate(BaseModel):
    title: str
    day_of_week: int  # 0=Monday ... 6=Sunday
    program_items: List[ProgramItem]
    participants: List[Participant]
    admin_announcements: List[str] = []
    pastors_announcements: List[str] = []

class ProgramTemplateUpdate(BaseModel):
    title: Optional[str] = None
    day_of_week: Optional[int] = None
    program_items: Optional[List[ProgramItem]] = None
    participants: Optional[List[Participant]] = None
    admin_announcements: Optional[List[str]] = None
    pastors_announcements: Optional[List[str]] = None

class ServiceProgramCreate(BaseModel):
    title: str  # "SUNDAY SERVICE"
    service_date: date
    program_items: List[ProgramItem]
    participants: List[Participant]
    admin_announcements: List[str] = []
    pastors_announcements: List[str] = []
    template_id: Optional[int] = None  # Track which template was used

class ServiceProgramUpdate(BaseModel):
    title: Optional[str] = None
    service_date: Optional[date] = None
    program_items: Optional[List[ProgramItem]] = None
    participants: Optional[List[Participant]] = None
    admin_announcements: Optional[List[str]] = None
    pastors_announcements: Optional[List[str]] = None

class ServiceProgramResponse(BaseModel):
    id: int
    title: str
    service_date: date
    program_items: List[ProgramItem]
    participants: List[Participant]
    admin_announcements: List[str] = []
    pastors_announcements: List[str] = []
    created_at: datetime
    updated_at: Optional[datetime] = None


class PosterRequestResponse(BaseModel):
    id: int
    requester_id: int
    requester_name: str
    requester_email: Optional[str] = None
    event_name: str
    ministry_department: Optional[str] = None
    event_date: date
    event_time: str
    venue_platform: str
    speakers: Optional[List[SpeakerInfo]] = None
    theme_tagline: Optional[str] = None
    scripture: Optional[str] = None
    target_audience: Optional[str] = None
    purpose: str
    output_formats: Optional[List[str]] = None
    additional_notes: Optional[str] = None
    status: str
    acknowledged_by_id: Optional[int] = None
    acknowledged_by_name: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    created_at: datetime
