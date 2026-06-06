"""
Church Manager client — fetches per-assembly portal configuration.

The portal is multi-tenant: one deployment serves all assemblies.
Features are fetched from church-manager keyed by assembly UUID,
so each assembly gets exactly the set of features the SuperAdmin
configured for it.

Required environment variables
-------------------------------
CHURCH_MANAGER_URL      Base URL of the church-manager backend API,
                        e.g. https://church-manager-api.rfm.org.za
                        Must NOT have a trailing slash.

CHURCH_MANAGER_API_KEY  Shared secret that matches  portal_api_key
                        in church-manager's .env.

If either var is unset the client returns None for every assembly and
features.py falls back to the local Settings table / compiled defaults.
"""

from __future__ import annotations

import os
import time
import logging
import urllib.parse
import urllib.request
import urllib.error
import json
from threading import Lock

logger = logging.getLogger(__name__)

_CHURCH_MANAGER_URL    = (os.environ.get("CHURCH_MANAGER_URL") or "").rstrip("/")
_CHURCH_MANAGER_API_KEY = os.environ.get("CHURCH_MANAGER_API_KEY") or ""

_CACHE_TTL  = 60   # seconds per assembly
_MAX_BACKOFF = 300  # 5 minutes

# ── Per-assembly cache ────────────────────────────────────────────────────────
# Keyed by assembly UUID string so each assembly gets its own independent TTL.
_cache:          dict[str, dict | None] = {}   # assembly_id → payload or None
_cache_ts:       dict[str, float]       = {}   # assembly_id → monotonic timestamp
_backoff_until:  dict[str, float]       = {}   # assembly_id → back-off expires
_failure_count:  dict[str, int]         = {}   # assembly_id → consecutive failures
_lock = Lock()


def _is_configured() -> bool:
    return bool(_CHURCH_MANAGER_URL and _CHURCH_MANAGER_API_KEY)


def get_portal_config(assembly_id: str) -> dict | None:
    """Return the PortalConfigPublic payload for  assembly_id, or None.

    None means "not available" — callers fall back to local Settings /
    compiled defaults.  Result is cached per assembly for _CACHE_TTL seconds
    with exponential back-off on repeated failures so transient network
    blips don't flip features off for members.
    """
    if not _is_configured() or not assembly_id:
        return None

    now = time.monotonic()

    with _lock:
        # Honour per-assembly back-off
        if now < _backoff_until.get(assembly_id, 0):
            return _cache.get(assembly_id)  # stale is better than nothing

        # Serve from per-assembly cache while fresh
        if assembly_id in _cache and now - _cache_ts.get(assembly_id, 0) < _CACHE_TTL:
            return _cache[assembly_id]

    url = f"{_CHURCH_MANAGER_URL}/api/portal-configs/by-assembly/{assembly_id}"
    req = urllib.request.Request(
        url,
        headers={
            "X-Portal-API-Key": _CHURCH_MANAGER_API_KEY,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        with _lock:
            _cache[assembly_id]         = data
            _cache_ts[assembly_id]      = time.monotonic()
            _failure_count[assembly_id] = 0
            _backoff_until[assembly_id] = 0.0
        logger.debug("portal config refreshed for assembly %s", assembly_id)
        return data

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Assembly not yet configured in church-manager — use defaults.
            logger.info("No portal config for assembly %s — using defaults", assembly_id)
            with _lock:
                _cache[assembly_id]    = None
                _cache_ts[assembly_id] = time.monotonic()
        else:
            with _lock:
                count = _failure_count.get(assembly_id, 0) + 1
                _failure_count[assembly_id] = count
                backoff = min(10 * (2 ** count), _MAX_BACKOFF)
                _backoff_until[assembly_id] = time.monotonic() + backoff
            logger.warning(
                "church-manager HTTP %s for assembly %s — back-off %ss",
                exc.code, assembly_id, backoff,
            )
        return _cache.get(assembly_id)

    except Exception as exc:
        with _lock:
            count = _failure_count.get(assembly_id, 0) + 1
            _failure_count[assembly_id] = count
            backoff = min(10 * (2 ** count), _MAX_BACKOFF)
            _backoff_until[assembly_id] = time.monotonic() + backoff
        logger.warning(
            "Failed to fetch portal config for assembly %s: %s — back-off %ss",
            assembly_id, exc, backoff,
        )
        return _cache.get(assembly_id)


# NOTE: The Elder attendance report now sources its data from rfm-database's
# member_attendance table (see rfm_api_client.assembly_attendance_*), not from
# church-manager. church-manager remains the source for portal feature flags.


def invalidate_cache(assembly_id: str | None = None) -> None:
    """Force the next fetch for  assembly_id  (or all assemblies) to re-fetch."""
    with _lock:
        if assembly_id:
            _cache_ts.pop(assembly_id, None)
            _backoff_until.pop(assembly_id, None)
        else:
            _cache_ts.clear()
            _backoff_until.clear()
