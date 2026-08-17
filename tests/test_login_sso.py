"""Nobody is denied access by the single sign-on migration.

The portal has two ways in — the identity provider and its own password
column — and the login route tries both. These tests cover every population
that exists at cutover, because the migration's whole promise is that each
of them keeps working:

  1. an existing member whose password never left the portal
  2. a member migrated to the provider
  3. a brand-new member who only ever had a central credential
  4. a member with no password anywhere
  5. anyone at all, while the provider is unreachable

Order matters and is the subtle part. SSO is attempted BEFORE the
first-time-sign-in branch; the other way round, population 3 would be told
to use their phone number as a password and locked out of an account whose
real password is perfectly valid.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def provider(monkeypatch):
    """A stand-in identity provider whose behaviour each test decides.

    Patching `sso_auth` rather than the network keeps these tests about the
    login route's decisions. How a token is verified is covered separately
    in test_sso_auth.py.
    """
    import sso_auth

    state = {"configured": True, "accepts": {}, "calls": [], "down": False}

    def is_configured():
        return state["configured"]

    def start_session(*, phone, email, password):
        state["calls"].append({"phone": phone, "email": email, "password": password})
        if state["down"]:
            raise OSError("connection refused")
        for identifier in (phone, email):
            if identifier and state["accepts"].get(identifier) == password:
                return True, [f"rfm_sso=session-for-{identifier}; Path=/; HttpOnly"]
        return False, []

    monkeypatch.setattr(sso_auth, "is_configured", is_configured)
    monkeypatch.setattr(sso_auth, "start_session", start_session)

    class Provider:
        def knows(self, identifier, password):
            state["accepts"][identifier] = password

        def turn_off(self):
            state["configured"] = False

        def go_down(self):
            state["down"] = True

        @property
        def calls(self):
            return state["calls"]

    return Provider()


def login(client, phone, password):
    return client.post("/api/auth/login", json={"phone": phone, "password": password})


# ── The five populations ─────────────────────────────────────────────────────


def test_a_local_password_still_works_after_the_migration(client, provider, make_member):
    """Population 1 — the largest, and the one a bad cutover would break."""
    member = make_member(password="their-old-password")
    r = login(client, member.phone, "their-old-password")
    assert r.status_code == 200
    assert r.json()["member_id"] == member.id


def test_a_migrated_member_signs_in_through_the_provider(client, provider, make_member):
    """Population 2 — the same password, now verified centrally."""
    member = make_member(password="migrated-password")
    provider.knows(member.phone, "migrated-password")
    r = login(client, member.phone, "migrated-password")
    assert r.status_code == 200


def test_a_new_member_with_only_a_central_credential_gets_in(client, provider, db, make_member):
    """Population 3 — the one the ordering exists to protect."""
    member = make_member(password=None)
    provider.knows(member.phone, "chosen-centrally")
    r = login(client, member.phone, "chosen-centrally")
    assert r.status_code == 200
    assert r.json().get("needs_password") is None


def test_a_member_with_no_password_anywhere_keeps_the_first_time_flow(
    client, provider, make_member
):
    """Population 4 — unchanged behaviour: the phone number is the default."""
    member = make_member(password=None)
    r = login(client, member.phone, member.phone)
    assert r.status_code == 200
    body = r.json()
    assert body["needs_password"] is True
    assert body["token"]


def test_the_local_password_works_while_the_provider_is_unreachable(
    client, provider, make_member
):
    """Population 5 — an outage must not become an outage of the portal."""
    member = make_member(password="local-copy")
    provider.go_down()
    r = login(client, member.phone, "local-copy")
    assert r.status_code == 200


# ── The fallback keeps itself current ────────────────────────────────────────


def test_a_central_only_password_is_mirrored_locally_on_first_use(
    client, provider, db, make_member
):
    """So a later provider outage can't lock out someone who signed in a
    minute ago. Without this, population 3 becomes population 5's casualty."""
    from models import Member

    member = make_member(password=None)
    provider.knows(member.phone, "chosen-centrally")
    login(client, member.phone, "chosen-centrally")

    db.expire_all()
    stored = db.query(Member).filter(Member.id == member.id).first()
    assert stored.password_hash, "the central password was not mirrored"


def test_the_mirrored_password_works_after_the_provider_goes_away(
    client, provider, make_member
):
    member = make_member(password=None)
    provider.knows(member.phone, "chosen-centrally")
    assert login(client, member.phone, "chosen-centrally").status_code == 200

    provider.go_down()
    assert login(client, member.phone, "chosen-centrally").status_code == 200


def test_mirroring_does_not_overwrite_a_password_the_portal_already_held(
    client, provider, db, make_member
):
    from models import Member

    member = make_member(password="portal-password")
    before = db.query(Member).filter(Member.id == member.id).first().password_hash
    provider.knows(member.phone, "central-password")
    login(client, member.phone, "central-password")

    db.expire_all()
    after = db.query(Member).filter(Member.id == member.id).first().password_hash
    assert after == before


# ── Refusals are still refusals ──────────────────────────────────────────────


def test_a_wrong_password_is_refused_by_both_paths(client, provider, make_member):
    member = make_member(password="correct")
    provider.knows(member.phone, "correct")
    r = login(client, member.phone, "wrong")
    assert r.status_code == 401


def test_an_unknown_phone_number_is_refused(client, provider):
    r = login(client, "0790000000", "anything")
    assert r.status_code == 401


def test_a_missing_phone_number_is_a_bad_request_not_a_refusal(client, provider):
    assert client.post("/api/auth/login", json={"password": "x"}).status_code == 400


def test_an_inactive_member_is_told_they_are_pending_not_that_they_are_wrong(
    client, provider, make_member
):
    member = make_member(password="correct", is_active=False)
    r = login(client, member.phone, "correct")
    assert r.status_code == 403
    assert "approval" in r.json()["detail"].lower()


def test_an_inactive_member_is_still_refused_when_the_provider_accepts_them(
    client, provider, make_member
):
    """Authentication is central; authorisation stays local."""
    member = make_member(password=None, is_active=False)
    provider.knows(member.phone, "central-password")
    assert login(client, member.phone, "central-password").status_code == 403


# ── Single sign-on has to actually be single ─────────────────────────────────


def test_the_providers_cookie_is_passed_on_to_the_browser(client, provider, make_member):
    """Without re-emitting it the member is signed in here and nowhere
    else — single sign-on in name only."""
    member = make_member(password=None)
    provider.knows(member.phone, "central-password")
    r = login(client, member.phone, "central-password")
    cookies = [v for k, v in r.headers.raw if k.lower() == b"set-cookie"]
    assert any(b"rfm_sso=" in c for c in cookies)


def test_the_portals_own_session_cookie_is_still_set(client, provider, make_member):
    member = make_member(password="local")
    r = login(client, member.phone, "local")
    cookies = [v for k, v in r.headers.raw if k.lower() == b"set-cookie"]
    assert any(b"HttpOnly" in c for c in cookies)


def test_no_password_attempt_reaches_the_provider_when_sso_is_off(
    client, provider, make_member
):
    member = make_member(password="local")
    provider.turn_off()
    assert login(client, member.phone, "local").status_code == 200
    assert provider.calls == []


def test_a_provider_error_is_absorbed_rather_than_surfaced(client, provider, make_member):
    """`start_session` raising must not turn into a 500 for the member."""
    member = make_member(password="local")
    provider.go_down()
    r = login(client, member.phone, "local")
    assert r.status_code == 200
