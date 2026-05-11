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


def upcoming_events(
    db: Session,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    time_max_iso: Optional[str] = None,
) -> List[dict]:
    """Return the next N events from the configured calendar.

    Each event:
      {
        id, summary, description (FULL, untrimmed), location,
        start_iso, end_iso, all_day, html_link
      }
    Empty list if not configured, network fails, or the calendar is empty.
    """
    cal_id = _get_setting(db, "google_calendar_id")
    api_key = _get_setting(db, "google_calendar_api_key")
    if not cal_id or not api_key:
        return []

    # Cached? Key includes time_max so different windows cache separately
    cache_key = f"{cal_id}:{max_results}:{time_max_iso or ''}"
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
    if time_max_iso:
        params["timeMax"] = time_max_iso
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

        # Full description preserved — the portal sheet shows it in full
        description = (item.get("description") or "").strip()

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


# ---------------------------------------------------------------------------
# Ministry-aware filtering
# ---------------------------------------------------------------------------
# Events whose titles mention a specific ministry are only shown to members
# of that ministry. Untagged events (e.g. "Home Church Service",
# "Fire Wednesday") are general — everyone sees them.

import re as _re

# bucket -> regex pattern matched (case-insensitive) against the event title
# and against the member's ministry names. Word-boundary so 'men' doesn't
# match 'women' and 'youth' doesn't match 'young adults'.
_MINISTRY_PATTERNS = {
    "singles":      _re.compile(r"\b(singles?|widowed)\b", _re.IGNORECASE),
    "couples":      _re.compile(r"\b(couples?|married)\b", _re.IGNORECASE),
    "ladies":       _re.compile(r"\b(ladies|women(?:'?s)?)\b", _re.IGNORECASE),
    "men":          _re.compile(r"\b(men(?:'?s)?)\b", _re.IGNORECASE),
    "teens":        _re.compile(r"\bteens?\b", _re.IGNORECASE),
    "youth":        _re.compile(r"\byouth\b", _re.IGNORECASE),
    "young_adults": _re.compile(r"\b(young\s+(?:unmarried\s+)?adults?|yua)\b", _re.IGNORECASE),
    # HoDs only — events for heads of department or project management
    "hod":          _re.compile(r"\b(hods?|heads?\s+of\s+departments?|project\s+management|project\s+managers?)\b", _re.IGNORECASE),
    # Everyone who serves (has at least one approved department)
    "serving":      _re.compile(r"\b(leaders?|leadership)\b", _re.IGNORECASE),
}


def detect_event_ministry_tags(title: str) -> set:
    """Return the audience buckets this event seems targeted at.

    Empty set = general event (everyone sees it). 'Women' is checked before
    'Men' so 'Women's Online Session' doesn't register as a men's event.
    """
    if not title:
        return set()
    text = title
    tags = set()
    has_ladies = bool(_MINISTRY_PATTERNS["ladies"].search(text))
    if has_ladies:
        tags.add("ladies")
    # Only check 'men' if 'ladies' wasn't matched — defence in depth
    if not has_ladies and _MINISTRY_PATTERNS["men"].search(text):
        tags.add("men")
    for bucket in ("singles", "couples", "teens", "youth", "young_adults", "hod", "serving"):
        if _MINISTRY_PATTERNS[bucket].search(text):
            tags.add(bucket)
    return tags


def member_ministry_buckets(ministry_names) -> set:
    """Map the central API's ministry names (e.g. 'Men On Fire',
    'Couples On Fire', 'Young Adults') to our buckets so we can intersect."""
    buckets = set()
    if not ministry_names:
        return buckets
    for raw in ministry_names:
        if not raw:
            continue
        name = str(raw)
        has_ladies = bool(_MINISTRY_PATTERNS["ladies"].search(name))
        if has_ladies:
            buckets.add("ladies")
        if not has_ladies and _MINISTRY_PATTERNS["men"].search(name):
            buckets.add("men")
        for bucket in ("singles", "couples", "teens", "youth", "young_adults"):
            if _MINISTRY_PATTERNS[bucket].search(name):
                buckets.add(bucket)
    return buckets


def filter_events_for_member(events: list, ministry_names) -> list:
    """Backward-compatible wrapper. Use filter_events_by_buckets() when the
    caller already has the final bucket set (e.g. ministry + HoD + serving)."""
    if not ministry_names:
        return list(events or [])
    return filter_events_by_buckets(events, member_ministry_buckets(ministry_names))


def filter_events_by_buckets(events: list, member_buckets: set) -> list:
    """Drop targeted events the member isn't in. Untagged events (no
    recognised audience keyword in the title) pass through unchanged.

    Empty buckets => permissive (show all) — better than hiding the
    whole calendar from a member who isn't yet centrally synced or
    serving anywhere.
    """
    if not member_buckets:
        return list(events or [])
    out = []
    for e in (events or []):
        tags = detect_event_ministry_tags(e.get("summary", ""))
        if not tags:
            out.append(e)
            continue
        if tags & member_buckets:
            out.append(e)
    return out
