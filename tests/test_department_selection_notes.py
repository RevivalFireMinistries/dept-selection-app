"""Per-department selection notes (the clarifying prompts members see when they
tick a department, e.g. a committee being a leadership role) and their exposure
through the departments API.

Covers:
 - GET /api/departments always carries selectionNote + noteSeverity (defaults).
 - POST /api/departments/set-note sets and clears the note (admin only).
 - The endpoint is guarded — an anonymous caller is refused.
"""
import json

import pytest

from models import Department


@pytest.fixture
def department(db):
    """A throwaway department, cleaned up afterwards."""
    d = Department(name="Couples Ministry Committee")
    db.add(d)
    db.commit()
    db.refresh(d)
    yield d
    obj = db.query(Department).filter(Department.id == d.id).first()
    if obj:
        db.delete(obj)
        db.commit()


@pytest.fixture
def admin_login(db, client, make_member, settings_value):
    """Log the TestClient in as an admin; cookies persist on the client."""
    admin = make_member(full_name="Admin Person", password="pw")
    admin.leadership_roles = json.dumps(["admin"])
    db.commit()
    settings_value("adminPassword", "admin123")
    r = client.post("/admin/login",
                    data={"phone": admin.phone, "password": "admin123"},
                    follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    return admin


def _find(groups, dept_id):
    for d in groups.get("uncategorized", []):
        if d["id"] == dept_id:
            return d
    for c in groups.get("categories", []):
        for d in c.get("departments", []):
            if d["id"] == dept_id:
                return d
    return None


def test_departments_api_always_carries_note_fields(client, department):
    groups = client.get("/api/departments?include_inactive=true").json()
    d = _find(groups, department.id)
    assert d is not None
    # Defaults: no note, info severity — present so the UI can bind safely.
    assert d["selectionNote"] == ""
    assert d["noteSeverity"] == "info"


def test_admin_can_set_and_clear_a_warning_note(db, client, department, admin_login):
    note = "This is the leadership committee — not the same as joining Couples Ministry."
    r = client.post("/api/departments/set-note",
                    json={"id": department.id, "note": note, "severity": "warning"})
    assert r.status_code == 200, r.text

    db.refresh(department)
    assert department.selection_note == note
    assert department.note_severity == "warning"

    d = _find(client.get("/api/departments?include_inactive=true").json(), department.id)
    assert d["selectionNote"] == note
    assert d["noteSeverity"] == "warning"

    # Clearing: empty note wipes it and resets severity.
    r = client.post("/api/departments/set-note",
                    json={"id": department.id, "note": "", "severity": "info"})
    assert r.status_code == 200, r.text
    db.refresh(department)
    assert department.selection_note is None
    assert department.note_severity == "info"


def test_an_unknown_severity_falls_back_to_info(db, client, department, admin_login):
    r = client.post("/api/departments/set-note",
                    json={"id": department.id, "note": "x", "severity": "banana"})
    assert r.status_code == 200, r.text
    db.refresh(department)
    assert department.note_severity == "info"


def test_setting_a_note_requires_authentication(client, department):
    # Drop any admin cookies left on the shared client by earlier tests, so this
    # call arrives unauthenticated and must be refused.
    client.cookies.clear()
    r = client.post("/api/departments/set-note",
                    json={"id": department.id, "note": "x", "severity": "info"})
    assert r.status_code in (401, 403)
