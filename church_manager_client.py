"""
Church Manager client — fetches this assembly's portal configuration.

The portal uses church-manager as the authoritative source for feature
flags.  On each request (via inject_features_middleware) features.py
calls get_portal_config() which either returns the cached result or
fetches a fresh copy from church-manager.

Required environment variables
-------------------------------
CHURCH_MANAGER_URL          Base URL of the church-manager backend API,
                             e.g. https://church-manager-api.rfm.org.za
                             Must NOT have a trailing slash.

CHURCH_MANAGER_API_KEY      Shared secret that matches  portal_api_key
                             in church-manager's .env.  Both sides must
                             hold the same value.

CHURCH_MANAGER_ASSEMBLY_ID  UUID of this deployment's assembly in
                             church-manager, e.g. the value from the
                             church-manager admin /assemblies list.

If any of the three vars is unset the client silently returns None and
features.py falls back to the local Settings table / compiled defaults.
"""

from __future__ import annotations

import os
import time
import logging
import urllib.request
import urllib.error
import json

logger = logging.getLogger(__name__)

_CHURCH_MANAGER_URL        = (os.environ.get("CHURCH_MANAGER_URL") or "").rstrip("/")
_CHURCH_MANAGER_API_KEY    = os.environ.get("CHURCH_MANAGER_API_KEY") or ""
_CHURCH_MANAGER_ASSEMBLY_ID = os.environ.get("CHURCH_MANAGER_ASSEMBLY_ID") or ""

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 60  # seconds — same cadence as features.py local cache

# Exponential back-off: after a failed fetch don't hammer the remote.
_backoff_until: float = 0.0
_failure_count: int = 0
_MAX_BACKOFF = 300  # 5 minutes


def _is_configured() -> bool:
    return bool(_CHURCH_MANAGER_URL and _CHURCH_MANAGER_API_KEY and _CHURCH_MANAGER_ASSEMBLY_ID)


def get_portal_config() -> dict | None:
    """Return the PortalConfigPublic payload from church-manager, or None.

    None means "not available" — callers should fall back to local config.
    The result is cached for _CACHE_TTL seconds to avoid per-request HTTP.
    """
    global _cache, _cache_ts, _backoff_until, _failure_count

    if not _is_configured():
        return None

    now = time.monotonic()

    # Honour back-off after repeated failures
    if now < _backoff_until:
        return _cache  # return stale cache rather than None during back-off

    # Serve from cache while fresh
    if _cache is not None and now - _cache_ts < _CACHE_TTL:
        return _cache

    url = (
        f"{_CHURCH_MANAGER_URL}/api/portal-configs/by-assembly"
        f"/{_CHURCH_MANAGER_ASSEMBLY_ID}"
    )
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
        _cache = data
        _cache_ts = now
        _failure_count = 0
        _backoff_until = 0.0
        logger.debug("portal config refreshed from church-manager")
        return _cache
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Assembly not configured in church-manager yet — not an error,
            # just means the portal should use its local defaults.
            logger.info(
                "No portal config in church-manager for assembly %s — using local defaults",
                _CHURCH_MANAGER_ASSEMBLY_ID,
            )
            _cache = None
            _cache_ts = now  # don't retry immediately
        else:
            _failure_count += 1
            backoff = min(10 * (2 ** _failure_count), _MAX_BACKOFF)
            _backoff_until = now + backoff
            logger.warning(
                "church-manager returned HTTP %s — backing off %ss (attempt %s)",
                exc.code, backoff, _failure_count,
            )
        return _cache
    except Exception as exc:
        _failure_count += 1
        backoff = min(10 * (2 ** _failure_count), _MAX_BACKOFF)
        _backoff_until = now + backoff
        logger.warning(
            "Failed to fetch portal config from church-manager: %s — backing off %ss",
            exc, backoff,
        )
        return _cache  # return stale if available


def invalidate_cache() -> None:
    """Force the next get_portal_config() call to re-fetch."""
    global _cache_ts, _backoff_until
    _cache_ts = 0.0
    _backoff_until = 0.0
