"""Per-event push payload builder.

Maps a notification EventType + render-data dict to the (title, body, url)
that the service worker shows. Centralised here so both the dispatcher
and any one-off admin tools render the same copy.

Pure function — no DB, no HTTP. Test by calling with sample data.
"""
from __future__ import annotations

from typing import Tuple


def build_push_payload(event_type, data: dict) -> Tuple[str, str, str]:
    """Return (title, body, url). Falls back to a generic shape for any
    event we haven't curated copy for so callers can rely on a tuple
    coming back."""
    et = getattr(event_type, "value", str(event_type))

    def _get(key, default=""):
        v = data.get(key)
        return str(v) if v not in (None, "") else default

    title_body_url = {
        "member_approved": (
            "Selection approved",
            f"Your selection for {_get('department_name')} has been approved.",
            "/portal",
        ),
        "member_rejected": (
            "Selection update",
            f"Your selection for {_get('department_name')} wasn't approved. Tap to see details.",
            "/portal",
        ),
        "department_assigned": (
            "Assigned to a department",
            f"You've been added to {_get('department_name')}.",
            "/portal",
        ),
        "results_published": (
            "Results published",
            "Department selection results are now available.",
            "/portal",
        ),
        "appeal_submitted": (
            "New appeal",
            f"{_get('member_name', 'A member')} has submitted an appeal.",
            "/admin/appeals",
        ),
        "appeal_resolved": (
            "Appeal resolved",
            f"Your appeal has been {_get('status', 'reviewed')}.",
            "/portal",
        ),
        "meeting_created": (
            f"New meeting: {_get('title', 'check details')}",
            f"{_get('meeting_date')} · {_get('start_time')} · {_get('location') or 'see details'}",
            "/portal",
        ),
        "meeting_reminder": (
            f"Today: {_get('title', 'meeting')}",
            f"{_get('start_time')} · {_get('location') or 'see details'}",
            "/portal",
        ),
        "meeting_updated": (
            f"Updated: {_get('title', 'meeting')}",
            "Details have changed. Tap to see what's new.",
            "/portal",
        ),
        "meeting_cancelled": (
            f"Cancelled: {_get('title', 'meeting')}",
            "This meeting has been cancelled.",
            "/portal",
        ),
        "poster_request_submitted": (
            "New poster request",
            f"{_get('event_name', 'A new design')} needs your eyes.",
            "/admin/poster-requests",
        ),
        "poster_request_acknowledged": (
            "Poster in progress",
            f"The design team has picked up your request: {_get('event_name', '')}.",
            "/portal",
        ),
        "poster_request_completed": (
            "Poster ready",
            f"Your design for {_get('event_name', 'the event')} is ready.",
            "/portal",
        ),
        "program_participant_added": (
            f"You're on the program: {_get('title', 'service')}",
            f"{_get('service_date')} — tap for details.",
            "/portal",
        ),
        "home_church_roster_published": (
            "Home church roster",
            f"{_get('home_church_name')} on {_get('roster_date')} — see who's preaching.",
            "/portal",
        ),
        "home_church_preacher_assigned": (
            "You're preaching",
            f"{_get('home_church_name')} on {_get('roster_date')}.",
            "/portal",
        ),
        "home_church_reminder_leader": (
            "Home church tomorrow",
            "Quick reminder — your home church meets tomorrow.",
            "/portal",
        ),
        "home_church_reminder_preacher": (
            "Preaching tomorrow",
            f"You're preaching at {_get('home_church_name')} tomorrow.",
            "/portal",
        ),
        "home_church_attendance_reminder": (
            "Attendance reports pending",
            f"{_get('pending_count', '?')} home church reports still to capture.",
            "/admin/home-churches",
        ),
        "prayer_chain_schedule": (
            f"🕊️ {_get('group_name', 'Prayer chain')}",
            "Your prayer chain slots are ready — tap to view your times.",
            "/portal",
        ),
    }
    if et in title_body_url:
        return title_body_url[et]
    # Sensible default for any future event we forget
    return ("Update", str(data.get("title") or "You have a new update."), "/portal")
