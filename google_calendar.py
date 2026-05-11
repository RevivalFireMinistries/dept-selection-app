"""Google Calendar v3 read-only client — fetches upcoming events from a
public Google Calendar so we can surface them on the portal Home tab.

Configuration (Settings table; admin sets via /admin/settings):
  google_calendar_id     — e.g. 'revivalfire@gmail.com' or
                            'abc123@group.calendar.google.com'
  google_calendar_api_key — Google Cloud API key with Calendar API enabled

The calendar must be publicly readable (or shared with the API key's
project's service account). Stdlib HTTP — no extra deps.

Results are cached in-memory for 10 minutes to avoid hitting Google
on every portal load.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session


CACHE_TTL_SECONDS = 10 * 60   # 10 minutes
DEFAULT_MAX_RESULTS = 8
DEFAULT_TIMEOUT = 8

# Module-level cache. Key = calendar_id; value = (fetched_at_unix, events_list).
_cache: dict = {}


def _get_setting(db: Session, key: str) -> str:
    from models import Settings
    row = db.query(Settings).filter(Settings.key == key).first()
    return (row.value or "").strip() if row and row.value else ""


def is_configured(db: Session) -> bool:
    return bool(_get_setting(db, "google_calendar_id") and _get_setting(db, "google_calendar_api_key"))


def upcoming_events(db: Session, *, max_results: int = DEFAULT_MAX_RESULTS) -> List[dict]:
    """Return the next N events from the configured calendar.

    Each event:
      {
        id, summary, description (truncated), location,
        start_iso, end_iso, all_day, html_link
      }
    Empty list if not configured, network fails, or the calendar is empty.
    """
    cal_id = _get_setting(db, "google_calendar_id")
    api_key = _get_setting(db, "google_calendar_api_key")
    if not cal_id or not api_key:
        return []

    # Cached?
    cache_key = f"{cal_id}:{max_results}"
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "key": api_key,
        "timeMin": now_iso,
        "singleEvents": "true",      # expand recurring events
        "orderBy": "startTime",
        "maxResults": str(int(max_results)),
        "showDeleted": "false",
    }
    url = (
        f"https://www.googleapis.com/calendar/v3/calendars/"
        f"{urllib.parse.quote(cal_id)}/events?"
        f"{urllib.parse.urlencode(params)}"
    )
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "RFM-Portal/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        # Surface the API error message into the cache so we don't hammer
        # a misconfigured calendar. Empty list to the caller.
        _cache[cache_key] = (time.time(), [])
        return []
    except Exception:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    out: List[dict] = []
    for item in (data.get("items") or []):
        start = item.get("start") or {}
        end = item.get("end") or {}
        all_day = "date" in start and "dateTime" not in start
        start_iso = start.get("dateTime") or start.get("date") or ""
        end_iso = end.get("dateTime") or end.get("date") or ""

        description = (item.get("description") or "").strip()
        if len(description) > 240:
            description = description[:237] + "…"

        out.append({
            "id": item.get("id"),
            "summary": (item.get("summary") or "Untitled event").strip(),
            "description": description,
            "location": (item.get("location") or "").strip(),
            "start_iso": start_iso,
            "end_iso": end_iso,
            "all_day": all_day,
            "html_link": item.get("htmlLink") or "",
        })

    _cache[cache_key] = (time.time(), out)
    return out


def clear_cache():
    """Force a refresh on next call. Used after admin updates the settings."""
    _cache.clear()
