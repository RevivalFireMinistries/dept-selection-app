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
