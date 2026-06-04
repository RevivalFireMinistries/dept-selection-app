"""Clerk JWT validation + member resolution for the portal.

Phase 5a of AUTH_CENTRALIZATION (see church-manager/docs/...). Adds a
parallel Clerk-based auth path alongside the existing phone+password
session cookie. Members can sign in via EITHER path; the existing
auth code is unchanged.

Six pieces, mirroring the church-manager pattern:
  * verify_clerk_token  — RS256 JWT signature check via cached JWKS
  * get_clerk_member    — JWT → portal Member, with lazy linkage
  * Per-app gate enforced inside get_clerk_member: token must carry
    `apps.portal.is_member = true` to access the portal. Staff-only
    or KG-only JWTs get a 403, never a misrouted session.

Environment variables (Railway → portal service → Variables):
  CLERK_PUBLISHABLE_KEY    pk_live_... or pk_test_...   (also used by FE)
  CLERK_SECRET_KEY         sk_live_... or sk_test_...   (backend only)
  CLERK_FRONTEND_API       https://clerk.<your-domain>  (JWKS host)
  CLERK_JWT_TEMPLATE       defaults to 'rfm'

When any of these is unset, the Clerk path silently disables; legacy
session cookie auth continues to work as before.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from fastapi import Request
from jose import jwt
from jose.exceptions import JWTError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config — read from env at import time. Falls through to legacy auth when
# any of these is missing.
# ---------------------------------------------------------------------------
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY") or ""
CLERK_FRONTEND_API = (os.getenv("CLERK_FRONTEND_API") or "").rstrip("/")
CLERK_JWT_TEMPLATE = os.getenv("CLERK_JWT_TEMPLATE") or "rfm"
CLERK_API = "https://api.clerk.com/v1"


def is_clerk_configured() -> bool:
    return bool(CLERK_FRONTEND_API)


# ---------------------------------------------------------------------------
# JWKS cache. 1-hour TTL matches what Clerk recommends; refreshes on
# unknown kid (handles Clerk key rotation transparently).
# ---------------------------------------------------------------------------
_JWKS_TTL_SECONDS = 3600
_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


async def _fetch_jwks(force: bool = False) -> dict:
    now = time.time()
    if (
        not force
        and _jwks_cache["keys"]
        and (now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS)
    ):
        return _jwks_cache["keys"]

    if not CLERK_FRONTEND_API:
        raise RuntimeError("CLERK_FRONTEND_API not configured")

    url = CLERK_FRONTEND_API + "/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        jwks = resp.json()

    _jwks_cache["keys"] = jwks
    _jwks_cache["fetched_at"] = now
    logger.info("Refreshed Clerk JWKS from %s (%d keys)", url, len(jwks.get("keys", [])))
    return jwks


async def verify_clerk_token(token: str) -> dict:
    """Validate a Clerk RS256 JWT. Returns claims dict; raises JWTError."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise JWTError(f"Malformed token header: {e}") from e

    if header.get("alg") != "RS256":
        raise JWTError(f"Unexpected alg {header.get('alg')!r}")

    kid = header.get("kid")
    if not kid:
        raise JWTError("Token header missing 'kid'")

    jwks = await _fetch_jwks()
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key:
        # Maybe Clerk rotated — force a refresh once.
        jwks = await _fetch_jwks(force=True)
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            raise JWTError(f"No JWK matches kid {kid!r}")

    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        issuer=CLERK_FRONTEND_API,
        options={"verify_aud": False},
    )


# ---------------------------------------------------------------------------
# Clerk Backend API helpers (member resolution fallback)
# ---------------------------------------------------------------------------


