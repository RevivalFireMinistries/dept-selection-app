"""The Exams tab: every sitting, plus the invitations nobody has acted on.

Attempts and invitations live in different tables but answer one question
for the team — "where is everybody up to?" — so the page shows both and
the filter chips work across them. The invitations are the half with no
home anywhere else: an attempt at least appears under Reports, while
someone invited three weeks ago who never opened the email is invisible
until you go looking cycle by cycle.
"""
import inspect

import pytest

import kingdom_gateway_client as kg
from routers import kg as kg_routes


# ── Which chip a row lands under ───────────────────────────────────────────

def _row(**kw):
    base = {"kind": "attempt", "status": "GRADED", "passed": None}
    base.update(kw)
    return base


@pytest.mark.parametrize("row,expected", [
    (_row(kind="invite", status="INVITED"),            "invited"),
    (_row(status="IN_PROGRESS"),                       "in_progress"),
    (_row(status="SUBMITTED"),                         "pending"),
    (_row(status="GRADED", passed=True),               "passed"),
    (_row(status="GRADED", passed=False),              "failed"),
])
def test_rows_land_under_the_right_chip(row, expected):
    assert kg_routes._exam_row_bucket(row) == expected


def test_a_graded_attempt_with_no_verdict_waits_for_marking():
    """percent/passed stay null until the manual half is marked. Calling
    that "failed" would tell someone they did not pass when nobody has
    looked at their paper yet."""
    assert kg_routes._exam_row_bucket(_row(status="GRADED", passed=None)) == "pending"


def test_an_invite_is_bucketed_on_kind_not_status():
    """Enrollment statuses and attempt statuses are different vocabularies
    and INVITED only exists in one of them."""
    assert kg_routes._exam_row_bucket({"kind": "invite", "status": "GRADED"}) == "invited"


def test_every_bucket_chip_can_be_reached():
    """A filter that can never match anything is worse than no filter.

    "missing_milestones" is excluded on purpose: it overlays the passed
    rows rather than being a bucket of its own, because someone blocked
    on a milestone HAS passed — that is the whole point of them.
    """
    reachable = {
        kg_routes._exam_row_bucket(r) for r in [
            _row(kind="invite"), _row(status="IN_PROGRESS"), _row(status="SUBMITTED"),
            _row(status="GRADED", passed=True), _row(status="GRADED", passed=False),
        ]
    }
    chips = {key for key, _ in kg_routes.EXAM_FILTERS if key}
    assert chips - {"missing_milestones"} == reachable


# ── The client calls KG correctly ──────────────────────────────────────────

def _capture(monkeypatch):
    seen = {}

    def fake_request(method, path, *, db=None, params=None, body=None):
        seen.update({"method": method, "path": path, "body": body})
        return kg.ApiResult(ok=True, data={})

    monkeypatch.setattr(kg, "_request", fake_request)
    return seen


def test_cancelling_a_sitting_abandons_it(monkeypatch):
    seen = _capture(monkeypatch)
    kg.abandon_attempt("a1")
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/exam-attempts/a1/abandon"


def test_withdrawing_an_invite_targets_the_enrollment(monkeypatch):
    seen = _capture(monkeypatch)
    kg.revoke_invite("e1")
    assert seen["path"] == "/api/v1/invites/revoke/e1"


def test_issuing_a_certificate_sends_the_name(monkeypatch):
    """KG holds ids, not people, and the certificate prints a name."""
    seen = _capture(monkeypatch)
    kg.issue_certificate("e1", "Sipho Dlamini")
    assert seen["path"] == "/api/v1/certificates/by-enrollment/e1/issue"
    assert seen["body"] == {"member_full_name": "Sipho Dlamini"}


# ── Guards and honesty about outcomes ──────────────────────────────────────

@pytest.mark.parametrize("handler", [
    "admin_kg_exams", "admin_kg_exam_cancel",
    "admin_kg_exam_revoke", "admin_kg_exam_certificate",
])
def test_the_exams_screens_require_a_kg_manager(handler):
    src = inspect.getsource(getattr(kg_routes, handler))
    assert "_require_kg_manage(request, db)" in src


def test_a_cancelled_sitting_is_not_listed():
    """Kept on the record so the history is honest, left out of the list
    someone actually works from."""
    src = inspect.getsource(kg_routes.admin_kg_exams)
    assert '"ABANDONED"' in src and "continue" in src


def test_a_certificate_that_cannot_issue_says_why():
    """"Nothing happened" is the least useful answer a button can give."""
    src = inspect.getsource(kg_routes.admin_kg_exam_certificate)
    assert 'data.get("reasons")' in src
    assert "No certificate for" in src


