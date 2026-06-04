"""Clerk auth router for the portal — Phase 5a entry points.

Provides three endpoints:
  GET  /login-clerk           HTML page that bootstraps the Clerk SDK and
                              either redirects to accounts.<domain>/sign-in
                              or (if already signed in) exchanges the JWT
                              for a portal session cookie + redirects to
                              /dashboard.
  POST /api/auth/clerk-exchange  Receives an Authorization: Bearer <JWT>
                              header, resolves the matching portal Member,
                              sets the legacy session cookie, returns
                              { ok: true, redirect: "/dashboard" } so the
                              client can follow up via window.location.
  GET  /api/auth/me           Unified member identity object regardless of
                              which auth path validated the request.

Pattern lifted from church-manager Phase 3b — same redirect-flow approach
that worked there. Designed so EXISTING phone+password session-cookie
auth keeps working untouched; the Clerk path is purely additive.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Read once at import; templates also receive these so the Clerk SDK can
# bootstrap with the right publishable key + frontend API host.
CLERK_PUBLISHABLE_KEY = os.getenv("CLERK_PUBLISHABLE_KEY") or ""
CLERK_FRONTEND_API = (os.getenv("CLERK_FRONTEND_API") or "").rstrip("/")
CLERK_JWT_TEMPLATE = os.getenv("CLERK_JWT_TEMPLATE") or "rfm"


def _derive_account_portal(frontend_api: str) -> str:
    """Convert the Clerk Frontend API URL → Account Portal URL.

    Dev:  <slug>.clerk.accounts.dev  →  <slug>.accounts.dev
    Prod: clerk.<domain>             →  accounts.<domain>
    """
    if not frontend_api:
        return ""
    return (
        frontend_api.replace(".clerk.accounts.dev", ".accounts.dev")
        .replace("//clerk.", "//accounts.")
        .rstrip("/")
    )


@router.get("/login-clerk", response_class=HTMLResponse, include_in_schema=False)
async def login_clerk_page(request: Request):
    """HTML page that bootstraps Clerk SDK; redirects to sign-in or
    exchanges the existing session for a portal cookie.

    Renders a small static template — the actual flow runs client-side
    in JS so we can wait for the Clerk SDK to detect any handshake URL
    params (e.g. from an invitation ticket) before deciding what to do.
    """
    if not CLERK_PUBLISHABLE_KEY or not CLERK_FRONTEND_API:
        return HTMLResponse(
            content=(
                "<h1>Clerk sign-in not configured</h1>"
                "<p>Set CLERK_PUBLISHABLE_KEY and CLERK_FRONTEND_API on the "
                "portal service.</p><p><a href='/login'>← Back to legacy login</a></p>"
            ),
            status_code=503,
        )

    account_portal = _derive_account_portal(CLERK_FRONTEND_API)
    return templates.TemplateResponse(
        "login_clerk.html",
        {
            "request": request,
            "clerk_publishable_key": CLERK_PUBLISHABLE_KEY,
            "clerk_frontend_api": CLERK_FRONTEND_API,
            "clerk_jwt_template": CLERK_JWT_TEMPLATE,
            "clerk_account_portal": account_portal,
        },
    )


@router.post("/api/auth/clerk-exchange")
async def clerk_exchange(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Exchange a Clerk JWT (Authorization: Bearer ...) for a portal
    session cookie. Returns the URL to redirect to, or 401 if the JWT
    doesn't resolve to a portal member.

    Called by login_clerk.html's JS after it obtains a token from
    Clerk.session.getToken({template: 'rfm'}).
    """
    from clerk_auth import get_clerk_member

    # get_clerk_member reads Authorization header from `request` directly.
    member = await get_clerk_member(request, db)
    if not member:
        raise HTTPException(
            status_code=401,
            detail=(
                "Clerk identity could not be linked to a portal member. "
                "If you're a staff member, ask the SuperAdmin to verify "
                "your apps.portal.member_id is set in Clerk."
            ),
        )

    # Reuse the legacy session helpers — keeps every other route
    # working unchanged (they all read the same cookie).
    from routers.pages import set_member_session

    set_member_session(response, member.id)

    logger.info(
        "[clerk-exchange] member %s (%s) signed in via Clerk",
        member.id, member.email,
    )
    # Portal's home is at /, not /dashboard (that path is church-manager's).
    return {"ok": True, "redirect": "/", "member_id": member.id}


@router.get("/api/auth/me")
async def auth_me(request: Request, db: Session = Depends(get_db)):
    """Unified member identity. Works for both legacy session cookie and
    Clerk JWT — get_current_member already handles both paths.
    """
    from routers.pages import get_current_member

    member = get_current_member(request, db)
    if not member:
        raise HTTPException(401, "Not authenticated")

    has_clerk = bool(getattr(member, "clerk_user_id", None))
    auth_header = (
        request.headers.get("authorization") or request.headers.get("Authorization") or ""
    )
    auth_mode = "clerk" if auth_header.lower().startswith("bearer ") else "legacy"

    return {
        "id": member.id,
        "full_name": member.full_name,
        "email": member.email,
        "phone": member.phone,
        "external_member_id": member.external_member_id,
        "external_assembly_id": member.external_assembly_id,
        "clerk_user_id": getattr(member, "clerk_user_id", None),
        "is_active": member.is_active,
        "auth_mode": auth_mode,
        "has_clerk_identity": has_clerk,
    }
