"""The one-off credential export, run as an admin endpoint.

It exists as an endpoint rather than a script because `railway run`
executes on the operator's laptop, which cannot reach Railway's internal
hostnames — see docs/SSO_MIGRATION_RUNBOOK.md. That makes its safety
properties worth testing: it defaults to changing nothing, it refuses
anonymous callers, and it never sends a credential it shouldn't.
"""
from __future__ import annotations

import json
import uuid

import pytest

ENDPOINT = "/api/admin/sso/export-identities"


@pytest.fixture
def admin(client, make_member):
    """An authenticated admin session.

    Built with the app's own cookie signer rather than by driving the login
    form: admin auth needs both the session cookie and a signed identity,
    and forging either by hand would be a test that passes for the wrong
    reason if the signing scheme changed.
    """
    from routers.pages import (ADMIN_COOKIE_NAME, ADMIN_IDENTITY_COOKIE,
                               _sign_admin_identity)

    member = make_member(full_name="Test Admin")
    client.cookies.set(ADMIN_COOKIE_NAME, "authenticated")
    client.cookies.set(ADMIN_IDENTITY_COOKIE,
                       _sign_admin_identity(member.id, member.full_name))
    yield member
    client.cookies.clear()


@pytest.fixture
def provider(monkeypatch):
    """Intercept the two calls the export makes to rfm-database."""
    import urllib.request

    sent = {"identities": None, "url": None}
    behaviour = {"mode": "ok"}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/service-token"):
            if behaviour["mode"] == "auth_fails":
                raise OSError("connection refused")
            return FakeResponse({"data": {"access_token": "service-token"}})
        sent["url"] = url
        sent["identities"] = json.loads(req.data.decode())["identities"]
        if behaviour["mode"] == "rejected":
            import urllib.error
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return FakeResponse({"data": {"created": len(sent["identities"]),
                                      "linked": 0, "skipped": 0, "collisions": []}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("SSO_INTERNAL_URL", "http://identity.test")
    monkeypatch.setenv("RFM_API_KEY", "rfm_testkey")

    class Provider:
        @property
        def sent(self):
            return sent

        def rejects(self):
            behaviour["mode"] = "rejected"

        def cannot_authenticate(self):
            behaviour["mode"] = "auth_fails"

    return Provider()


# ── Authorisation ────────────────────────────────────────────────────────────


def test_an_anonymous_caller_is_refused(client):
    client.cookies.clear()
    assert client.post(ENDPOINT).status_code in (401, 403)


def test_an_administrator_may_run_it(client, admin):
    assert client.post(ENDPOINT).status_code == 200


# ── Safe by default ──────────────────────────────────────────────────────────


def test_it_defaults_to_changing_nothing(client, admin):
    """A forgotten query parameter must not perform the migration."""
    body = client.post(ENDPOINT).json()
    assert body["dry_run"] is True
    assert "would_send" in body


def test_a_dry_run_never_contacts_the_provider(client, admin, provider):
    client.post(ENDPOINT, params={"dry_run": "true"})
    assert provider.sent["identities"] is None


def test_a_dry_run_names_who_would_be_sent(client, admin, make_member):
    member = make_member(password="pw", external_member_id=str(uuid.uuid4()),
                         full_name="Dry Run Member")
    listed = client.post(ENDPOINT, params={"dry_run": "true"}).json()["would_send"]
    assert any(e["name"] == "Dry Run Member" for e in listed)


# ── Who is eligible ──────────────────────────────────────────────────────────


def test_a_member_with_no_password_is_skipped_not_sent(client, admin, make_member):
    make_member(password=None, external_member_id=str(uuid.uuid4()))
    body = client.post(ENDPOINT).json()
    assert body["skipped"]["no_password"] >= 1


def test_a_member_not_linked_to_the_directory_is_skipped(client, admin, make_member):
    """There is no person record to attach a login to, and inventing one
    would create the duplicate identity this migration exists to remove."""
    make_member(password="pw", external_member_id=None)
    body = client.post(ENDPOINT).json()
    assert body["skipped"]["not_linked"] >= 1


def test_an_inactive_member_is_skipped(client, admin, make_member):
    make_member(password="pw", external_member_id=str(uuid.uuid4()), is_active=False)
    body = client.post(ENDPOINT).json()
    assert body["skipped"]["inactive"] >= 1


def test_an_eligible_member_is_counted(client, admin, make_member):
    before = client.post(ENDPOINT).json()["eligible"]
    make_member(password="pw", external_member_id=str(uuid.uuid4()))
    assert client.post(ENDPOINT).json()["eligible"] == before + 1


# ── What actually goes over the wire ─────────────────────────────────────────


def test_the_hash_is_sent_verbatim_so_nobody_resets_a_password(
    client, admin, provider, db, make_member
):
    from models import Member

    member = make_member(password="known-password",
                         external_member_id=str(uuid.uuid4()))
    stored = db.query(Member).filter(Member.id == member.id).first().password_hash

    client.post(ENDPOINT, params={"dry_run": "false"})
    mine = [i for i in provider.sent["identities"]
            if i["external_member_id"] == member.external_member_id]
    assert mine and mine[0]["password_hash"] == stored


def test_every_identity_is_sent_as_a_member(client, admin, provider, make_member):
    make_member(password="pw", external_member_id=str(uuid.uuid4()))
    client.post(ENDPOINT, params={"dry_run": "false"})
    assert {i["role"] for i in provider.sent["identities"]} == {"MEMBER"}


def test_it_posts_to_the_member_scoped_endpoint_not_the_role_setting_one(
    client, admin, provider, make_member
):
    """The portal's API key authenticates as ADMINISTRATOR. Only
    /member-credentials accepts that, and it forces role=MEMBER — so a
    leaked portal key cannot mint an administrator centrally."""
    make_member(password="pw", external_member_id=str(uuid.uuid4()))
    client.post(ENDPOINT, params={"dry_run": "false"})
    assert provider.sent["url"].endswith("/api/v1/auth/member-credentials")


# ── Failure is reported, not raised ──────────────────────────────────────────


def test_a_rejected_import_is_reported_with_its_status(
    client, admin, provider, make_member
):
    make_member(password="pw", external_member_id=str(uuid.uuid4()))
    provider.rejects()
    body = client.post(ENDPOINT, params={"dry_run": "false"}).json()
    assert "403" in body["error"]


def test_a_provider_that_cannot_be_reached_is_reported(
    client, admin, provider, make_member
):
    make_member(password="pw", external_member_id=str(uuid.uuid4()))
    provider.cannot_authenticate()
    body = client.post(ENDPOINT, params={"dry_run": "false"}).json()
    assert "authenticate" in body["error"].lower()


def test_missing_configuration_is_reported_rather_than_crashing(
    client, admin, monkeypatch, make_member
):
    make_member(password="pw", external_member_id=str(uuid.uuid4()))
    monkeypatch.delenv("SSO_INTERNAL_URL", raising=False)
    monkeypatch.delenv("RFM_API_URL", raising=False)
    body = client.post(ENDPOINT, params={"dry_run": "false"}).json()
    assert "not configured" in body["error"]
