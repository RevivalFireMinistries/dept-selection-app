"""A member who exists in the church directory but has no portal account.

Very common: the office adds someone to rfm-database, or they are invited
to Kingdom Gateway, long before they ever open the portal. The first thing
they do is follow a link and try to sign in.

The portal has always had a fallback for this — look them up centrally by
phone and auto-provision a linked local account. It had never once worked.
rfm-database stores phones canonically as `+27XXXXXXXXX` and its member
search is a substring ILIKE, so searching the local `0XXXXXXXXX` form
matched nothing at all. Every such member was told "No account found with
this phone number", and any Kingdom Gateway invitation waiting for them was
unreachable — the enrollment is keyed on the directory UUID their portal
account never got linked to.
"""
import re

import pytest

import rfm_api_client as rfm


CANONICAL = "+27821110099"
LOCAL = "0821110099"


def _last9(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-9:]


def test_the_two_phone_formats_share_their_last_nine_digits():
    """The premise of the fix: nine digits identify the same person in
    either format, and are a substring of both."""
    assert _last9(LOCAL) == _last9(CANONICAL) == "821110099"
    assert "821110099" in CANONICAL
    assert "821110099" in LOCAL


def test_the_local_form_is_not_a_substring_of_the_canonical_one():
    """Why the old lookup found nobody. Not a style point — this is the
    entire bug, and it is invisible because the search simply returns an
    empty list rather than failing."""
    assert LOCAL not in CANONICAL


@pytest.mark.parametrize("stored", [CANONICAL, LOCAL, "+27 82 111 0099", "082-111-0099"])
def test_a_directory_record_is_recognised_however_it_is_stored(stored):
    """Whatever shape the directory holds, the last nine digits match what
    the member typed."""
    assert _last9(stored) == _last9(LOCAL)


def test_the_login_lookup_searches_by_the_national_significant_number():
    """Pins the call the login fallback makes. If someone later 'tidies'
    this back to passing the raw phone, directory-only members silently
    stop being able to sign in again."""
    import inspect

    from routers import api

    src = inspect.getsource(api.login_member)
    # The search must use the nine-digit form, not the raw local number.
    assert "_rfm.search_members(phone=target_last9" in src, (
        "login must look the member up by their national significant number"
    )
    assert "search_members(phone=normalized" not in src, (
        "searching the local 0XX form matches nothing in rfm-database"
    )


def test_a_failed_central_lookup_is_logged_not_swallowed():
    """It was `except Exception: pass`, which is how a fallback can be
    broken for its whole life without anyone noticing."""
    import inspect

    from routers import api

    src = inspect.getsource(api.login_member)
    assert "logger.exception" in src


def test_a_phone_is_sent_as_the_search_term(monkeypatch):
    """search_members has no dedicated phone endpoint — it folds the phone
    into the `search` ILIKE. The fix depends on that staying true, so pin
    the parameter that actually goes over the wire."""
    seen = {}

    def fake_request(method, path, *, db=None, params=None, body=None):
        seen.update({"path": path, "params": params or {}})
        return rfm.ApiResult(ok=True, data=[])

    monkeypatch.setattr(rfm, "_request", fake_request)
    rfm.search_members(phone="821110099")

    assert seen["path"] == "/api/v1/members"
    assert seen["params"]["search"] == "821110099", (
        "the phone must travel as the search term, or the ILIKE never runs"
    )
