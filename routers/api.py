from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from typing import Optional, Dict, Any
from io import BytesIO
from datetime import datetime
import re

from database import get_db
from models import Category, Department, Member, MemberDepartment, Settings, Appeal
from schemas import (
    CategoryCreate, CategoryUpdate, CategoryResponse,
    DepartmentCreate, DepartmentUpdate, DepartmentResponse, DepartmentInCategory,
    MemberSubmission, MemberResponse,
    SettingUpdate, DepartmentsGroupedResponse,
    ReviewStatusUpdate, ReplaceDepartmentRequest, AssignDepartmentRequest,
    AppealCreate, AppealResolve
)

router = APIRouter()


def validate_phone(phone: str) -> bool:
    """Validate phone number is exactly 10 digits"""
    digits = re.sub(r'\D', '', phone)
    return len(digits) == 10


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
    db: Session = Depends(get_db)
):
    """Approve or reject a single department selection"""
    md = db.query(MemberDepartment).filter(MemberDepartment.id == member_department_id).first()
    if not md:
        raise HTTPException(status_code=404, detail="Selection not found")

    if data.status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    md.status = data.status
    md.admin_note = data.admin_note
    md.status_changed_at = datetime.now()
    db.commit()

    return {"success": True, "id": member_department_id, "status": data.status}


@router.post("/admin/reviews/{member_department_id}/replace")
def replace_department(
    member_department_id: int,
    data: ReplaceDepartmentRequest,
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

    return {"success": True, "original_id": member_department_id, "new_id": new_md.id}


@router.post("/admin/members/{member_id}/assign")
def assign_department(
    member_id: int,
    data: AssignDepartmentRequest,
    db: Session = Depends(get_db)
):
    """Admin assigns an additional department to a member"""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    dept = db.query(Department).filter(Department.id == data.department_id).first()
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
    db.commit()
    db.refresh(md)

    return {"success": True, "id": md.id}


@router.post("/admin/reviews/bulk-approve")
def bulk_approve_pending(db: Session = Depends(get_db)):
    """Approve all pending selections (including null status from before workflow)"""
    count = db.query(MemberDepartment).filter(
        or_(MemberDepartment.status == "pending", MemberDepartment.status.is_(None))
    ).update({
        MemberDepartment.status: "approved",
        MemberDepartment.status_changed_at: datetime.now()
    }, synchronize_session='fetch')
    db.commit()

    return {"success": True, "approved_count": count}


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
def publish_results(db: Session = Depends(get_db)):
    """Publish results - make approved selections visible to members"""
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

    return {"success": True, "published_at": now}


@router.post("/admin/unpublish")
def unpublish_results(db: Session = Depends(get_db)):
    """Unpublish results - hide from members"""
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

        members_data.append({
            "id": member.id,
            "full_name": member.full_name,
            "email": member.email,
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

    return {"success": True, "message": "Department assignment accepted"}


# ============ APPEAL ENDPOINTS ============

@router.post("/appeals")
def submit_appeal(data: AppealCreate, db: Session = Depends(get_db)):
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

    # Create appeal
    appeal = Appeal(
        member_id=member.id,
        unwanted_department_id=data.unwanted_department_id,
        wanted_department_id=data.wanted_department_id,
        reason=data.reason,
        status="pending"
    )
    db.add(appeal)
    db.commit()
    db.refresh(appeal)

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
    db: Session = Depends(get_db)
):
    """Resolve an appeal (approve or reject)"""
    appeal = db.query(Appeal).filter(Appeal.id == appeal_id).first()
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")

    if data.status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    appeal.status = data.status
    appeal.admin_response = data.admin_response
    appeal.resolved_at = datetime.now()

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

    return {"success": True, "appeal_id": appeal_id, "status": data.status}


@router.post("/admin/appeals/window")
def toggle_appeal_window(open: bool = Query(...), db: Session = Depends(get_db)):
    """Open or close the appeal window"""
    setting = db.query(Settings).filter(Settings.key == "appealWindowOpen").first()
    if setting:
        setting.value = "true" if open else "false"
    else:
        db.add(Settings(key="appealWindowOpen", value="true" if open else "false"))

    db.commit()

    return {"success": True, "appeal_window_open": open}
