from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date, time
from decimal import Decimal


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

class DepartmentResponse(DepartmentBase):
    id: int
    created_at: datetime
    category: Optional[CategoryInDepartment] = None

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


# ============ ATTENDANCE TRACKING SCHEMAS ============

# Service schemas
class ServiceBase(BaseModel):
    name: str
    day_of_week: int  # 0=Monday, 6=Sunday
    start_time: time

class ServiceCreate(ServiceBase):
    pass

class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    day_of_week: Optional[int] = None
    start_time: Optional[time] = None
    is_active: Optional[bool] = None

class ServiceResponse(ServiceBase):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# Service Instance schemas
class ServiceInstanceCreate(BaseModel):
    service_id: int
    date: date
    notes: Optional[str] = None

class ServiceInstanceResponse(BaseModel):
    id: int
    service_id: int
    date: date
    notes: Optional[str] = None
    is_cancelled: bool
    created_at: datetime
    service: Optional[ServiceResponse] = None
    attendance_count: Optional[int] = None

    class Config:
        from_attributes = True


# Visitor schemas
class VisitorBase(BaseModel):
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None

class VisitorCreate(VisitorBase):
    first_visit_date: date

class VisitorUpdate(VisitorBase):
    pass

class VisitorResponse(VisitorBase):
    id: int
    first_visit_date: date
    converted_to_member_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# Attendance schemas
class AttendanceCreate(BaseModel):
    member_id: Optional[int] = None
    visitor_id: Optional[int] = None
    check_in_method: str = "admin"  # "admin", "self", "qr"

class AttendanceResponse(BaseModel):
    id: int
    service_instance_id: int
    member_id: Optional[int] = None
    visitor_id: Optional[int] = None
    check_in_method: str
    check_in_time: datetime
    member_name: Optional[str] = None
    visitor_name: Optional[str] = None

    class Config:
        from_attributes = True


# Check-in schemas
class PhoneCheckInRequest(BaseModel):
    phone: str
    service_instance_id: int

class QRCheckInRequest(BaseModel):
    qr_code: str
    service_instance_id: int

class CheckInResponse(BaseModel):
    success: bool
    message: str
    member_name: Optional[str] = None
    service_name: Optional[str] = None


# QR Code schemas
class MemberQRCodeResponse(BaseModel):
    id: int
    member_id: int
    code: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ============ MEMBER DIRECTORY SCHEMAS ============

class MemberProfileBase(BaseModel):
    full_name: str
    phone: str
    email: Optional[str] = ""
    address: Optional[str] = ""
    photo_url: Optional[str] = None
    birthday: Optional[date] = None
    anniversary: Optional[date] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    occupation: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    member_since: Optional[date] = None

class MemberProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    photo_url: Optional[str] = None
    birthday: Optional[date] = None
    anniversary: Optional[date] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    occupation: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    member_since: Optional[date] = None
    is_active: Optional[bool] = None

class MemberProfileResponse(MemberProfileBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DirectoryMemberResponse(BaseModel):
    id: int
    full_name: str
    phone: str
    email: Optional[str] = None
    photo_url: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True

class DirectoryResponse(BaseModel):
    members: List[DirectoryMemberResponse]
    total: int
    page: int
    page_size: int

class BirthdayReportEntry(BaseModel):
    id: int
    full_name: str
    phone: str
    birthday: date
    day_of_month: int

    class Config:
        from_attributes = True


# ============ CELL GROUP SCHEMAS ============

# Cell Group schemas
class CellGroupBase(BaseModel):
    name: str
    description: Optional[str] = None
    meeting_day: Optional[int] = None  # 0=Monday, 6=Sunday
    meeting_time: Optional[time] = None
    meeting_location: Optional[str] = None

class CellGroupCreate(CellGroupBase):
    leader_id: Optional[int] = None
    assistant_leader_id: Optional[int] = None

class CellGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    meeting_day: Optional[int] = None
    meeting_time: Optional[time] = None
    meeting_location: Optional[str] = None
    leader_id: Optional[int] = None
    assistant_leader_id: Optional[int] = None
    is_active: Optional[bool] = None

class CellGroupMemberBrief(BaseModel):
    id: int
    full_name: str
    phone: str

    class Config:
        from_attributes = True

class CellGroupResponse(CellGroupBase):
    id: int
    is_active: bool
    created_at: datetime
    leader: Optional[CellGroupMemberBrief] = None
    assistant_leader: Optional[CellGroupMemberBrief] = None
    member_count: Optional[int] = None

    class Config:
        from_attributes = True


# Cell Membership schemas
class CellMembershipCreate(BaseModel):
    member_id: int
    role: str = "member"  # "leader", "assistant", "member", "host"

class CellMembershipUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None

class CellMembershipResponse(BaseModel):
    id: int
    cell_group_id: int
    member_id: int
    member_name: str
    member_phone: str
    role: str
    joined_at: datetime
    left_at: Optional[datetime] = None
    is_active: bool

    class Config:
        from_attributes = True


# Cell Meeting schemas
class CellMeetingCreate(BaseModel):
    date: date
    topic: Optional[str] = None
    notes: Optional[str] = None
    offering_amount: Optional[Decimal] = None

class CellMeetingUpdate(BaseModel):
    topic: Optional[str] = None
    notes: Optional[str] = None
    offering_amount: Optional[Decimal] = None

class CellMeetingResponse(BaseModel):
    id: int
    cell_group_id: int
    date: date
    topic: Optional[str] = None
    notes: Optional[str] = None
    offering_amount: Optional[Decimal] = None
    created_at: datetime
    attendance_count: Optional[int] = None

    class Config:
        from_attributes = True


# Cell Meeting Attendance schemas
class CellMeetingAttendanceCreate(BaseModel):
    member_id: Optional[int] = None
    visitor_name: Optional[str] = None
    visitor_phone: Optional[str] = None

class CellMeetingAttendanceResponse(BaseModel):
    id: int
    meeting_id: int
    member_id: Optional[int] = None
    member_name: Optional[str] = None
    visitor_name: Optional[str] = None
    visitor_phone: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# Cell Leader portal schemas
class CellLeaderGroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    meeting_day: Optional[int] = None
    meeting_time: Optional[time] = None
    meeting_location: Optional[str] = None
    member_count: int
    recent_meetings: List[CellMeetingResponse] = []

    class Config:
        from_attributes = True
