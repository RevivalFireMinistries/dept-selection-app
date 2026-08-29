"""Reassigning who runs a service.

The Edit Program form has always offered a Service Manager field and both
editors have always sent it. It was missing from ServiceProgramUpdate, so
Pydantic dropped it before the endpoint saw it — the field looked editable,
accepted a change, and silently saved nothing.

That failure is invisible from the outside: the request succeeds, the page
reloads, and the old name comes back. These tests pin the field to the
round trip rather than to the request succeeding.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest


@pytest.fixture
def program(db, make_member):
    """A program with a manager already set, so reassignment is a change."""
    from models import ServiceProgram

    original = make_member(full_name="Elder Precious Hamandishe")
    p = ServiceProgram(
        title="SUNDAY SERVICE",
        service_date=date.today() + timedelta(days=7),
        location_type="onsite",
        program_items=json.dumps([{"time": "09:30", "item": "1st Prayer"}]),
        participants=json.dumps([]),
        admin_announcements=json.dumps([]),
        pastors_announcements=json.dumps([]),
        prayer_points=json.dumps([]),
        created_by_member_id=original.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    yield p, original
    db.query(ServiceProgram).filter(ServiceProgram.id == p.id).delete()
    db.commit()


def put(client, program_id, body):
    return client.put(f"/api/admin/programs/{program_id}", json=body)


# ── The bug ──────────────────────────────────────────────────────────────────


def test_the_service_manager_can_be_changed(client, db, program, make_member):
    p, _ = program
    incoming = make_member(full_name="Pastor Russel Mupfumira")

    r = put(client, p.id, {"created_by_member_id": incoming.id})
    assert r.status_code == 200


def test_the_change_actually_persists(client, db, program, make_member):
    """The whole failure: the request succeeded and nothing was saved."""
    from models import ServiceProgram

    p, _ = program
    incoming = make_member(full_name="Pastor Russel Mupfumira")
    put(client, p.id, {"created_by_member_id": incoming.id})

    db.expire_all()
    stored = db.query(ServiceProgram).filter(ServiceProgram.id == p.id).first()
    assert stored.created_by_member_id == incoming.id


def test_the_response_reflects_the_new_manager(client, db, program, make_member):
    """So the page that just saved shows the new name rather than the old."""
    p, _ = program
    incoming = make_member(full_name="Pastor Russel Mupfumira")

    body = put(client, p.id, {"created_by_member_id": incoming.id}).json()
    assert body.get("created_by_member_id") == incoming.id


def test_reloading_the_program_shows_the_new_manager(client, db, program, make_member):
    p, _ = program
    incoming = make_member(full_name="Pastor Russel Mupfumira")
    put(client, p.id, {"created_by_member_id": incoming.id})

    listed = client.get("/api/admin/programs").json()
    mine = next((x for x in listed if x["id"] == p.id), None)
    assert mine is not None
    assert mine.get("created_by_member_id") == incoming.id


# ── Not sending it must not wipe it ──────────────────────────────────────────


def test_editing_something_else_leaves_the_manager_alone(client, db, program):
    """Every other save — reordering the running order, fixing a title —
    omits this field, and must not clear it as a side effect."""
    from models import ServiceProgram

    p, original = program
    r = put(client, p.id, {"title": "SUNDAY SERVICE (REVISED)"})
    assert r.status_code == 200

    db.expire_all()
    stored = db.query(ServiceProgram).filter(ServiceProgram.id == p.id).first()
    assert stored.created_by_member_id == original.id
    assert stored.title == "SUNDAY SERVICE (REVISED)"


def test_the_manager_can_be_cleared(client, db, program):
    """Sent explicitly as null — distinguishable from not sending it."""
    from models import ServiceProgram

    p, _ = program
    r = put(client, p.id, {"created_by_member_id": None})
    assert r.status_code == 200

    db.expire_all()
    stored = db.query(ServiceProgram).filter(ServiceProgram.id == p.id).first()
    assert stored.created_by_member_id is None


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_an_unknown_member_is_refused(client, db, program):
    """Better a clear 404 than a foreign key error, or a program pointing at
    somebody who doesn't exist."""
    from models import ServiceProgram

    p, original = program
    r = put(client, p.id, {"created_by_member_id": 999999})
    assert r.status_code == 404

    db.expire_all()
    stored = db.query(ServiceProgram).filter(ServiceProgram.id == p.id).first()
    assert stored.created_by_member_id == original.id


def test_a_missing_program_is_a_404(client, make_member):
    incoming = make_member()
    assert put(client, 999999, {"created_by_member_id": incoming.id}).status_code == 404


# ── Reassigning alongside other edits ────────────────────────────────────────


def test_the_manager_and_other_fields_change_together(client, db, program, make_member):
    """The real save: someone edits the running order and hands the service
    over in the same action."""
    from models import ServiceProgram

    p, _ = program
    incoming = make_member(full_name="Pastor Russel Mupfumira")

    r = put(client, p.id, {
        "title": "SUNDAY SERVICE",
        "created_by_member_id": incoming.id,
        "program_items": [
            {"time": "09:30", "item": "1st Prayer"},
            {"time": "09:40", "item": "Praise"},
        ],
    })
    assert r.status_code == 200

    db.expire_all()
    stored = db.query(ServiceProgram).filter(ServiceProgram.id == p.id).first()
    assert stored.created_by_member_id == incoming.id
    assert len(json.loads(stored.program_items)) == 2
