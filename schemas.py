from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


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
