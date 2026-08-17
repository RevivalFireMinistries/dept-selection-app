"""Verification of RFM single sign-on tokens in the portal.

The provider signs with RS256 and publishes the public half at a JWKS
endpoint. These tests generate their own keypair and serve a JWKS from
memory, so nothing here needs rfm-database running.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt
from jose.backends import RSAKey

ISSUER = "https://identity.test"


@pytest.fixture
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def jwks_for(public_pem: str, kid: str) -> dict:
    jwk = RSAKey(public_pem, "RS256").to_dict()
    jwk = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in jwk.items()}
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


@pytest.fixture
def sso(monkeypatch, keypair):
    """sso_auth pointed at an in-memory provider.

    Returns a small harness: `sso.sign(...)` mints a token, `sso.serve(...)`
    swaps out what the JWKS endpoint returns, and `sso.fetches` counts how
    often it was called — which is how the caching tests observe behaviour
    rather than implementation.
    """
    import urllib.request

    import sso_auth

    private_pem, public_pem = keypair
    monkeypatch.setenv("SSO_ISSUER", ISSUER)
    monkeypatch.setenv("SSO_JWKS_URL", f"{ISSUER}/.well-known/jwks.json")

    # A cache that survived from another test would mask a real failure.
    sso_auth._jwks_cache["keys"] = None
    sso_auth._jwks_cache["fetched_at"] = 0.0

    state = {"jwks": jwks_for(public_pem, "test-kid"), "fetches": 0}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, *args, **kwargs):
        state["fetches"] += 1
        return FakeResponse(state["jwks"])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    class Harness:
        module = sso_auth

        @staticmethod
        def sign(kid="test-kid", **claims):
            payload = {
                "sub": str(uuid.uuid4()),
                "iss": ISSUER,
                "exp": int(time.time()) + 300,
                **claims,
            }
            return jwt.encode(payload, private_pem, algorithm="RS256",
                              headers={"kid": kid})

        @staticmethod
        def serve(jwks):
            state["jwks"] = jwks

        @staticmethod
        def rotate_to(new_kid):
            state["jwks"] = jwks_for(public_pem, new_kid)

        @property
        def fetches(self):
            return state["fetches"]

    yield Harness()

    sso_auth._jwks_cache["keys"] = None
    sso_auth._jwks_cache["fetched_at"] = 0.0


# ── Configuration ────────────────────────────────────────────────────────────


def test_sso_is_off_when_no_issuer_is_set(monkeypatch):
    import sso_auth

    monkeypatch.delenv("SSO_ISSUER", raising=False)
    assert sso_auth.is_configured() is False


def test_verifying_a_token_while_sso_is_off_is_refused(monkeypatch):
    import sso_auth

    monkeypatch.delenv("SSO_ISSUER", raising=False)
    with pytest.raises(JWTError):
        sso_auth.verify_token("anything")


def test_jwks_url_defaults_to_the_issuer(monkeypatch):
    import sso_auth

    monkeypatch.setenv("SSO_ISSUER", ISSUER)
    monkeypatch.delenv("SSO_JWKS_URL", raising=False)
    assert sso_auth.jwks_url() == f"{ISSUER}/.well-known/jwks.json"


def test_a_trailing_slash_on_the_issuer_does_not_change_identity(monkeypatch):
    import sso_auth

    monkeypatch.setenv("SSO_ISSUER", ISSUER + "/")
    assert sso_auth.is_sso_token({"iss": ISSUER}) is True


# ── Verification ─────────────────────────────────────────────────────────────


def test_a_valid_token_verifies(sso):
    claims = sso.module.verify_token(sso.sign(name="Thandi"))
    assert claims["name"] == "Thandi"
    assert claims["iss"] == ISSUER


def test_a_token_from_another_issuer_is_rejected(sso):
    with pytest.raises(JWTError):
        sso.module.verify_token(sso.sign(iss="https://attacker.test"))


def test_an_expired_token_is_rejected(sso):
    with pytest.raises(JWTError):
        sso.module.verify_token(sso.sign(exp=int(time.time()) - 60))


def test_a_refresh_token_is_not_accepted_as_an_access_token(sso):
    """Refresh tokens are signed by the same key and would otherwise pass."""
    with pytest.raises(JWTError):
        sso.module.verify_token(sso.sign(type="refresh"))


def test_an_hs256_token_is_rejected_however_it_is_signed(sso):
    """The algorithm is pinned, so the confusion attack has nowhere to go."""
    forged = jwt.encode({"sub": "x", "iss": ISSUER}, "secret", algorithm="HS256")
    with pytest.raises(JWTError):
        sso.module.verify_token(forged)


def test_a_token_with_no_kid_is_rejected(sso, keypair):
    """RS256 but unkidded — so this reaches the kid check rather than
    stopping at the algorithm check the way an HS256 token would."""
    private_pem, _ = keypair
    unkidded = jwt.encode({"sub": "x", "iss": ISSUER, "exp": int(time.time()) + 60},
                          private_pem, algorithm="RS256")
    assert jwt.get_unverified_header(unkidded).get("alg") == "RS256"
    with pytest.raises(JWTError):
        sso.module.verify_token(unkidded)


def test_a_token_signed_by_an_unknown_key_is_rejected(sso, keypair):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    forged = jwt.encode({"sub": "x", "iss": ISSUER, "exp": int(time.time()) + 60},
                        other_pem, algorithm="RS256", headers={"kid": "test-kid"})
    with pytest.raises(JWTError):
        sso.module.verify_token(forged)


# ── Key handling ─────────────────────────────────────────────────────────────


def test_the_key_set_is_cached_rather_than_fetched_per_request(sso):
    for _ in range(5):
        sso.module.verify_token(sso.sign())
    assert sso.fetches == 1


def test_an_unrecognised_kid_triggers_one_refetch(sso):
    """The provider may have rotated since we cached; look again before failing."""
    sso.module.verify_token(sso.sign())
    before = sso.fetches
    sso.rotate_to("rotated-kid")
    sso.module.verify_token(sso.sign(kid="rotated-kid"))
    assert sso.fetches == before + 1


def test_an_unknown_kid_that_survives_a_refetch_is_rejected(sso):
    with pytest.raises(JWTError):
        sso.module.verify_token(sso.sign(kid="never-existed"))


# ── Session exchange ─────────────────────────────────────────────────────────


def test_no_cookie_means_no_session_rather_than_an_error(sso):
    assert sso.module.exchange_session_cookie("") is None


def test_an_unreachable_provider_yields_no_session_rather_than_raising(sso, monkeypatch):
    """A visitor with no session and a provider that is down look the same
    to the caller on purpose — neither is an error the page should show."""
    import urllib.request

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert sso.module.exchange_session_cookie("rfm_sso=abc") is None


def test_sign_in_failure_is_reported_not_raised(sso, monkeypatch):
    import urllib.error
    import urllib.request

    def unauthorised(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", unauthorised)
    ok, cookies = sso.module.start_session(phone="0721234567", email=None,
                                           password="wrong")
    assert ok is False and cookies == []


def test_sign_in_while_sso_is_off_reports_failure_quietly(monkeypatch):
    import sso_auth

    monkeypatch.delenv("SSO_ISSUER", raising=False)
    assert sso_auth.start_session(phone="0721234567", email=None, password="x") == (False, [])


# ── Mapping an identity onto a portal member ─────────────────────────────────


def test_an_identity_links_by_external_member_id(sso, db, make_member):
    external = str(uuid.uuid4())
    member = make_member(external_member_id=external, full_name="Linked Member")
    resolved = sso.module.resolve_member(
        {"member_id": external, "name": "Different Name"}, db
    )
    assert resolved.id == member.id


def test_an_identity_links_by_email_when_the_id_is_unknown(sso, db, make_member):
    member = make_member(email="Mixed.Case@portal.example.com")
    resolved = sso.module.resolve_member({"email": "mixed.case@portal.example.com"}, db)
    assert resolved.id == member.id


def test_an_identity_links_by_phone_across_formats(sso, db, make_member):
    """+27 72… and 072… are the same number and must resolve to one member."""
    member = make_member(phone="0721234567", email="")
    resolved = sso.module.resolve_member({"phone": "+27 72 123 4567"}, db)
    assert resolved.id == member.id


def test_linking_backfills_the_central_id_for_next_time(sso, db, make_member):
    member = make_member(email="backfill@portal.example.com")
    external = str(uuid.uuid4())
    sso.module.resolve_member(
        {"member_id": external, "email": "backfill@portal.example.com"}, db
    )
    db.refresh(member)
    assert member.external_member_id == external
    assert member.external_match_status == "matched"


def test_backfilling_never_overwrites_an_email_the_portal_already_holds(sso, db, make_member):
    member = make_member(email="original@portal.example.com")
    sso.module.resolve_member(
        {"member_id": member.external_member_id or str(uuid.uuid4()),
         "email": "original@portal.example.com", "phone": member.phone}, db
    )
    db.refresh(member)
    assert member.email == "original@portal.example.com"


def test_an_unknown_identity_is_created_because_the_portal_is_for_members(sso, db):
    """The opposite of church-manager's rule, and deliberately so."""
    from models import Member

    external = str(uuid.uuid4())
    phone = "0799" + str(uuid.uuid4().int)[:6]
    created = sso.module.resolve_member(
        {"member_id": external, "name": "Brand New", "phone": phone}, db
    )
    try:
        assert created is not None
        assert created.full_name == "Brand New"
        assert created.external_member_id == external
    finally:
        db.query(Member).filter(Member.id == created.id).delete()
        db.commit()


def test_an_identity_with_nothing_to_identify_it_is_not_created(sso, db):
    assert sso.module.resolve_member({"member_id": str(uuid.uuid4())}, db) is None
