"""
Assembly context resolver.

Every portal deployment belongs to exactly one assembly.  This module
resolves that identity so the portal can:

  - Display the assembly name in the UI
  - Fetch the right PortalConfig from church-manager
  - Gate all access behind "is this portal configured?"

Resolution order for the assembly UUID
---------------------------------------
1. CHURCH_MANAGER_ASSEMBLY_ID  env var  (set in Railway per-deployment)
2. rfm_assembly_id             Settings row  (set in admin → Settings)

Resolution for the assembly name
---------------------------------
1. assembly_name               Settings row  (set in admin → Settings)
2. "Revival Fire Ministries"   fallback

If neither source provides a UUID the portal is **unconfigured**.
inject_features_middleware (main.py) redirects every non-exempt
request to /not-configured in that case.

Exempt paths (never redirected):
  /admin/login          — admin must be able to log in to fix config
  /admin/settings       — admin sets rfm_assembly_id here
  /not-configured       — the page itself
  /static/*             — assets
  /api/*                — API responses handle their own errors
  /sw.js                — PWA service worker
  /manifest.webmanifest
  /offline
  /health
"""

from __future__ import annotations
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_CACHE_TTL = 300  # 5 minutes — assembly rarely changes

_cache: dict | None = None
_cache_ts: float = 0.0

# Paths the middleware never redirects even when unconfigured.
EXEMPT_PREFIXES = (
    "/admin/login",
    "/admin/settings",
    "/not-configured",
    "/static/",
    "/api/",
    "/sw.js",
    "/manifest.webmanifest",
    "/offline",
    "/health",
    "/favicon",
)


def get_assembly_context(db: "Session | None" = None) -> dict:
    """Return  {id, name, configured}  for this deployment.

    id         — UUID string used to fetch PortalConfig from church-manager
    name       — display name shown in the portal header
    configured — False when no assembly UUID is available anywhere
    """
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache is not None and now - _cache_ts < _CACHE_TTL:
        return dict(_cache)

    assembly_id   = os.environ.get("CHURCH_MANAGER_ASSEMBLY_ID", "").strip() or None
    assembly_name = None

    if db is not None:
        try:
            from models import Settings
            rows = {
                r.key: r.value
                for r in db.query(Settings).filter(
                    Settings.key.in_(["rfm_assembly_id", "assembly_name"])
                ).all()
            }
            if not assembly_id:
                assembly_id = rows.get("rfm_assembly_id", "").strip() or None
            assembly_name = rows.get("assembly_name", "").strip() or None
        except Exception:
            pass

    ctx = {
        "id":         assembly_id,
        "name":       assembly_name or "Revival Fire Ministries",
        "configured": bool(assembly_id),
    }
    _cache = ctx
    _cache_ts = now
    return dict(ctx)


def is_exempt(path: str) -> bool:
    """Return True for paths that bypass the assembly-context check."""
    return any(path.startswith(p) for p in EXEMPT_PREFIXES)


def invalidate_cache() -> None:
    global _cache_ts
    _cache_ts = 0.0
