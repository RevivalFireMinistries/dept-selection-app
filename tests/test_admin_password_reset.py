"""Getting a locked-out member back in.

Two tools, because the two situations differ. Someone who can read email
gets a link. Someone standing at the desk, or whose address on file is
wrong, gets their phone number as a one-use password.

The property that matters most: a forced password change is checked AFTER
authentication, whichever way they authenticated. If the check lived inside
either sign-in branch, single sign-on being on or off would decide whether
an admin's forced reset was actually enforced.
"""
from __future__ import annotations

import re

import pytest

INVITE_KEY = "test-invite-key"


@pytest.fixture(autouse=True)
def invite_key(monkeypatch):
    monkeypatch.setenv("PORTAL_INVITE_API_KEY", INVITE_KEY)


@pytest.fixture
def admin_headers():
    return {"X-Portal-Invite-Key": INVITE_KEY}


@pytest.fixture
def provider(monkeypatch):
    """Stand in for the identity provider, recording what we push to it."""
    import sso_auth

    state = {"configured": True, "accepts": {}, "pushed": []}

    monkeypatch.setattr(sso_auth, "is_configured", lambda: state["configured"])

    def start_session(*, phone, email, password):
        for ident in (phone, email):
            if ident and state["accepts"].get(ident) == password:
                return True, ["rfm_sso=abc; Path=/"]
        return False, []

    monkeypatch.setattr(sso_auth, "start_session", start_session)

    import routers.api as api

    def fake_push(member, plain):
        state["pushed"].append((member.id, plain))
        return True

    monkeypatch.setattr(api, "_push_credential_to_sso", fake_push)

    class Provider:
        @property
        def pushed(self):
            return state["pushed"]

        def knows(self, ident, password):
            state["accepts"][ident] = password

        def turn_off(self):
            state["configured"] = False

    return Provider()


def login(client, phone, password):
    return client.post("/api/auth/login", json={"phone": phone, "password": password})


def digits_of(phone):
    return re.sub(r"\D", "", phone)


# ── Reset link ───────────────────────────────────────────────────────────────


def test_the_link_is_returned_not_just_emailed(client, admin_headers, make_member):
    """Email is often the very thing broken for the person who is stuck, so
    an admin who cannot see the link cannot help them."""
    member = make_member(password="old")
    r = client.post("/api/portal/admin/reset-link", headers=admin_headers,
                    json={"phone": member.phone, "send_email": False})
    assert r.status_code == 200
    assert "/reset-password?token=" in r.json()["reset_url"]


def test_the_link_carries_an_expiry(client, admin_headers, make_member):
    member = make_member(password="old")
    body = client.post("/api/portal/admin/reset-link", headers=admin_headers,
                       json={"phone": member.phone, "send_email": False}).json()
    assert body["expires_at"]


def test_a_failed_email_is_reported_rather_than_hidden(client, admin_headers, make_member):
    """The self-service flow claims success even when nothing was sent. An
    admin tool must not repeat that."""
    member = make_member(password="old", email="")
    body = client.post("/api/portal/admin/reset-link", headers=admin_headers,
                       json={"phone": member.phone}).json()
    assert body["emailed"] is False
    assert body["email_error"]


def test_the_link_actually_resets_the_password(client, admin_headers, make_member):
    member = make_member(password="old")
    url = client.post("/api/portal/admin/reset-link", headers=admin_headers,
                      json={"phone": member.phone, "send_email": False}).json()["reset_url"]
    token = url.split("token=")[1]

    r = client.post("/api/auth/reset-password",
                    json={"token": token, "password": "brand-new-one"})
    assert r.status_code == 200
    assert login(client, member.phone, "brand-new-one").status_code == 200


def test_an_unknown_member_is_a_404(client, admin_headers):
    r = client.post("/api/portal/admin/reset-link", headers=admin_headers,
                    json={"phone": "0790000000"})
    assert r.status_code == 404


# ── Reset to phone number ────────────────────────────────────────────────────


def test_the_phone_number_becomes_the_password(client, admin_headers, make_member, provider):
    member = make_member(password="forgotten")
    r = client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                    json={"phone": member.phone})
    assert r.status_code == 200
    assert r.json()["temporary_password"] == digits_of(member.phone)


def test_they_can_sign_in_with_it(client, admin_headers, make_member, provider):
    member = make_member(password="forgotten")
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})
    assert login(client, member.phone, digits_of(member.phone)).status_code == 200