async def fetch_clerk_user(clerk_user_id: str) -> dict | None:
    """Fetch a user from Clerk Backend API. Used for email-based
    lazy-link when the local row doesn't yet have clerk_user_id."""
    if not CLERK_SECRET_KEY:
        return None
    headers = {"Authorization": f"Bearer {CLERK_SECRET_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            resp = await client.get(f"{CLERK_API}/users/{clerk_user_id}")
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        logger.exception("Clerk fetch_user failed for %s", clerk_user_id)
    return None


def _primary_email(clerk_user: dict) -> str | None:
    primary_id = clerk_user.get("primary_email_address_id")
    for entry in clerk_user.get("email_addresses") or []:
        if entry.get("id") == primary_id:
            return entry.get("email_address")
    addrs = clerk_user.get("email_addresses") or []
    if addrs:
        return addrs[0].get("email_address")
    return None


# ---------------------------------------------------------------------------
# Portal-side member resolution
# ---------------------------------------------------------------------------


async def get_clerk_member(request: Request, db: Session):
    """If the request carries a valid Clerk Bearer JWT with portal access,
    return the matching portal Member row. Otherwise return None — callers
    fall through to the legacy session-cookie path.

    Lookup precedence:
      1. Local members.clerk_user_id == JWT.sub
      2. Local members.external_member_id == JWT.apps.portal.member_id
         (this is how a STAFF user with a populated apps.portal.member_id
         finds their portal row even if it doesn't yet have clerk_user_id)
      3. Auto-link by email: fetch Clerk user, match by email against
         members.email, stamp clerk_user_id onto the row.
      4. Auto-create a stub portal Member row using JWT claims, if the
         JWT carries enough info (apps.portal.member_id +
         apps.portal.is_member=true). Skipped otherwise — returns None.

    Per-app gate: token must carry apps.portal.is_member=true. A staff
    user whose JWT has only apps.church_manager (no portal slice) gets
    None here, falls through, and is treated as unauthenticated —
    correct behaviour, they shouldn't access the portal.
    """
    if not is_clerk_configured():
        return None

    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()

    # Skip non-RS256 quickly — legacy session cookie path handles those.
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            return None
    except JWTError:
        return None

    try:
        claims = await verify_clerk_token(token)
    except JWTError as e:
        logger.info("[portal-clerk] token rejected: %s", e)
        return None

    # Per-app gate
    apps = claims.get("apps") or {}
    portal_slice = apps.get("portal") if isinstance(apps, dict) else None
    if not isinstance(portal_slice, dict) or not portal_slice.get("is_member"):
        logger.info(
            "[portal-clerk] token rejected: apps.portal.is_member not set "
            "for clerk user %s",
            claims.get("sub"),
        )
        return None

    # Lazy-import to avoid circular imports at module init
    from models import Member

    clerk_user_id = claims.get("sub")

    # 1. By clerk_user_id
    member = (
        db.query(Member).filter(Member.clerk_user_id == clerk_user_id).first()
        if hasattr(Member, "clerk_user_id")
        else None
    )
    if member and member.is_active:
        return member

    # 2. By external_member_id == apps.portal.member_id
    portal_member_id = portal_slice.get("member_id") or claims.get("member_id")
    if portal_member_id:
        member = (
            db.query(Member)
            .filter(Member.external_member_id == str(portal_member_id))
            .first()
        )
        if member:
            if member.is_active:
                # Stamp clerk_user_id for next time
                if hasattr(Member, "clerk_user_id") and not member.clerk_user_id:
                    member.clerk_user_id = clerk_user_id
                    db.commit()
                    logger.info(
                        "[portal-clerk] linked portal member %s to clerk user %s "
                        "via apps.portal.member_id",
                        member.id, clerk_user_id,
                    )
                return member

    # 3. By email (fetch from Clerk API)
    clerk_user = await fetch_clerk_user(clerk_user_id)
    if clerk_user:
        email = _primary_email(clerk_user)
        if email:
            member = (
                db.query(Member)
                .filter(Member.email == email, Member.is_active == True)  # noqa: E712
                .first()
            )
            if member:
                if hasattr(Member, "clerk_user_id"):
                    member.clerk_user_id = clerk_user_id
                    if not member.external_member_id and portal_member_id:
                        member.external_member_id = str(portal_member_id)
                    db.commit()
                logger.info(
                    "[portal-clerk] linked portal member %s to clerk user %s "
                    "via email match (%s)",
                    member.id, clerk_user_id, email,
                )
                return member

    # 4. Auto-create stub portal member from JWT claims (covers staff
    #    who don't yet have a portal row at all). Requires enough info
    #    in the JWT to construct a valid Member row.
    if portal_member_id and clerk_user:
        email = _primary_email(clerk_user) or ""
        first_name = clerk_user.get("first_name") or claims.get("first_name") or ""
        last_name = clerk_user.get("last_name") or claims.get("last_name") or ""
        full_name = f"{first_name} {last_name}".strip() or email or "Member"
        phone = claims.get("phone") or ""
        assembly_id = (
            (portal_slice.get("assembly_id"))
            or claims.get("assembly_id")
            or ""
        )

        # Members.phone has NOT NULL — keep a placeholder if Clerk JWT
        # doesn't carry one (staff users may have null phone).
        if not phone:
            phone = ""

        try:
            new_member = Member(
                full_name=full_name,
                phone=phone,
                email=email,
                address="",
                external_member_id=str(portal_member_id),
                external_match_status="manual",
                external_assembly_id=str(assembly_id) if assembly_id else None,
                is_active=True,
            )
            if hasattr(Member, "clerk_user_id"):
                new_member.clerk_user_id = clerk_user_id
            db.add(new_member)
            db.commit()
            db.refresh(new_member)
            logger.info(
                "[portal-clerk] auto-created portal member %s for clerk user %s "
                "(%s, external_member_id=%s)",
                new_member.id, clerk_user_id, email, portal_member_id,
            )
            return new_member
        except Exception:
            db.rollback()
            logger.exception(
                "[portal-clerk] auto-create stub member failed for %s", email
            )

    logger.info(
        "[portal-clerk] no portal member match for clerk user %s",
        clerk_user_id,
    )
    return None