def test_refusals_from_kg_are_shown_as_they_came():
    """KG's messages name the counts and the reasons; replacing them with
    something generic loses the only useful part."""
    for handler in ["admin_kg_exam_cancel", "admin_kg_exam_revoke"]:
        src = inspect.getsource(getattr(kg_routes, handler))
        assert "r.error or" in src


# ── Recording the milestones that hold up a certificate ────────────────────

def test_every_row_offers_a_way_to_record_milestones():
    """Milestones hold up most certificates and the refusal names them.
    Naming them without saying where to record them is a dead end."""
    env = kg_routes.templates.env
    source = env.loader.get_source(env, "kg/admin_exams.html")[0]
    assert "/admin/kg/cycles/{{ r.cycle_id }}/milestones" in source


def test_the_refusal_points_at_where_to_record_them():
    src = inspect.getsource(kg_routes.admin_kg_exam_certificate)
    assert "Use the Milestones link on their row" in src
    assert '"milestone" in str(r).lower()' in src


def test_marking_a_milestone_sends_the_member_name():
    """Marking the last mandatory milestone is what triggers completion,
    and a certificate cannot issue without a name to print. Without this
    the member is marked COMPLETED with nothing to show for it — done,
    silently, with no certificate."""
    src = inspect.getsource(kg_routes.desk_kg_milestones_mark)
    assert "member_full_name=_enrollment_member_name(" in src


def test_the_name_lookup_never_blocks_the_milestone(monkeypatch):
    """Best-effort: recording that someone was baptised must not fail
    because the directory was unreachable."""
    monkeypatch.setattr(
        kg, "list_enrollments_for_cycle",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("directory down")),
    )
    assert kg_routes._enrollment_member_name("e1", "c1", None) is None


# ── Milestones belong in the admin, not the facilitator's desk ─────────────

def test_the_exams_row_links_to_the_admin_grid_not_the_desk():
    """Following "these milestones are not recorded" used to throw an
    admin into the Class desk — different section, different navigation,
    no way back to what they were doing."""
    env = kg_routes.templates.env
    source = env.loader.get_source(env, "kg/admin_exams.html")[0]
    assert "/admin/kg/cycles/{{ r.cycle_id }}/milestones" in source
    assert "/desk/kg/cycle/{{ r.cycle_id }}/milestones" not in source


def test_both_milestone_screens_read_the_same_data():
    """Two grids that could disagree about who has been baptised is a
    worse problem than one in the wrong place."""
    for handler in ["desk_kg_milestones_page", "admin_kg_cycle_milestones"]:
        src = inspect.getsource(getattr(kg_routes, handler))
        assert "_milestone_grid(cycle_id, db)" in src


def test_the_admin_grid_is_behind_the_manager_check():
    src = inspect.getsource(kg_routes.admin_kg_cycle_milestones)
    assert "_require_kg_manage(request, db)" in src


def test_marking_from_the_admin_grid_also_sends_the_name():
    src = inspect.getsource(kg_routes.admin_kg_cycle_milestones_mark)
    assert "_enrollment_member_name(" in src
    assert "member_full_name=name" in src


def test_it_says_when_a_milestone_completed_someone():
    """Recording the last one issues a certificate. Doing that silently
    is how people end up pressing Issue certificate to find out."""
    src = inspect.getsource(kg_routes.admin_kg_cycle_milestones_mark)
    assert "that completed" in src
    assert "get_certificate_meta" in src


# ── Who is a certificate waiting on? ───────────────────────────────────────

def test_outstanding_milestones_are_worked_out_per_member():
    src = inspect.getsource(kg_routes._milestone_grid)
    assert '_outstanding' in src
    assert 'cm.get("is_mandatory")' in src, "optional milestones must not block"


def test_missing_milestones_is_a_chip():
    keys = [k for k, _ in kg_routes.EXAM_FILTERS]
    assert "missing_milestones" in keys


def test_the_chip_overlays_the_passed_rows_rather_than_replacing_a_bucket():
    """Someone blocked on a milestone HAS passed — that is the whole
    point of them. The chip filters on the field, not the bucket."""
    src = inspect.getsource(kg_routes.admin_kg_exams)
    assert 'if which == "missing_milestones"' in src
    assert 'r["missing_milestones"]' in src


def test_only_passed_rows_are_checked_for_outstanding_milestones():
    """Milestones do not block anything for someone who has not passed,
    and the lookup costs a round trip per cycle."""
    src = inspect.getsource(kg_routes.admin_kg_exams)
    assert 'r["bucket"] == "passed"' in src
