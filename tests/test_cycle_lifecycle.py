"""Closing and deleting a cycle, and when a cycle becomes active.

A cycle is the root of everything: enrollments hang off it, and through
them the register, the exam attempts and the certificates. All of it
cascades, so deleting a cycle that people actually used would quietly
take their results with it. Closing is the normal end of a cycle;
deleting is only for one set up by mistake.
"""
import inspect

import pytest

import kingdom_gateway_client as kg
from routers import kg as kg_routes


# ── The client speaks the right words to KG ────────────────────────────────

def _capture(monkeypatch):
    seen = {}

    def fake_request(method, path, *, db=None, params=None, body=None):
        seen.update({"method": method, "path": path, "body": body})
        return kg.ApiResult(ok=True, data={})

    monkeypatch.setattr(kg, "_request", fake_request)
    return seen


def test_closing_sets_the_status_rather_than_removing_anything(monkeypatch):
    seen = _capture(monkeypatch)
    kg.close_cycle("abc")
    assert seen["method"] == "PATCH"
    assert seen["path"] == "/api/v1/cycles/abc"
    assert seen["body"] == {"status": "CLOSED"}


def test_a_close_can_be_undone(monkeypatch):
    """Someone always closes one a week early."""
    seen = _capture(monkeypatch)
    kg.reopen_cycle("abc")
    assert seen["body"] == {"status": "ACTIVE"}


def test_deleting_is_a_delete_and_carries_no_body(monkeypatch):
    seen = _capture(monkeypatch)
    kg.delete_cycle("abc")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/api/v1/cycles/abc"
    assert seen["body"] is None


# ── The refusal has to reach the person who clicked ────────────────────────

def test_a_refused_delete_shows_kgs_own_reason():
    """KG's refusal names the counts and tells them to close instead.
    Replacing it with something generic would leave the admin guessing at
    why the button did nothing."""
    src = inspect.getsource(kg_routes.admin_kg_cycle_delete)
    assert "r.error or" in src, "KG's message must be preferred over a generic one"


def test_a_refused_delete_lands_back_on_the_cycle():
    """Not on the list — the Close button they now need is on the cycle."""
    src = inspect.getsource(kg_routes.admin_kg_cycle_delete)
    assert 'f"/admin/kg/cycles/{cycle_id}"' in src


def test_a_successful_delete_leaves_the_cycle_page():
    """The page it would return to no longer exists."""
    src = inspect.getsource(kg_routes.admin_kg_cycle_delete)
    assert '_flash_redirect("/admin/kg/cycles"' in src


# ── The flash has to survive the redirect ──────────────────────────────────

def test_the_flash_cookie_is_scoped_to_the_whole_site():
    """Without an explicit path a cookie defaults to the DIRECTORY of the
    request that set it, so a flash set on /admin/kg/cycles/<id>/delete
    was never sent to /admin/kg/cycles. The message vanished on exactly
    the redirects that go somewhere else."""
    src = inspect.getsource(kg_routes._set_flash)
    assert 'path="/"' in src


def test_clearing_the_flash_uses_the_same_path():
    """A delete_cookie that misses leaves the message to repeat on the
    next page."""
    src = inspect.getsource(kg_routes._clear_flash)
    assert 'path="/"' in src


# ── Guarding both actions ──────────────────────────────────────────────────

@pytest.mark.parametrize("handler", ["admin_kg_cycle_close", "admin_kg_cycle_delete"])
def test_both_actions_require_a_kg_manager(handler):
    src = inspect.getsource(getattr(kg_routes, handler))
    assert "_require_kg_manage(request, db)" in src


@pytest.mark.parametrize("handler", ["admin_kg_cycle_close", "admin_kg_cycle_delete"])
def test_both_actions_respect_the_kill_switch(handler):
    src = inspect.getsource(getattr(kg_routes, handler))
    assert "kg.is_enabled(db)" in src
