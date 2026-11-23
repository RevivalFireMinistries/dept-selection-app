"""Admin search for an individual's campaign selections.

GET /api/admin/selection/member-search?q= searches EVERY member by name or
phone and returns each with their campaign-year selections — including members
who have selected nothing (so admins can pull anyone up and assign them).
"""
import json

import pytest

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
def department(db):
    d = Department(name="Welcome Team")
    db.add(d)
    db.commit()
    db.refresh(d)
    yield d
    obj = db.query(Department).filter(Department.id == d.id).first()
    if obj:
        db.delete(obj)
        db.commit()


def _find(members, member_id):
    return next((m for m in members if m["id"] == member_id), None)


def test_search_returns_member_with_their_selections(db, client, department,
                                                     make_member, admin_login, settings_value):
    settings_value("selectionCampaignYear", "2031")
    settings_value("selectionCampaignOpen", "true")
    m = make_member(full_name="Zomato Searchable")
    db.add(DepartmentSelection(year=2031, member_id=m.id, department_id=department.id,
                               status="pending", source="member"))
    db.commit()

    body = client.get("/api/admin/selection/member-search?q=Zomato").json()
    assert body["year"] == 2031
    found = _find(body["members"], m.id)
    assert found is not None
    assert [s["department_name"] for s in found["selections"]] == ["Welcome Team"]
    assert found["selections"][0]["status"] == "pending"


def test_search_returns_non_selector_with_empty_list(db, client, make_member, admin_login):
    m = make_member(full_name="Qwerty Neverpicked")
    body = client.get("/api/admin/selection/member-search?q=Qwerty").json()
    found = _find(body["members"], m.id)
    assert found is not None, "a member who selected nothing must still be findable"
    assert found["selections"] == []


def test_search_matches_by_phone(db, client, make_member, admin_login):
    m = make_member(full_name="Phone Match", phone="0834567890")
    body = client.get("/api/admin/selection/member-search?q=0834567890").json()
    assert _find(body["members"], m.id) is not None


def test_short_query_returns_nothing(db, client, make_member, admin_login):
    make_member(full_name="A Person")
    body = client.get("/api/admin/selection/member-search?q=a").json()
    assert body["members"] == []


def test_search_requires_authentication(client, make_member):
    make_member(full_name="Hidden Person")
    client.cookies.clear()
    r = client.get("/api/admin/selection/member-search?q=Hidden")
    assert r.status_code in (401, 403)