def test_the_old_password_stops_working(client, admin_headers, make_member, provider):
    member = make_member(password="forgotten")
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})
    assert login(client, member.phone, "forgotten").status_code == 401


def test_any_outstanding_link_is_voided(client, admin_headers, make_member, provider):
    """Two ways in at once is one more than anyone needs."""
    member = make_member(password="old")
    url = client.post("/api/portal/admin/reset-link", headers=admin_headers,
                      json={"phone": member.phone, "send_email": False}).json()["reset_url"]
    token = url.split("token=")[1]

    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})
    r = client.post("/api/auth/reset-password",
                    json={"token": token, "password": "sneaky-one"})
    assert r.status_code == 400


def test_a_member_with_no_usable_number_is_refused(client, admin_headers, make_member, provider):
    member = make_member(password="old", phone="12")
    r = client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                    json={"email": member.email})
    assert r.status_code == 400


# ── The forced change, with SSO off and on ───────────────────────────────────


def test_signing_in_with_the_temporary_password_forces_a_change(
    client, admin_headers, make_member, provider
):
    member = make_member(password="forgotten")
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})

    body = login(client, member.phone, digits_of(member.phone)).json()
    assert body["needs_password"] is True
    assert body["token"]
    assert body["reason"] == "reset_by_admin"


def test_no_session_is_issued_until_they_choose_one(
    client, admin_headers, make_member, provider
):
    """Otherwise "forced" means "asked nicely", and they can ignore it."""
    member = make_member(password="forgotten")
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})

    r = login(client, member.phone, digits_of(member.phone))
    cookies = [v for k, v in r.headers.raw if k.lower() == b"set-cookie"]
    assert not any(b"member_session=" in c for c in cookies)


def test_the_change_is_enforced_when_sso_is_off(
    client, admin_headers, make_member, provider
):
    provider.turn_off()
    member = make_member(password="forgotten")
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})
    body = login(client, member.phone, digits_of(member.phone)).json()
    assert body["needs_password"] is True


def test_the_change_is_enforced_when_sso_authenticated_them(
    client, admin_headers, make_member, provider
):
    """The check sits after both sign-in paths on purpose. In the local
    branch only, anyone authenticating centrally would walk straight past a
    reset an admin had just forced."""
    member = make_member(password="forgotten")
    temp = digits_of(member.phone)
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})
    # The provider now accepts the temporary password, so SSO is what lets
    # them in rather than the local hash.
    provider.knows(member.phone, temp)

    body = login(client, member.phone, temp).json()
    assert body["needs_password"] is True


def test_the_temporary_password_is_pushed_centrally(
    client, admin_headers, make_member, provider
):
    """So it works with SSO on, not only against the local hash."""
    member = make_member(password="forgotten")
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})
    assert any(m_id == member.id for m_id, _ in provider.pushed)


def test_choosing_a_new_password_clears_the_obligation(
    client, admin_headers, make_member, provider
):
    member = make_member(password="forgotten")
    temp = digits_of(member.phone)
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})
    token = login(client, member.phone, temp).json()["token"]

    client.post("/api/auth/reset-password",
                json={"token": token, "password": "a-real-password"})
    r = login(client, member.phone, "a-real-password")
    assert r.status_code == 200
    assert r.json().get("needs_password") is None


def test_they_cannot_keep_the_phone_number_as_their_password(
    client, admin_headers, make_member, provider
):
    """Otherwise the forced change is theatre."""
    member = make_member(password="forgotten")
    temp = digits_of(member.phone)
    client.post("/api/portal/admin/reset-to-phone", headers=admin_headers,
                json={"phone": member.phone})
    token = login(client, member.phone, temp).json()["token"]

    r = client.post("/api/auth/reset-password", json={"token": token, "password": temp})
    assert r.status_code == 400


# ── Authorisation ────────────────────────────────────────────────────────────


def test_both_tools_refuse_an_unauthenticated_caller(client, make_member):
    member = make_member(password="old")
    for path in ("reset-link", "reset-to-phone"):
        r = client.post(f"/api/portal/admin/{path}", json={"phone": member.phone})
        assert r.status_code in (401, 403), path


def test_both_tools_refuse_a_wrong_key(client, make_member):
    member = make_member(password="old")
    for path in ("reset-link", "reset-to-phone"):
        r = client.post(f"/api/portal/admin/{path}",
                        headers={"X-Portal-Invite-Key": "not-the-key"},
                        json={"phone": member.phone})
        assert r.status_code in (401, 403), path
