from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional, Dict, Any
from io import BytesIO
from datetime import datetime
import re

from database import get_db
from models import Category, Department, Member, MemberDepartment, Settings
from schemas import (
    CategoryCreate, CategoryUpdate, CategoryResponse,
    DepartmentCreate, DepartmentUpdate, DepartmentResponse, DepartmentInCategory,
    MemberSubmission, MemberResponse,
    SettingUpdate, DepartmentsGroupedResponse
)

router = APIRouter()


# ============ DEPARTMENTS ============

@router.get("/departments")
def get_departments(db: Session = Depends(get_db)):
    """Get all departments grouped by category"""
    categories = db.query(Category).options(
        joinedload(Category.departments)
    ).order_by(Category.name).all()

    uncategorized = db.query(Department).filter(
        Department.category_id == None
    ).order_by(Department.name).all()

    return {
        "categories": [
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
        ],
        "uncategorized": [
            {"id": d.id, "name": d.name, "categoryId": None}
            for d in uncategorized
        ]
    }


@router.post("/departments")
def create_department(data: DepartmentCreate, db: Session = Depends(get_db)):
    """Create a new department"""
    if not data.name:
        raise HTTPException(status_code=400, detail="Name is required")

    department = Department(name=data.name, category_id=data.category_id)
    db.add(department)
    db.commit()
    db.refresh(department)

    return {"id": department.id, "name": department.name, "categoryId": department.category_id}


@router.put("/departments")
def update_department(data: DepartmentUpdate, db: Session = Depends(get_db)):
    """Update an existing department"""
    if not data.id or not data.name:
        raise HTTPException(status_code=400, detail="ID and name are required")

    department = db.query(Department).filter(Department.id == data.id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    department.name = data.name
    department.category_id = data.category_id
    db.commit()

    return {"id": department.id, "name": department.name, "categoryId": department.category_id}


@router.delete("/departments")
def delete_department(id: int = Query(...), db: Session = Depends(get_db)):
    """Delete a department"""
    department = db.query(Department).filter(Department.id == id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    db.delete(department)
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
def create_category(data: CategoryCreate, db: Session = Depends(get_db)):
    """Create a new category"""
    if not data.name:
        raise HTTPException(status_code=400, detail="Name is required")

    category = Category(name=data.name, max_selections=data.max_selections)
    db.add(category)
    db.commit()
    db.refresh(category)

    return {"id": category.id, "name": category.name, "maxSelections": category.max_selections}


@router.put("/categories")
def update_category(data: CategoryUpdate, db: Session = Depends(get_db)):
    """Update an existing category"""
    if not data.id or not data.name:
        raise HTTPException(status_code=400, detail="ID and name are required")

    category = db.query(Category).filter(Category.id == data.id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    category.name = data.name
    category.max_selections = data.max_selections
    db.commit()

    return {"id": category.id, "name": category.name, "maxSelections": category.max_selections}


@router.delete("/categories")
def delete_category(id: int = Query(...), db: Session = Depends(get_db)):
    """Delete a category (departments become uncategorized)"""
    category = db.query(Category).filter(Category.id == id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    db.delete(category)
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
            "fullName": m.full_name,
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
def update_member(member_id: int, data: dict, db: Session = Depends(get_db)):
    """Update a member's information and department selections"""
    member = db.query(Member).filter(Member.id == member_id).first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Update basic info
    if "full_name" in data:
        member.full_name = data["full_name"]
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

        # Delete existing department associations
        db.query(MemberDepartment).filter(MemberDepartment.member_id == member_id).delete()

        # Create new associations
        for dept_id in selected:
            md = MemberDepartment(member_id=member_id, department_id=dept_id)
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
def update_setting(data: SettingUpdate, db: Session = Depends(get_db)):
    """Update or create a setting"""
    if not data.key or data.value is None:
        raise HTTPException(status_code=400, detail="Key and value are required")

    setting = db.query(Settings).filter(Settings.key == data.key).first()
    if setting:
        setting.value = str(data.value)
    else:
        setting = Settings(key=data.key, value=str(data.value))
        db.add(setting)

    db.commit()

    return {"success": True}


# ============ SUBMIT ============

@router.post("/submit")
def submit_form(data: MemberSubmission, db: Session = Depends(get_db)):
    """Submit member department selection form"""
    # Validate required fields
    if not data.full_name or not data.phone or not data.address:
        raise HTTPException(status_code=400, detail="Full name, phone, and address are required")

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
        full_name=data.full_name,
        phone=data.phone,
        email=data.email or "",
        address=data.address
    )
    db.add(member)
    db.flush()

    # Create member-department associations
    for dept_id in data.selected_departments:
        md = MemberDepartment(member_id=member.id, department_id=dept_id)
        db.add(md)

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


# ============ EXPORT ============

def sanitize_sheet_name(name: str) -> str:
    """Sanitize string for Excel sheet name"""
    # Remove invalid characters
    sanitized = re.sub(r'[\[\]:*?/\\]', '', name)
    # Limit to 31 characters
    return sanitized[:31]


@router.get("/export")
def export_data(type: str = Query("department"), db: Session = Depends(get_db)):
    """Export data to Excel file"""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    today = datetime.now().strftime("%Y-%m-%d")

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
            count = db.query(MemberDepartment).filter(MemberDepartment.department_id == dept.id).count()
            ws_summary.cell(row=row, column=1, value=dept.name)
            ws_summary.cell(row=row, column=2, value=count)

        filename = f"members-report-{today}.xlsx"

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
            ws.cell(row=row, column=1, value=dept.name)
            ws.cell(row=row, column=2, value=dept.category.name if dept.category else "Uncategorized")
            ws.cell(row=row, column=3, value=len(dept.member_departments))

        # One sheet per department
        for dept in departments:
            sheet_name = sanitize_sheet_name(dept.name)
            ws_dept = wb.create_sheet(sheet_name)

            # Header info
            ws_dept.cell(row=1, column=1, value="Department:")
            ws_dept.cell(row=1, column=2, value=dept.name)
            ws_dept.cell(row=2, column=1, value="Category:")
            ws_dept.cell(row=2, column=2, value=dept.category.name if dept.category else "Uncategorized")
            ws_dept.cell(row=3, column=1, value="Total Members:")
            ws_dept.cell(row=3, column=2, value=len(dept.member_departments))

            # Column headers
            ws_dept.cell(row=5, column=1, value="Full Name")
            ws_dept.cell(row=5, column=2, value="Phone")
            ws_dept.cell(row=5, column=3, value="Email")
            ws_dept.cell(row=5, column=4, value="Address")
            ws_dept.cell(row=5, column=5, value="Submitted On")

            # Members
            for row, md in enumerate(dept.member_departments, 6):
                ws_dept.cell(row=row, column=1, value=md.member.full_name)
                ws_dept.cell(row=row, column=2, value=md.member.phone)
                ws_dept.cell(row=row, column=3, value=md.member.email)
                ws_dept.cell(row=row, column=4, value=md.member.address)
                ws_dept.cell(row=row, column=5, value=md.member.created_at.strftime("%Y-%m-%d %H:%M") if md.member.created_at else "")

            # Set column widths
            for col in range(1, 6):
                ws_dept.column_dimensions[get_column_letter(col)].width = 20

        filename = f"departments-report-{today}.xlsx"

    # Save to BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
