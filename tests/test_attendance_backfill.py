"""Recording the register for a cycle that ran outside the system.

The classes genuinely happened — they were just never marked here. The
register is filled in retrospectively rather than attendance being waived,
so a certificate still rests on a record of the person having attended,
and the ordinary completion rule applies unchanged.

Two properties matter and are easy to get wrong: it must not overwrite a
register somebody kept properly, and it must not mark people who were
never on the course.
"""
import pytest


def _plan(classes, enrollment_ids, already, status="PRESENT"):
    """The decision the route makes, isolated from HTTP and the KG client.

    Mirrors routers/kg.py::desk_kg_attendance_backfill — per session, mark
    everyone not already on the register for that session.
    """
    per_session = {}
    skipped = 0
    for class_id in classes:
        entries = [
            {"enrollment_id": eid, "status": status, "method": "MANUAL"}
            for eid in enrollment_ids if (eid, class_id) not in already
        ]
        skipped += len(enrollment_ids) - len(entries)
        per_session[class_id] = entries
    return per_session, skipped


CLASSES = ["c1", "c2", "c3"]
PEOPLE = ["e1", "e2"]


def test_every_member_is_marked_for_every_session():
    plan, skipped = _plan(CLASSES, PEOPLE, already=set())
    assert sum(len(v) for v in plan.values()) == 6
    assert skipped == 0


def test_a_register_already_kept_is_left_alone():
    """The point is to fill gaps. Someone who marked class 1 properly must
    not have their work overwritten by a bulk backfill."""
    already = {("e1", "c1"), ("e2", "c1")}
    plan, skipped = _plan(CLASSES, PEOPLE, already)

    assert plan["c1"] == []
    assert skipped == 2
    assert sum(len(v) for v in plan.values()) == 4


def test_a_partly_kept_register_is_completed_not_replaced():
    """One person marked, the other missed — only the gap is filled."""
    plan, _ = _plan(CLASSES, PEOPLE, already={("e1", "c2")})
    marked_for_c2 = [e["enrollment_id"] for e in plan["c2"]]
    assert marked_for_c2 == ["e2"]


def test_entries_carry_what_the_bulk_endpoint_needs():
    """The session id travels once, in the envelope — NOT on each entry.
    Putting it on the entry is what made every bulk mark fail with a 422
    naming a field the endpoint does not even read."""
    plan, _ = _plan(CLASSES, PEOPLE, already=set())
    for entries in plan.values():
        for e in entries:
            assert set(e) == {"enrollment_id", "status", "method"}
            assert "class_session_id" not in e
            assert e["method"] == "MANUAL"


@pytest.mark.parametrize("status", ["PRESENT", "LATE", "EXCUSED", "ABSENT"])
def test_the_chosen_status_is_what_gets_written(status):
    plan, _ = _plan(CLASSES, PEOPLE, already=set(), status=status)
    assert all(e["status"] == status for v in plan.values() for e in v)


def test_nobody_is_marked_when_there_are_no_sessions():
    plan, skipped = _plan([], PEOPLE, already=set())
    assert plan == {} and skipped == 0


def test_withdrawn_members_are_excluded_before_planning():
    """Pins the filter in the route: a withdrawal must not be marked
    present for classes they were not part of."""
    import inspect

    from routers import kg

    src = inspect.getsource(kg.desk_kg_attendance_backfill)
    assert '"WITHDRAWN", "EXEMPTED"' in src


def test_the_route_reports_what_it_did():
    """A silent bulk write over someone's register is not acceptable —
    it has to say how many it wrote and how many it left."""
    import inspect

    from routers import kg

    src = inspect.getsource(kg.desk_kg_attendance_backfill)
    assert "already-marked alone" in src
    assert "Recorded" in src
