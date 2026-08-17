"""Accept RFM single sign-on identities in the members' portal.

rfm-database is the identity provider. This module verifies the tokens it
signs and maps them onto the portal's local `members` row.

The portal's rule is the opposite of church-manager's, deliberately.
church-manager is staff-only, so an identity it doesn't recognise is
refused. The portal is *for* members: anyone the central directory knows
belongs here, so an identity with no local row gets one created and linked
by external_member_id. Refusing them would mean a member who signed in
elsewhere couldn't use their own portal.

Everything is synchronous because the portal's routes are.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any

from jose import JWTError, jwt

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600
_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


def issuer() -> str:
    return (os.environ.get("SSO_ISSUER") or "").rstrip("/")


def jwks_url() -> str:
    explicit = os.environ.get("SSO_JWKS_URL")
    if explicit:
        return explicit
    return f"{issuer()}/.well-known/jwks.json"


def session_url() -> str:
    """Where to exchange the shared cookie for a token."""
    base = os.environ.get("SSO_INTERNAL_URL") or issuer()
    return f"{base.rstrip('/')}/api/v1/auth/session/refresh"


def is_configured() -> bool:
    return bool(issuer())


def is_sso_token(claims: dict) -> bool:
    """Does this token claim to come from our identity provider?

    Read from unverified claims only to choose a verifier — the signature
    check is what decides whether the claim is true.
    """
    if not is_configured():
        return False
    return str(claims.get("iss", "")).rstrip("/") == issuer()


def _fetch_jwks(force: bool = False) -> dict:
    now = time.time()
    if (
        not force
        and _jwks_cache["keys"]
        and (now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS)
    ):
        return _jwks_cache["keys"]

    with urllib.request.urlopen(jwks_url(), timeout=10) as resp:
        jwks = json.loads(resp.read())

    _jwks_cache["keys"] = jwks
    _jwks_cache["fetched_at"] = now
    logger.info("Refreshed SSO JWKS from %s (%d key(s))", jwks_url(), len(jwks.get("keys", [])))
    return jwks


def verify_token(token: str) -> dict:
    """Verify an identity-provider token. Returns claims; raises JWTError."""
    if not is_configured():
        raise JWTError("SSO is not configured")

    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256":
        raise JWTError(f"Unexpected algorithm {header.get('alg')}")
    kid = header.get("kid")
    if not kid:
        raise JWTError("Token header has no 'kid'")

    jwks = _fetch_jwks()
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key:
        jwks = _fetch_jwks(force=True)   # the provider may have rotated
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key:
        raise JWTError(f"No signing key matches kid {kid}")

    claims = jwt.decode(
        token, key, algorithms=["RS256"], issuer=issuer(),
        options={"verify_aud": False},
    )
    if claims.get("type") not in (None, "access"):
        raise JWTError(f"Token is a '{claims.get('type')}', not an access token")
    return claims


def exchange_session_cookie(cookie_header: str) -> dict | None:
    """Trade the shared SSO cookie for tokens.

    Returns the provider's payload (access_token, refresh_token, …) or
    None when there's no valid session — which is the ordinary case for a
    visitor and must not be treated as an error.
    """
    if not is_configured() or not cookie_header:
        return None
    req = urllib.request.Request(
        session_url(),
        data=b"",
        method="POST",
        headers={"Cookie": cookie_header, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read() or b"{}")
            return body.get("data", body)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            logger.info("[sso] session exchange failed: HTTP %s", e.code)
        return None
    except Exception as e:
        logger.warning("[sso] could not reach the identity provider: %s", e)
        return None


def start_session(*, phone: str | None, email: str | None, password: str) -> tuple[bool, list[str]]:
    """Sign in at the identity provider on the member's behalf.

    Returns (succeeded, set_cookie_headers). The cookies must be re-emitted
    on the portal's own response: the provider set them on our server-side
    call, so without that the member is signed in here and nowhere else.

    A failure here is not fatal — the caller falls back to the local
    password, which is what keeps the portal working when SSO is off.
    """
    if not is_configured():
        return False, []

    base = os.environ.get("SSO_INTERNAL_URL") or issuer()
    body = {"password": password}
    if phone:
        body["phone"] = phone
    if email:
        body["email"] = email

    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1/auth/session",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            cookies = resp.headers.get_all("Set-Cookie") or []
            return resp.status == 200, list(cookies)
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403):
            logger.info("[sso] sign-in attempt returned HTTP %s", e.code)
        return False, []
    except Exception as e:
        logger.warning("[sso] could not reach the identity provider: %s", e)
        return False, []


def end_session(cookie_header: str | None) -> list[str]:
    """End the shared session. Returns Set-Cookie headers to re-emit."""
    if not is_configured() or not cookie_header:
        return []
    base = os.environ.get("SSO_INTERNAL_URL") or issuer()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1/auth/session",
        method="DELETE",
        headers={"Cookie": cookie_header},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return list(resp.headers.get_all("Set-Cookie") or [])
    except Exception:
        return []


def resolve_member(claims: dict, db):
    """Map verified claims onto the portal's `members` row.

    Links by external_member_id, then email, then phone. A member the
    portal has never seen is CREATED — the central directory already
    considers them a member of this church, and the portal is the place
    members go. This is the deliberate difference from church-manager,
    which refuses identities it doesn't already know.
    """
    import re

    from models import Member

    external_id = claims.get("member_id")
    email = (claims.get("email") or "").strip().lower() or None
    phone = (claims.get("phone") or "").strip() or None
    name = (claims.get("name") or "").strip()

    member = None
    if external_id:
        member = db.query(Member).filter(Member.external_member_id == external_id).first()

    if member is None and email:
        member = db.query(Member).filter(Member.email.ilike(email)).first()

    if member is None and phone:
        tail = re.sub(r"\D", "", phone)[-9:]
        if tail:
            for candidate in db.query(Member).all():
                if re.sub(r"\D", "", candidate.phone or "")[-9:] == tail:
                    member = candidate
                    break

    if member is None:
        if not name and not phone:
            logger.info("[sso] token has no name or phone — cannot create a member row")
            return None
        member = Member(
            full_name=name or "Member",
            phone=phone or "",
            email=email or "",
            address="",
            external_member_id=external_id,
            external_match_status="matched" if external_id else None,
            is_active=True,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        logger.info("[sso] created portal member %s for central identity %s",
                    member.id, external_id)
        return member

    # Backfill the link so later requests take the direct path.
    changed = False
    if external_id and not member.external_member_id:
        member.external_member_id = external_id
        member.external_match_status = "matched"
        changed = True
    if email and not member.email:
        member.email = email
        changed = True
    if changed:
        db.commit()
    return member
