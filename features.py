"""
Assembly feature flags.

Each key maps to a toggleable feature. Flags are stored in the Settings
table as  feature_<key> = "true" | "false"  and fall back to the defaults
defined here when no row exists.

Usage
-----
    from features import get_features
    flags = get_features(db)        # dict[str, bool]
    if flags["kg"]:
        ...

Middleware (main.py) calls get_features(db) on every request and stores
the result in request.state.features.  The RFMTemplates subclass in
routers/pages.py auto-injects it into every TemplateResponse context,
so templates can always reference  {{ features.kg }},  {{ features.giving }},
etc. without any individual route handler needing to pass it.

Admin can toggle flags via  PUT /api/admin/features  or the
/admin/features  page.  Call invalidate_cache() after any write.
"""

from __future__ import annotations
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# ── Canonical feature list ────────────────────────────────────────────────────
# "default" is what the app behaves like when no Settings row exists for the key.
# "label"   is shown in the admin Features UI.
# "group"   is used to organise the toggle grid.

FEATURES: dict[str, dict] = {
    # ── Core member workflow ───────────────────────────────────────────────────
    "department_selection": {
        "label": "Department Selection",
        "group": "Core",
        "default": True,
        "description": "Members choose departments to serve in; admin approves/rejects.",
    },
    "meetings": {
        "label": "Meetings",
        "group": "Core",
        "default": True,
        "description": "Department and leadership meetings with RSVP.",
    },
    "connect_form": {
        "label": "Connect Card",
        "group": "Core",
        "default": True,
        "description": "Public visitor registration / first-time connect form.",
    },
    # ── Service & media ───────────────────────────────────────────────────────
    "programs": {
        "label": "Service Programs",
        "group": "Service & Media",
        "default": True,
        "description": "Order-of-service programs, templates, participant scheduling.",
    },
    "projector": {
        "label": "Projector / Display",
        "group": "Service & Media",
        "default": True,
        "description": "Members submit media, verses, and songs for the projector.",
    },
    "songs": {
        "label": "Song List",
        "group": "Service & Media",
        "default": True,
        "description": "Song catalog and worship set-list submission.",
    },
    "poster_requests": {
        "label": "Poster / Design Requests",
        "group": "Service & Media",
        "default": True,
        "description": "Members request event posters from the design team.",
    },
    # ── Content & discipleship ────────────────────────────────────────────────
    "devotionals": {
        "label": "Devotionals",
        "group": "Content & Discipleship",
        "default": True,
        "description": "Daily devotionals authored by leaders, shown on the portal home.",
    },
    "reading_plans": {
        "label": "Reading Plans",
        "group": "Content & Discipleship",
        "default": True,
        "description": "Bible reading plans members can follow and track.",
    },
    "surveys": {
        "label": "Surveys",
        "group": "Content & Discipleship",
        "default": True,
        "description": "Create and distribute surveys to members.",
    },
    # ── Finance ───────────────────────────────────────────────────────────────
    "giving": {
        "label": "Giving / Contributions",
        "group": "Finance",
        "default": True,
        "description": "Online giving via Yoco, contribution history, pledge tracking.",
    },
    # ── Structure ─────────────────────────────────────────────────────────────
    "home_church": {
        "label": "Home Churches",
        "group": "Structure",
        "default": True,
        "description": "Home church groups, rosters, attendance, and preacher assignment.",
    },
    "kg": {
        "label": "Kingdom Gateway",
        "group": "Structure",
        "default": False,
        "description": "Discipleship training cycles, exams, milestones, and teacher pool.",
    },
}

# ── In-memory cache (per-process) ────────────────────────────────────────────
# Avoids a DB hit on every request. Invalidated by invalidate_cache() which
# is called after any Settings write that touches a feature_* key.

_cache: dict[str, bool] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60  # seconds


def get_features(db: "Session") -> dict[str, bool]:
    """Return the resolved feature flags for the current assembly deployment.

    Reads  feature_<key>  rows from Settings, falls back to each feature's
    default.  Result is cached for _CACHE_TTL seconds.
    """
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and now - _cache_ts < _CACHE_TTL:
        return dict(_cache)

    from models import Settings  # avoid circular import at module load

    rows = db.query(Settings).filter(Settings.key.like("feature_%")).all()
    flags: dict[str, bool] = {k: meta["default"] for k, meta in FEATURES.items()}
    for row in rows:
        key = row.key[len("feature_"):]
        if key in flags:
            flags[key] = (row.value or "").strip().lower() in ("true", "1", "yes", "on")

    _cache = flags
    _cache_ts = now
    return dict(flags)


def default_features() -> dict[str, bool]:
    """Return the compiled defaults without touching the DB — safe to call
    before a DB session is available (e.g. during startup or in tests)."""
    return {k: meta["default"] for k, meta in FEATURES.items()}


def invalidate_cache() -> None:
    """Force the next get_features() call to re-read from the DB.
    Call this after writing any feature_* Settings row."""
    global _cache_ts
    _cache_ts = 0.0
