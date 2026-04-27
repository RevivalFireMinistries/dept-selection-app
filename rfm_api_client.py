"""Client for the rfm-database service (the central member directory).

This module is the *only* thing in dptSelectionApp that knows how to talk to
the central member database. All other code reads/writes through these
functions so a future swap of provider, base URL, or auth mechanism only
touches this file.

Key principles:
  * Feature-flagged via RFM_API_INTEGRATION_ENABLED. When disabled, functions
    short-circuit with a clear "disabled" result so callers can continue with
    legacy behaviour. This is the rollback lever.
  * Stdlib-only HTTP (urllib) — no extra requirements.
  * Errors never crash the caller. Every function returns a well-typed result
    so a backend outage degrades gracefully.
  * No global state besides config; safe to call from anywhere.

Configuration (Railway env vars — also fall back to Settings table):
  RFM_API_URL                   e.g. https://rfm-db-api.railway.app
  RFM_API_KEY                   X-API-Key value with member read/write scope
  RFM_API_INTEGRATION_ENABLED   "true" / "false" (default false until ready)
  RFM_API_TTL_SECONDS           Cache freshness (default 86400 = 24h)
  RFM_API_TIMEOUT_SECONDS       HTTP timeout per request (default 8s)

Response envelope from the API:
  { "data": <payload>, "meta": {"cached": false, ...} }
  We unwrap that automatically.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 8
DEFAULT_TTL = 86400


def _from_settings(db, key: str) -> Optional[str]:
    """Read a fallback value from the church-portal Settings table."""
    if db is None:
        return None
    try:
        from models import Settings  # local import to avoid circulars at import time
        row = db.query(Settings).filter(Settings.key == key).first()
        return row.value if row else None
    except Exception:
        return None


def get_api_url(db=None) -> str:
    return (os.getenv("RFM_API_URL") or _from_settings(db, "rfm_api_url") or "").rstrip("/")


def get_api_key(db=None) -> str:
    return os.getenv("RFM_API_KEY") or _from_settings(db, "rfm_api_key") or ""


def is_enabled(db=None) -> bool:
    """The kill switch. False by default — set RFM_API_INTEGRATION_ENABLED=true
    once the rest of the system is wired up. Setting it back to false at any
    time (Railway → Variables) reverts every API-driven behaviour to legacy."""
    val = os.getenv("RFM_API_INTEGRATION_ENABLED")
    if val is None:
        val = _from_settings(db, "rfm_api_integration_enabled")
    return (val or "").strip().lower() == "true"


def get_timeout(db=None) -> int:
    raw = os.getenv("RFM_API_TIMEOUT_SECONDS") or _from_settings(db, "rfm_api_timeout_seconds")
    try:
        return int(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT


def get_ttl_seconds(db=None) -> int:
    raw = os.getenv("RFM_API_TTL_SECONDS") or _from_settings(db, "rfm_api_ttl_seconds")
    try:
        return int(raw) if raw else DEFAULT_TTL
    except ValueError:
        return DEFAULT_TTL


def is_configured(db=None) -> bool:
    """True when the URL + key are both present. Independent of `is_enabled`
    so callers can show 'configured but disabled' diagnostics."""
    return bool(get_api_url(db)) and bool(get_api_key(db))


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ApiResult:
    """Wraps every response so callers don't have to handle exceptions.
    `ok` means the call succeeded; `data` holds the unwrapped envelope payload."""
    ok: bool
    data: Any = None
    status: int = 0
    error: Optional[str] = None
    disabled: bool = False  # True when the kill switch is off

    @classmethod
    def disabled_result(cls) -> "ApiResult":
        return cls(ok=False, disabled=True, error="rfm-db integration disabled")

    @classmethod
    def err(cls, msg: str, status: int = 0) -> "ApiResult":
        return cls(ok=False, error=msg, status=status)


# ---------------------------------------------------------------------------
# Low-level HTTP
# ---------------------------------------------------------------------------

def _request(
    method: str,
    path: str,
    *,
    db=None,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
) -> ApiResult:
    if not is_enabled(db):
        return ApiResult.disabled_result()
    url = get_api_url(db)
    key = get_api_key(db)
    if not url or not key:
        return ApiResult.err("rfm-db API not configured (RFM_API_URL / RFM_API_KEY missing)")

    full = f"{url}{path}"
    if params:
        # Drop None values
        cleaned = {k: v for k, v in params.items() if v is not None}
        if cleaned:
            full = f"{full}?{urllib.parse.urlencode(cleaned, doseq=True)}"

    data_bytes = None
    headers = {
        "X-API-Key": key,
        "Accept": "application/json",
        "User-Agent": "RFM-Stellenbosch-Portal/1.0",
    }
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(full, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=get_timeout(db)) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        status = e.code
    except urllib.error.URLError as e:
        return ApiResult.err(f"network error: {e.reason}")
    except Exception as e:
        return ApiResult.err(f"request failed: {e}")

    # Try to parse JSON envelope
    payload = None
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None

    if 200 <= status < 300:
        # API uses {"data": ..., "meta": ...}; unwrap if present
        if isinstance(payload, dict) and "data" in payload:
            return ApiResult(ok=True, data=payload["data"], status=status)
        return ApiResult(ok=True, data=payload, status=status)

    # Error
    err_msg = None
    if isinstance(payload, dict):
        err = payload.get("error") or {}
        err_msg = err.get("message") or payload.get("detail") or json.dumps(payload)[:300]
    return ApiResult.err(err_msg or f"HTTP {status}", status=status)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def health_check(db=None) -> ApiResult:
    """Lightweight ping (used by admin UI to show connection status)."""
    return _request("GET", "/health", db=db)


def search_members(
    *,
    search: Optional[str] = None,
    phone: Optional[str] = None,
    assembly_id: Optional[str] = None,
    page: int = 1,
    size: int = 20,
    db=None,
) -> ApiResult:
    """List members, optionally filtered. Returns the API's paginated payload
    (a list of member dicts under .data when ok)."""
    # API supports a `search` ILIKE on name+phone+email; we route phone-only
    # lookups through it too because there's no dedicated phone endpoint.
    q = search or phone
    return _request(
        "GET",
        "/api/v1/members",
        db=db,
        params={
            "search": q,
            "assembly_id": assembly_id,
            "page": page,
            "size": size,
        },
    )


def get_member(member_id: str, *, db=None) -> ApiResult:
    return _request("GET", f"/api/v1/members/{member_id}", db=db)


def update_member(member_id: str, fields: dict, *, db=None) -> ApiResult:
    """Partial update — uses PATCH so we only send what changed."""
    return _request("PATCH", f"/api/v1/members/{member_id}", db=db, body=fields)


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

_PHONE_NON_DIGIT = re.compile(r"\D+")


def normalise_phone(raw: str) -> str:
    """Strip everything except digits. Good enough for "0712345678" vs
    "071 234 5678" vs "+27 71 234 5678" comparisons (last 9 digits should match)."""
    if not raw:
        return ""
    digits = _PHONE_NON_DIGIT.sub("", raw)
    # Treat South African numbers — drop leading "27" if present so 27821234567
    # matches 0821234567 by the last-9-digit comparison.
    return digits[-9:] if len(digits) >= 9 else digits


def fullname_from_member(api_member: dict) -> str:
    """API splits first_name + last_name; we display joined."""
    first = (api_member.get("first_name") or "").strip()
    last = (api_member.get("last_name") or "").strip()
    pref = (api_member.get("preferred_name") or "").strip()
    name = f"{first} {last}".strip()
    return name or pref


def address_from_member(api_member: dict) -> str:
    """API splits address into 4 fields; we display joined for now."""
    parts = [
        api_member.get("physical_address"),
        api_member.get("suburb"),
        api_member.get("city"),
        api_member.get("postal_code"),
    ]
    return ", ".join(p.strip() for p in parts if p and str(p).strip())


def name_match_score(local_name: str, api_member: dict) -> float:
    """Crude similarity score in [0, 1] used for fuzzy matching when phone-exact
    has multiple candidates. Implementation: case-insensitive token overlap
    (Jaccard). We deliberately keep this dependency-free."""
    local = (local_name or "").lower().split()
    api_full = fullname_from_member(api_member).lower().split()
    if not local or not api_full:
        return 0.0
    a, b = set(local), set(api_full)
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)
