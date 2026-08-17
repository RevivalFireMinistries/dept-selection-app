"""The portal's half of events.

church-manager owns the data; this router is the member-facing surface. So
what matters here is not what an event *is* — that belongs to
church-manager's tests — but what the portal refuses to let the browser
decide:

  * who you are registering as. Identity comes from the portal's signed
    session cookie, never from the request body.
  * whether the shared secret ever reaches the page. It must not, which is
    why downloads and images are streamed rather than linked.
  * what a member is allowed to see of the directory when they aren't
    signed in.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def manager(monkeypatch):
    """Stand in for church-manager and record what the portal sent it.

    Every call is captured so a test can assert on the identity the portal
    injected, which is the property worth protecting.
    """
    import events_client

    calls: list[dict] = []
    behaviour = {"mode": "ok"}

    class Result:
        def __init__(self, ok=True, data=None, status=None, error=None,
                     unavailable=False):
            self.ok, self.data = ok, data if data is not None else {}
            self.status, self.error, self.unavailable = status, error, unavailable

    def record(name, **kwargs):
        calls.append({"call": name, **kwargs})
        if behaviour["mode"] == "unavailable":
            # No error text, so the portal's own wording is what reaches
            # the member — which is the thing worth asserting on.
            return Result(ok=False, unavailable=True, error=None)
        if behaviour["mode"] == "denied":
            return Result(ok=False, status=403, error="Not an event manager")
        return Result(data={"id": kwargs.get("event_id"), "ok": True})

    monkeypatch.setattr(events_client, "get_event",
                        lambda event_id, ext=None: record("get_event", event_id=event_id, ext=ext))
    monkeypatch.setattr(events_client, "register",
                        lambda event_id, **kw: record("register", event_id=event_id, **kw))
    monkeypatch.setattr(events_client, "registry",
                        lambda event_id, ext, **kw: record("registry", event_id=event_id, ext=ext, **kw))
    monkeypatch.setattr(events_client, "registration_status",
                        lambda event_id, member_id: record("registration_status",
                                                           event_id=event_id, member_id=member_id))

    class Manager:
        @property
        def calls(self):
            return calls

        def last(self, name=None):
            matching = [c for c in calls if name is None or c["call"] == name]
            return matching[-1] if matching else None

        def goes_down(self):
            behaviour["mode"] = "unavailable"

        def denies(self):
            behaviour["mode"] = "denied"

    return Manager()


@pytest.fixture
def signed_in(client, make_member):
    """A member with a session cookie on the client."""
    from routers.pages import MEMBER_COOKIE_NAME, _sign_member_session

    member = make_member(full_name="Signed In Member",
                         external_member_id=str(uuid.uuid4()))
    client.cookies.set(MEMBER_COOKIE_NAME, _sign_member_session(member.id))
    yield member
    client.cookies.clear()


EVENT = "11111111-2222-3333-4444-555555555555"


# ── Identity is never the browser's to choose ────────────────────────────────


def test_a_signed_in_member_registers_as_themselves(client, manager, signed_in):
    client.post(f"/api/events/{EVENT}/register", json={"full_name": "Someone Else"})
    sent = manager.last("register")
    assert sent["full_name"] == signed_in.full_name
    assert sent["external_member_id"] == signed_in.external_member_id


def test_a_signed_in_member_cannot_register_as_another_person(
    client, manager, signed_in
):
    """The payload naming a different central id must be ignored, or one
    member could register anyone in the directory."""
    client.post(f"/api/events/{EVENT}/register",
                json={"external_member_id": str(uuid.uuid4()), "full_name": "Impostor"})
    assert manager.last("register")["external_member_id"] == signed_in.external_member_id


def test_a_signed_in_member_is_never_a_guest(client, manager, signed_in):
    client.post(f"/api/events/{EVENT}/register", json={"is_guest": True})
    assert manager.last("register")["is_guest"] is False


def test_a_visitor_may_claim_a_directory_record(client, manager):
    client.cookies.clear()
    external = str(uuid.uuid4())
    client.post(f"/api/events/{EVENT}/register",
                json={"external_member_id": external, "full_name": "Found Myself"})
    sent = manager.last("register")
    assert sent["external_member_id"] == external
    assert sent["is_guest"] is False


def test_a_visitor_with_no_directory_record_registers_as_a_guest(client, manager):
    client.cookies.clear()
    client.post(f"/api/events/{EVENT}/register", json={"full_name": "Passing Visitor"})
    sent = manager.last("register")
    assert sent["is_guest"] is True
    assert sent["external_member_id"] is None


def test_a_visitor_must_give_a_name(client, manager):
    client.cookies.clear()
    r = client.post(f"/api/events/{EVENT}/register", json={})
    assert r.status_code == 400


def test_registration_records_where_it_came_from(client, manager, signed_in):
    client.post(f"/api/events/{EVENT}/register", json={})
    assert manager.last("register")["source"] == "SELF_PORTAL"


# ── Manager actions carry the manager's own identity ─────────────────────────


def test_a_manager_adding_someone_is_recorded_as_the_registrar(
    client, manager, signed_in
):
    """Who registered whom is the audit trail the registry depends on."""
    client.post(f"/api/events/{EVENT}/registry/add", json={"full_name": "Walk-in Guest"})
    sent = manager.last("register")
    assert sent["registered_by_member_id"] == signed_in.external_member_id
    assert sent["registered_by_name"] == signed_in.full_name
    assert sent["source"] == "EVENT_MANAGER"


def test_a_manager_added_person_keeps_their_own_name(client, manager, signed_in):
    client.post(f"/api/events/{EVENT}/registry/add", json={"full_name": "Walk-in Guest"})
    assert manager.last("register")["full_name"] == "Walk-in Guest"


def test_adding_someone_without_a_name_is_refused(client, manager, signed_in):
    assert client.post(f"/api/events/{EVENT}/registry/add",
                       json={}).status_code == 400


def test_the_registry_is_fetched_as_the_signed_in_manager(
    client, manager, signed_in
):
    """church-manager decides whether they may see it, using the identity
    the portal supplies — so supplying the right one is the whole job."""
    client.get(f"/api/events/{EVENT}/registry")
    assert manager.last("registry")["ext"] == signed_in.external_member_id


def test_a_visitor_cannot_reach_the_registry(client, manager):
    client.cookies.clear()
    assert client.get(f"/api/events/{EVENT}/registry").status_code == 401


def test_a_visitor_cannot_add_people_to_the_registry(client, manager):
    client.cookies.clear()
    r = client.post(f"/api/events/{EVENT}/registry/add", json={"full_name": "X"})
    assert r.status_code == 401


def test_a_member_not_linked_centrally_cannot_manage(client, manager, make_member):
    """Without a central id there is no identity to authorise against."""
    from routers.pages import MEMBER_COOKIE_NAME, _sign_member_session

    member = make_member(external_member_id=None)
    client.cookies.set(MEMBER_COOKIE_NAME, _sign_member_session(member.id))
    assert client.get(f"/api/events/{EVENT}/registry").status_code == 401
    client.cookies.clear()


def test_a_refusal_from_church_manager_is_passed_through(client, manager, signed_in):
    manager.denies()
    assert client.get(f"/api/events/{EVENT}/registry").status_code == 403


# ── Guest directory search shows enough to recognise, not to harvest ─────────


@pytest.fixture
def directory(monkeypatch):
    import rfm_api_client as _rfm

    class Result:
        ok = True
        data = [{
            "id": str(uuid.uuid4()),
            "first_name": "Thandi",
            "last_name": "Mokoena",
            "phone": "+27821234567",
            "email": "thandi@example.com",
            "physical_address": "12 Long Street",
        }]

    monkeypatch.setattr(_rfm, "search_members", lambda **kw: Result())
    return Result


def test_a_visitor_can_find_themselves_by_name(client, directory):
    client.cookies.clear()
    results = client.get(f"/api/events/{EVENT}/directory-search",
                         params={"q": "Thandi"}).json()["results"]
    assert results[0]["full_name"] == "Thandi Mokoena"


def test_the_phone_number_is_masked(client, directory):
    """Enough to recognise your own number, not enough to read it off."""
    results = client.get(f"/api/events/{EVENT}/directory-search",
                         params={"q": "Thandi"}).json()["results"]
    hint = results[0]["phone_hint"]
    assert "…" in hint
    assert "+27821234567" not in hint


def test_no_email_or_address_is_returned(client, directory):
    results = client.get(f"/api/events/{EVENT}/directory-search",
                         params={"q": "Thandi"}).json()["results"]
    assert set(results[0]) == {"external_member_id", "full_name", "phone_hint"}


def test_a_one_character_search_is_refused(client, directory):
    """Otherwise the endpoint enumerates the directory a letter at a time."""
    r = client.get(f"/api/events/{EVENT}/directory-search", params={"q": "T"})
    assert r.status_code == 422


def test_an_unavailable_directory_returns_no_results_rather_than_an_error(
    client, monkeypatch
):
    import rfm_api_client as _rfm

    class Down:
        ok = False
        data = None

    monkeypatch.setattr(_rfm, "search_members", lambda **kw: Down())
    r = client.get(f"/api/events/{EVENT}/directory-search", params={"q": "Thandi"})
    assert r.status_code == 200
    assert r.json()["results"] == []


# ── Already-registered check ─────────────────────────────────────────────────


def test_not_knowing_whether_someone_is_registered_is_not_an_error(
    client, manager, monkeypatch
):
    """The register call is the authority; this is only a courtesy check, so
    a failure here must not block the form."""
    manager.goes_down()
    r = client.get(f"/api/events/{EVENT}/registration-status",
                   params={"member_id": str(uuid.uuid4())})
    assert r.status_code == 200
    assert r.json()["registered"] is False


# ── Outage handling ──────────────────────────────────────────────────────────


def test_an_events_outage_is_reported_as_temporary(client, manager, signed_in):
    manager.goes_down()
    r = client.get(f"/api/events/{EVENT}")
    assert r.status_code == 503
    assert "try again" in r.json()["detail"].lower()


def test_the_event_page_says_whether_you_are_signed_in(client, manager, signed_in):
    body = client.get(f"/api/events/{EVENT}").json()
    assert body["signed_in"] is True
    assert body["me"]["full_name"] == signed_in.full_name


def test_a_visitor_gets_no_me_block(client, manager):
    client.cookies.clear()
    body = client.get(f"/api/events/{EVENT}").json()
    assert body["signed_in"] is False
    assert "me" not in body
