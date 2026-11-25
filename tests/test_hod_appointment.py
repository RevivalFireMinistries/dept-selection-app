"""Appointing a Head of Department.

Covers:
 - Staging an INCOMING HOD (during a campaign) emails them, flagged incoming,
   with the department name and campaign year.
 - Re-saving the same incoming HOD does not re-notify.
 - Setting an ACTIVE HOD emails them, flagged not-incoming.
 - An incoming HOD can already see their department's campaign joiners
   (pending included) via /api/hod/departments while the campaign is open.
"""
import json

import pytest

from models import Department, DepartmentSelection


@pytest.fixture
def admin_login(db, client, make_member, settings_value):
    """Log the shared TestClient in as an admin."""
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
    d = Department(name="Ushering Committee")
    db.add(d)
    db.commit()
    db.refresh(d)
    yield d
    obj = db.query(Department).filter(Department.id == d.id).first()
    if obj:
        db.delete(obj)
        db.commit()


@pytest.fixture
def capture_dispatch(monkeypatch):
    """Capture dispatch_event calls instead of sending anything."""
    calls = []

    def fake_dispatch(db, event_type, data, recipients=None):
        calls.append({"event_type": event_type, "data": data})

    # The endpoints do `from notifications.dispatcher import dispatch_event`
    # at call time, so patching the module attribute is enough.
    monkeypatch.setattr("notifications.dispatcher.dispatch_event", fake_dispatch)
    return calls


def _hod_calls(calls):
    from notifications.events import EventType
    return [c for c in calls if c["event_type"] == EventType.HOD_ASSIGNED]


def test_staging_incoming_hod_emails_them(db, client, department, make_member,
                                          admin_login, capture_dispatch, settings_value):
    settings_value("selectionCampaignYear", "2031")
    settings_value("selectionCampaignOpen", "true")
    hod = make_member(full_name="New Leader", email="lead@example.com")

    r = client.post(f"/api/admin/departments/{department.id}/set-pending-hod",
                    json={"member_id": hod.id})
    assert r.status_code == 200, r.text

    calls = _hod_calls(capture_dispatch)
    assert len(calls) == 1
    data = calls[0]["data"]
    assert data["incoming"] is True
    assert data["department_name"] == "Ushering Committee"
    assert data["member_email"] == "lead@example.com"
    assert data["year"] == 2031


def test_restaging_same_incoming_hod_does_not_reemail(db, client, department,
                                                      make_member, admin_login, capture_dispatch):
    hod = make_member(full_name="New Leader", email="lead@example.com")
    url = f"/api/admin/departments/{department.id}/set-pending-hod"
    assert client.post(url, json={"member_id": hod.id}).status_code == 200
    capture_dispatch.clear()
    # Saving the same person again changes nothing — no second email.
    assert client.post(url, json={"member_id": hod.id}).status_code == 200
    assert _hod_calls(capture_dispatch) == []


def test_setting_active_hod_emails_them_not_incoming(db, client, department,
                                                     make_member, admin_login, capture_dispatch):
    hod = make_member(full_name="Active Leader", email="active@example.com")
    r = client.post(f"/api/admin/departments/{department.id}/set-hod",
                    json={"member_id": hod.id})
    assert r.status_code == 200, r.text

    calls = _hod_calls(capture_dispatch)
    assert len(calls) == 1
    assert calls[0]["data"]["incoming"] is False
    assert calls[0]["data"]["department_name"] == "Ushering Committee"


def test_incoming_hod_sees_pending_campaign_joiners(db, client, department,
                                                    make_member, settings_value):
    settings_value("selectionCampaignYear", "2031")
    settings_value("selectionCampaignOpen", "true")
    hod = make_member(full_name="Future HOD")
    joiner = make_member(full_name="Keen Member")

    department.pending_hod_member_id = hod.id
    db.add(DepartmentSelection(year=2031, member_id=joiner.id,
                               department_id=department.id,
                               status="pending", source="member"))
    db.commit()

    r = client.get(f"/api/hod/departments?phone={hod.phone}")
    assert r.status_code == 200, r.text
    data = r.json()
    dept = next((d for d in data["departments"] if d["id"] == department.id), None)
    assert dept is not None, "incoming HOD should see their staged department"
    assert dept["is_incoming"] is True
    assert dept["campaign"]["year"] == 2031
    assert dept["campaign"]["pending"] == 1
    names = [m["full_name"] for m in dept["campaign"]["members"]]
    assert "Keen Member" in names


def test_portal_me_surfaces_incoming_hod_on_dashboard(db, client, department,
                                                      make_member, settings_value):
    """The portal dashboard data flags a staged incoming HOD and carries the
    campaign joiner counts so the 'Leading a department' card can render."""
    from routers.pages import MEMBER_COOKIE_NAME, _sign_member_session
    settings_value("selectionCampaignYear", "2031")
    settings_value("selectionCampaignOpen", "true")
    hod = make_member(full_name="Dash HOD")
    joiner = make_member(full_name="Joiner One")
    department.pending_hod_member_id = hod.id
    db.add(DepartmentSelection(year=2031, member_id=joiner.id,
                               department_id=department.id,
                               status="pending", source="member"))
    db.commit()

    client.cookies.set(MEMBER_COOKIE_NAME, _sign_member_session(hod.id))
    try:
        me = client.get("/api/portal/me").json()
    finally:
        client.cookies.clear()

    lead = me["leadership"]
    assert lead["is_hod"] is True
    assert lead["hod_campaign_year"] == 2031
    dep = next((d for d in lead["hod_departments"] if d["id"] == department.id), None)
    assert dep is not None
    assert dep["incoming"] is True
    assert dep["joining"] == 1
    assert dep["pending"] == 1


def test_portal_me_hides_incoming_hod_when_campaign_closed(db, client, department,
                                                          make_member, settings_value):
    from routers.pages import MEMBER_COOKIE_NAME, _sign_member_session
    settings_value("selectionCampaignOpen", "false")
    hod = make_member(full_name="Closed HOD")
    department.pending_hod_member_id = hod.id
    db.commit()

    client.cookies.set(MEMBER_COOKIE_NAME, _sign_member_session(hod.id))
    try:
        me = client.get("/api/portal/me").json()
    finally:
        client.cookies.clear()

    lead = me["leadership"]
    # An incoming-only HOD must not surface while the campaign is closed.
    assert lead["is_hod"] is False
    assert lead["hod_campaign_year"] is None
