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
    department_id: int
    title: str
    description: Optional[str] = None
    meeting_date: date
    start_slot: int  # 0-47 (30-min slots)
    end_slot: int    # Exclusive end slot, must be > start_slot
    location: Optional[str] = None
    meeting_link: Optional[str] = None

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
    department_id: int
    department_name: str
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
