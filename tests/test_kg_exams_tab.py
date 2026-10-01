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


def test_every_chip_has_a_bucket_that_can_reach_it():
    """A filter that can never match anything is worse than no filter."""
    reachable = {
        kg_routes._exam_row_bucket(r) for r in [
            _row(kind="invite"), _row(status="IN_PROGRESS"), _row(status="SUBMITTED"),
            _row(status="GRADED", passed=True), _row(status="GRADED", passed=False),
        ]
    }
    chips = {key for key, _ in kg_routes.EXAM_FILTERS if key}
    assert chips == reachable


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
