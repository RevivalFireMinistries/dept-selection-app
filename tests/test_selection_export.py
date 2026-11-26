"""Campaign Excel export.

GET /api/admin/selection/export downloads an .xlsx workbook with a Summary
sheet plus one worksheet per department listing everyone who selected it.
"""
import io
import json

import pytest

from openpyxl import load_workbook

from models import Department, DepartmentSelection


@pytest.fixture
def admin_login(db, client, make_member, settings_value):
    admin = make_member(full_name="Admin Person", password="pw")
    admin.leadership_roles = json.dumps(["admin"])
    db.commit()
    settings_value("adminPassword", "admin123")
    r = client.post("/admin/login",
                    data={"phone": admin.phone, "password": "admin123"},
                    follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    yield admin
    client.cookies.clear()


@pytest.fixture
def two_departments(db):
    a = Department(name="Choir Export")
    b = Department(name="Ushering Export")
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    yield a, b
    for dept in (a, b):
        obj = db.query(Department).filter(Department.id == dept.id).first()
        if obj:
            db.delete(obj)
    db.commit()


def test_export_has_a_sheet_per_department_with_selectors(db, client, two_departments,
                                                          make_member, admin_login, settings_value):
    settings_value("selectionCampaignYear", "2031")
    settings_value("selectionCampaignOpen", "true")
    choir, ushering = two_departments
    alice = make_member(full_name="Alice Export", phone="0820000001")
    bob = make_member(full_name="Bob Export", phone="0820000002")
    db.add_all([
        DepartmentSelection(year=2031, member_id=alice.id, department_id=choir.id,
                            status="pending", source="member"),
        DepartmentSelection(year=2031, member_id=bob.id, department_id=choir.id,
                            status="approved", source="admin"),
    ])
    db.commit()

    r = client.get("/api/admin/selection/export")
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    assert "campaign-2031-selections" in r.headers.get("content-disposition", "")

    wb = load_workbook(io.BytesIO(r.content))
    assert "Summary" in wb.sheetnames
    # A sheet exists for each department (names are sanitised + truncated to 31).
    assert "Choir Export" in wb.sheetnames
    assert "Ushering Export" in wb.sheetnames

    choir_ws = wb["Choir Export"]
    text = "\n".join(
        str(c.value) for row in choir_ws.iter_rows() for c in row if c.value is not None
    )
    assert "Alice Export" in text
    assert "Bob Export" in text
    assert "Total who selected: 2" in text
    # Phones are listed; the default columns are Full Name + Phone only.
    assert "0820000001" in text and "0820000002" in text
    header = [c.value for c in next(
        r for r in choir_ws.iter_rows() if (r[0].value == "Full Name"))]
    assert header[:2] == ["Full Name", "Phone"]
    assert "Email" not in header and "Status" not in header

    # An untouched department still gets a sheet, marked empty.
    ush_text = "\n".join(
        str(c.value) for row in wb["Ushering Export"].iter_rows()
        for c in row if c.value is not None
    )
    assert "No selections yet" in ush_text


def test_export_requires_authentication(client):
    client.cookies.clear()
    r = client.get("/api/admin/selection/export")
    assert r.status_code in (401, 403)
