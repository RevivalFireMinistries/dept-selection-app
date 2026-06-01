"""
Assembly context resolver — multi-tenant.

The portal serves multiple assemblies from one deployment.  Assembly
context is determined per-request from whoever is logged in:

  1. Member session cookie  → member.external_assembly_id
  2. Admin identity cookie  → admin member's external_assembly_id
  3. No session             → no assembly context (use default features)

There is no deployment-level assembly pin.  Every member carries their
own assembly with them.  Feature flags are then fetched from church-manager
for that specific assembly and cached per-assembly-id.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.orm import Session


_FALLBACK = {"id": None, "name": "Revival Fire Ministries", "configured": False}


def resolve_assembly(request: "Request", db: "Session") -> dict:
    """Return  {id, name, configured}  for the current request.

    Reads the signed session cookies that pages.py already sets — no
    extra cookies or headers needed.  Returns the fallback dict when the
    user is not logged in or their member record has no assembly UUID.
    """
    from routers.pages import (
        MEMBER_COOKIE_NAME,
        _verify_member_session,
        get_admin_identity,
    )

    member_id: int | None = None

    # ── 1. Member session ─────────────────────────────────────────────────────
    token = request.cookies.get(MEMBER_COOKIE_NAME)
    if token:
        member_id = _verify_member_session(token)

    # ── 2. Admin identity session (admins are members too) ───────────────────
    if not member_id:
        identity = get_admin_identity(request)
        if identity:
            member_id = identity.get("member_id")

    if not member_id:
        return dict(_FALLBACK)

    # ── 3. Resolve member's assembly ─────────────────────────────────────────
    try:
        from models import Member
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            return dict(_FALLBACK)

        assembly_id = getattr(member, "external_assembly_id", None)
        if not assembly_id:
            return dict(_FALLBACK)

        return {
            "id":         str(assembly_id),
            "name":       "Revival Fire Ministries",   # display name — could be
            "configured": True,                         # enriched later if needed
        }
    except Exception:
        return dict(_FALLBACK)
