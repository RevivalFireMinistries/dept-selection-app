"""
Assembly feature flags — single source of truth for what the portal exposes.

Every feature the portal contains is listed here.  The SuperAdmin in
church-manager controls which flags are on/off per assembly via the
PortalConfig model.  The portal fetches that config on every request
(cached 60 s) and injects `features` into every Jinja2 template and
`window.FEATURES` in the base template so both server-side and
client-side code can gate accordingly.

Resolution order (highest-priority last wins):
  1. Compiled defaults below
  2. Local Settings rows  feature_<key> = "true"/"false"
     (useful as dev overrides or when church-manager is not connected)
  3. church-manager PortalConfig.features  ← authoritative in production

Call invalidate_cache() after writing any feature_* Settings row or
after church-manager config is expected to have changed.
"""

from __future__ import annotations
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ── Canonical feature registry ────────────────────────────────────────────────
FEATURES: dict[str, dict] = {

    # ── Member Portal — Core ─────────────────────────────────────────────────
    "department_selection": {
        "label": "Department Selection",
        "group": "Member Portal — Core",
        "default": True,
        "description": "Members choose departments to serve in; submit, edit, view results, and appeal.",
    },
    "change_requests": {
        "label": "Change Requests",
        "group": "Member Portal — Core",
        "default": True,
        "description": "Members submit profile change requests; admin reviews and approves/rejects.",
    },
    "profile_enrichment": {
        "label": "Profile Enrichment Prompts",
        "group": "Member Portal — Core",
        "default": True,
        "description": "One-at-a-time prompts on the portal home to fill in missing personal data.",
    },
    "spouse_linking": {
        "label": "Spouse Linking",
        "group": "Member Portal — Core",
        "default": True,
        "description": "Members can search and link a spouse to form a family unit.",
    },
    "push_notifications": {
        "label": "Push Notifications",
        "group": "Member Portal — Core",
        "default": True,
        "description": "Opt-in banner and subscription management for browser push alerts.",
    },
    "calendar": {
        "label": "Calendar (What's Coming Up)",
        "group": "Member Portal — Core",
        "default": True,
        "description": "Google Calendar events section on the portal home.",
    },
    "attendance": {
        "label": "Attendance Summary",
        "group": "Member Portal — Core",
        "default": True,
        "description": "Monthly and yearly view of personal service attendance.",
    },

    # ── Member Portal — Content ──────────────────────────────────────────────
    "devotionals": {
        "label": "Devotionals",
        "group": "Member Portal — Content",
        "default": True,
        "description": "Daily devotional card on portal home and full reading page.",
    },
    "verse_of_the_day": {
        "label": "Verse of the Day",
        "group": "Member Portal — Content",
        "default": True,
        "description": "Fallback scripture card shown when no devotional is scheduled.",
    },
    "reading_plans": {
        "label": "Reading Plans",
        "group": "Member Portal — Content",
        "default": True,
        "description": "Enrol in and track Bible reading plans with streaks and progress.",
    },
    "announcements": {
        "label": "Announcements",
        "group": "Member Portal — Content",
        "default": True,
        "description": "Pinned and time-windowed notice cards on the portal home.",
    },

    # ── Member Portal — Giving & Finance ─────────────────────────────────────
    "giving": {
        "label": "Online Giving",
        "group": "Member Portal — Giving & Finance",
        "default": True,
        "description": "Online giving form via Yoco (card / Apple Pay).",
    },
    "banking_details": {
        "label": "Banking Details",
        "group": "Member Portal — Giving & Finance",
        "default": True,
        "description": "Church bank account details card with copy and share buttons.",
    },
    "pledges": {
        "label": "Pledges",
        "group": "Member Portal — Giving & Finance",
        "default": True,
        "description": "Active pledge display and payment against projects.",
    },
    "contribution_history": {
        "label": "Contribution History",
        "group": "Member Portal — Giving & Finance",
        "default": True,
        "description": "Past giving grouped by month with project filter.",
    },

    # ── Member Portal — Community ────────────────────────────────────────────
    "meetings": {
        "label": "Meetings",
        "group": "Member Portal — Community",
        "default": True,
        "description": "Upcoming department and leadership meetings with RSVP.",
    },
    "home_church": {
        "label": "Home Church",
        "group": "Member Portal — Community",
        "default": True,
        "description": "Home church membership display on the portal.",
    },
    "surveys": {
        "label": "Surveys",
        "group": "Member Portal — Community",
        "default": True,
        "description": "Survey list, responses, and builder (for authorised members).",
    },

    # ── Member Portal — Services & Media ────────────────────────────────────
    "programs": {
        "label": "Service Programs",
        "group": "Member Portal — Services & Media",
        "default": True,
        "description": "Service order-of-service programs visible to members.",
    },
    "poster_requests": {
        "label": "Poster / Design Requests",
        "group": "Member Portal — Services & Media",
        "default": True,
        "description": "Members request event posters from the design team.",
    },
    "projector_submit": {
        "label": "Projector / Display Submission",
        "group": "Member Portal — Services & Media",
        "default": True,
        "description": "Members submit media, verses, and announcements for the projector.",
    },
    "songs": {
        "label": "Song List",
        "group": "Member Portal — Services & Media",
        "default": True,
        "description": "Worship song set-list submission and song catalog.",
    },

    # ── Kingdom Gateway ──────────────────────────────────────────────────────
    "kg": {
        "label": "Kingdom Gateway (Discipleship)",
        "group": "Kingdom Gateway",
        "default": False,
        "description": "Full KG module — member enrolment, exams, cycles, milestones, and all admin/desk KG screens.",
    },

    # ── Public (No Login) ────────────────────────────────────────────────────
    "connect_form": {
        "label": "Connect Card (Visitor Form)",
        "group": "Public",
        "default": True,
        "description": "Public visitor registration form at /connect. Includes admin submissions view.",
    },
    "public_giving": {
        "label": "Public Giving Page",
        "group": "Public",
        "default": True,
        "description": "Anyone can donate at /give without creating an account.",
    },
    "public_surveys": {
        "label": "Public Surveys",
        "group": "Public",
        "default": True,
        "description": "Anonymous survey responses via shareable /s/{slug} links.",
    },

    # ── Admin — Service Management ───────────────────────────────────────────
    "admin_programs": {
        "label": "Programs (Admin)",
        "group": "Admin — Service Management",
        "default": True,
        "description": "Service programs, templates, and schedules in the admin panel.",
    },
    "admin_meetings": {
        "label": "Meetings (Admin)",
        "group": "Admin — Service Management",
        "default": True,
        "description": "Meeting creation, RSVP tracking, and reminder management.",
    },
    "admin_poster_requests": {
        "label": "Poster Requests (Admin)",
        "group": "Admin — Service Management",
        "default": True,
        "description": "Poster design request management in the admin panel.",
    },
    "admin_projector_submissions": {
        "label": "Display Submissions (Admin)",
        "group": "Admin — Service Management",
        "default": True,
        "description": "Approve, reject, and delete projector media submissions.",
    },
    "admin_songs": {
        "label": "Song Catalog (Admin)",
        "group": "Admin — Service Management",
        "default": True,
        "description": "Song catalog and set-list management in the admin panel.",
    },

    # ── Admin — Content Management ───────────────────────────────────────────
    "admin_devotionals": {
        "label": "Devotionals (Admin)",
        "group": "Admin — Content Management",
        "default": True,
        "description": "Devotional builder, scheduling, and publishing.",
    },
    "admin_reading_plans": {
        "label": "Reading Plans (Admin)",
        "group": "Admin — Content Management",
        "default": True,
        "description": "Reading plan builder and member completion metrics.",
    },
    "admin_announcements": {
        "label": "Announcements (Admin)",
        "group": "Admin — Content Management",
        "default": True,
        "description": "Announcement creation, pinning, and scheduling.",
    },
    "admin_surveys": {
        "label": "Surveys (Admin)",
        "group": "Admin — Content Management",
        "default": True,
        "description": "Survey creation, distribution, and response analysis.",
    },

    # ── Admin — Structure ────────────────────────────────────────────────────
    "admin_home_churches": {
        "label": "Home Churches (Admin)",
        "group": "Admin — Structure",
        "default": True,
        "description": "Home church cells, rosters, preacher assignment, and attendance.",
    },
    "admin_connect_submissions": {
        "label": "Connect Submissions (Admin)",
        "group": "Admin — Structure",
        "default": True,
        "description": "Review visitor registration submissions.",
    },
    "admin_rfm_sync": {
        "label": "Member Sync (Admin)",
        "group": "Admin — Structure",
        "default": True,
        "description": "Sync members to/from the central rfm-database.",
    },

    # ── Admin — Finance ──────────────────────────────────────────────────────
    "admin_giving": {
        "label": "Giving / Payments (Admin)",
        "group": "Admin — Finance",
        "default": True,
        "description": "Yoco configuration, transaction history, and payment webhooks.",
    },

    # ── Admin — Kingdom Gateway ──────────────────────────────────────────────
    "admin_kg": {
        "label": "Kingdom Gateway (Admin)",
        "group": "Admin — Kingdom Gateway",
        "default": False,
        "description": "Cycle management, enrolment, exam administration, team, and reports.",
    },

    # ── Admin — AI ──────────────────────────────────────────────────────────
    "admin_ai": {
        "label": "AI Features (Admin)",
        "group": "Admin — AI",
        "default": False,
        "description": "AI provider configuration and AI-powered member tools.",
    },

    # ── Operator Tools ───────────────────────────────────────────────────────
    "info_desk": {
        "label": "Info Desk",
        "group": "Operator Tools",
        "default": True,
        "description": "Info desk login and all desk screens for member lookup and registration.",
    },
    "projector_operator": {
        "label": "Projector Operator Screen",
        "group": "Operator Tools",
        "default": True,
        "description": "Full projector operator screen at /projector with media, verses, and songs tabs.",
    },
}


# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict[str, bool] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60  # seconds


def get_features(db: "Session") -> dict[str, bool]:
    """Return resolved feature flags.  Resolution order (last wins):
      1. Compiled defaults
      2. Local Settings rows  feature_<key>
      3. church-manager PortalConfig.features  (highest priority)
    Result cached for _CACHE_TTL seconds.
    """
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and now - _cache_ts < _CACHE_TTL:
        return dict(_cache)

    flags: dict[str, bool] = {k: meta["default"] for k, meta in FEATURES.items()}

    # Layer 1 — local Settings overrides
    try:
        from models import Settings
        rows = db.query(Settings).filter(Settings.key.like("feature_%")).all()
        for row in rows:
            key = row.key[len("feature_"):]
            if key in flags:
                flags[key] = (row.value or "").strip().lower() in ("true", "1", "yes", "on")
    except Exception:
        pass

    # Layer 2 — church-manager PortalConfig (authoritative)
    try:
        from church_manager_client import get_portal_config
        remote = get_portal_config()
        if remote and isinstance(remote.get("features"), dict):
            for key, val in remote["features"].items():
                if key in flags:
                    flags[key] = bool(val)
    except Exception:
        pass

    _cache = flags
    _cache_ts = now
    return dict(flags)


def default_features() -> dict[str, bool]:
    """Compiled defaults only — safe to call without a DB session."""
    return {k: meta["default"] for k, meta in FEATURES.items()}


def invalidate_cache() -> None:
    """Force next get_features() to re-read from DB and church-manager."""
    global _cache_ts
    _cache_ts = 0.0
    try:
        from church_manager_client import invalidate_cache as _cm
        _cm()
    except Exception:
        pass
