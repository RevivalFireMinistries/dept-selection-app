"""Bulk import portal members into Clerk — Phase 5b.

Iterates the `members` table, creating a Clerk user (or linking to an
existing one by email) for each member who has BOTH:
  * a non-empty email address, AND
  * a bcrypt password_hash (i.e. they registered for the portal at
    some point)

Phone-only and never-registered members are skipped — they stay on
the legacy phone+password flow until a follow-up sub-phase handles
username-as-phone signups.

bcrypt password hashes are sent to Clerk via password_digest +
password_hasher='bcrypt'. Clerk re-hashes to argon2id on first
successful login. Members keep their existing password — no resets,
no lockouts.

Mirrors the church-manager admin/clerk-import-users endpoint shape so
operators have ONE pattern to learn.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CLERK_API = "https://api.clerk.com/v1"
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY") or ""


def _split_full_name(full_name: str) -> tuple[str, str]:
    """Split `Full Name` into (first, last) — best-effort.

    Most rows in this DB carry the entire name in one column; for Clerk
    we need first_name + last_name as separate fields. Single-token
    names become first_name only with empty last.
    """
    if not full_name:
        return "Member", ""
    parts = full_name.strip().split()
    if not parts:
        return "Member", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _public_metadata_for(member) -> dict:
    """Build the Clerk public_metadata blob for a portal member.

    Member-only — no apps.church_manager slice. The portal backend's
    per-app gate checks apps.portal.is_member specifically.
    """
    portal: dict = {"is_member": True}
    if member.external_member_id:
        portal["member_id"] = member.external_member_id
    if member.external_assembly_id:
        portal["assembly_id"] = member.external_assembly_id

    apps = {"portal": portal}
    md: dict[str, Any] = {"apps": apps}
    if member.external_member_id:
        md["member_id"] = member.external_member_id
    if member.external_assembly_id:
        md["assembly_id"] = member.external_assembly_id
    # Stash name in public_metadata too — the user.created webhook on
    # rfm-database promotes these onto user.first_name / user.last_name
    # because Clerk's bulk-import + invitation flows don't let us set
    # those directly on user-create. Same trick as the church-manager
    # invite_user path.
    first, last = _split_full_name(member.full_name or "")
    md["first_name"] = first
    md["last_name"] = last
    return md


def _build_clerk_payload(member) -> dict:
    """Build POST /v1/users body for an email-having portal member."""
    first, last = _split_full_name(member.full_name or "")
    payload: dict[str, Any] = {
        "external_id": f"portal-member:{member.id}",
        "email_address": [member.email],
        "first_name": first,
        "last_name": last,
        "password_digest": member.password_hash,
        "password_hasher": "bcrypt",
        "public_metadata": _public_metadata_for(member),
        "skip_password_requirement": False,
        "skip_password_checks": True,
    }
    return payload


def _merge_portal_into_existing_metadata(
    existing_md: dict, member
) -> dict:
    """Take existing public_metadata from a Clerk user (probably staff
    who's also a member) and ensure the apps.portal slice carries this
    member's portal context — without stomping any existing
    apps.church_manager / kingdom_gateway slices.
    """
    md = dict(existing_md or {})
    apps = dict(md.get("apps") or {})
    portal = dict(apps.get("portal") or {})
    portal["is_member"] = True
    if member.external_member_id:
        portal["member_id"] = member.external_member_id
    if member.external_assembly_id:
        portal["assembly_id"] = member.external_assembly_id
    apps["portal"] = portal
    md["apps"] = apps
    if member.external_member_id and not md.get("member_id"):
        md["member_id"] = member.external_member_id
    if member.external_assembly_id and not md.get("assembly_id"):
        md["assembly_id"] = member.external_assembly_id
    return md


async def run_member_import(
    db: Session,
    dry_run: bool = True,
    limit: int | None = None,
    reset_existing: bool = False,
) -> dict:
    """Iterate eligible portal members and import (or link) each.

    Returns a JSON-serialisable summary with counts + per-member outcomes.

    Eligibility filter:
      * is_active = True
      * email present (non-empty)
      * password_hash present (member has registered)
      * clerk_user_id IS NULL unless reset_existing=True
    """
    start = time.monotonic()

    if not CLERK_SECRET_KEY:
        raise RuntimeError("CLERK_SECRET_KEY is not configured on the portal")

    # Lazy-import to avoid circular import at module init.
    from models import Member

    if reset_existing and not dry_run:
        db.execute(
            update(Member)
            .where(Member.clerk_user_id.isnot(None))
            .values(clerk_user_id=None)
        )
        db.commit()
        logger.info("[clerk-member-import] reset_existing: cleared clerk_user_id")

    query = (
        db.query(Member)
        .filter(Member.is_active == True)  # noqa: E712
        .filter(Member.email != "")
        .filter(Member.password_hash.isnot(None))
    )
    if not reset_existing:
        query = query.filter(Member.clerk_user_id.is_(None))
    query = query.order_by(Member.id)
    if limit:
        query = query.limit(limit)
    members = list(query.all())

    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "total_seen": len(members),
        "created": 0,
        "linked": 0,
        "skipped": 0,
        "failed": 0,
        "samples": [],
    }

    if dry_run or not members:
        summary["elapsed_seconds"] = round(time.monotonic() - start, 2)
        return summary

    headers = {"Authorization": f"Bearer {CLERK_SECRET_KEY}"}
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        for m in members:
            try:
                outcome = await _process_one(client, m, db)
            except Exception as e:
                logger.exception(
                    "[clerk-member-import] crashed on member %s", m.id
                )
                outcome = {"status": "failed", "error": str(e)[:200]}

            summary[outcome["status"]] = summary.get(outcome["status"], 0) + 1
            if len(summary["samples"]) < 20:
                summary["samples"].append({
                    "member_id": m.id,
                    "email": m.email,
                    **outcome,
                })

    summary["elapsed_seconds"] = round(time.monotonic() - start, 2)
    return summary


async def _process_one(
    client: httpx.AsyncClient, member, db: Session
) -> dict:
    """Import or link a single member. Returns the per-member outcome."""
    from models import Member  # local

    email = member.email.strip()
    if not email:
        return {"status": "skipped", "reason": "no_email"}
    if not member.password_hash:
        return {"status": "skipped", "reason": "no_password_hash"}

    # 1. Look up by email — if the email already has a Clerk identity
    #    (most likely because they're staff in church-manager), link
    #    only; don't create a duplicate Clerk user.
    lookup = await client.get(
        f"{CLERK_API}/users", params={"email_address": [email]}
    )
    existing_users = lookup.json() if lookup.status_code == 200 else []
    if existing_users:
        existing = existing_users[0]
        clerk_user_id = existing.get("id")
        # Merge apps.portal slice into whatever's there
        merged_md = _merge_portal_into_existing_metadata(
            existing.get("public_metadata") or {}, member
        )
        patch_resp = await client.patch(
            f"{CLERK_API}/users/{clerk_user_id}",
            json={"public_metadata": merged_md},
        )
        if patch_resp.status_code not in (200, 204):
            logger.warning(
                "[clerk-member-import] PATCH metadata failed for member %s "
                "(clerk %s): HTTP %d %s",
                member.id, clerk_user_id, patch_resp.status_code,
                patch_resp.text[:200],
            )
            return {
                "status": "failed",
                "error": f"PATCH {patch_resp.status_code}",
                "clerk_user_id": clerk_user_id,
            }
        member.clerk_user_id = clerk_user_id
        db.commit()
        return {"status": "linked", "clerk_user_id": clerk_user_id}

    # 2. Create new Clerk user with the bcrypt hash preserved.
    payload = _build_clerk_payload(member)
    resp = await client.post(f"{CLERK_API}/users", json=payload)
    if resp.status_code in (200, 201):
        new_user = resp.json()
        clerk_user_id = new_user.get("id")
        member.clerk_user_id = clerk_user_id
        db.commit()
        return {"status": "created", "clerk_user_id": clerk_user_id}

    # Race: if a parallel run just created the user, retry the email lookup.
    if resp.status_code == 422:
        again = await client.get(
            f"{CLERK_API}/users", params={"email_address": [email]}
        )
        again_users = again.json() if again.status_code == 200 else []
        if again_users:
            existing = again_users[0]
            clerk_user_id = existing.get("id")
            member.clerk_user_id = clerk_user_id
            db.commit()
            return {"status": "linked", "clerk_user_id": clerk_user_id, "via": "race-retry"}

    logger.warning(
        "[clerk-member-import] create failed for member %s (%s): HTTP %d %s",
        member.id, email, resp.status_code, resp.text[:200],
    )
    return {
        "status": "failed",
        "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
    }
