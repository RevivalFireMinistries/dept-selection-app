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


def create_member(payload: dict, *, db=None) -> ApiResult:
    """Create a new member in the central database. Used when an orphan
    genuinely doesn't exist there yet and admin chooses to push them up.
    Caller must include assembly_id, first_name, last_name (required by the
    API's MemberCreate schema)."""
    return _request("POST", "/api/v1/members", db=db, body=payload)


def list_assemblies(*, db=None) -> ApiResult:
    """List assemblies the API key has access to. With a scoped service key,
    only one is returned — that's the calling portal's assembly."""
    return _request("GET", "/api/v1/assemblies", db=db, params={"page": 1, "size": 50})


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

_PHONE_NON_DIGIT = re.compile(r"\D+")
# SA mobile canonical: 10 digits, leading 0, second digit must be 6/7/8 (mobile prefixes 06X/07X/08X)
_SA_MOBILE_CANONICAL_RE = re.compile(r"^0[678]\d{8}$")


def normalise_phone(raw: str) -> str:
    """Strip everything except digits. Good enough for "0712345678" vs
    "071 234 5678" vs "+27 71 234 5678" comparisons (last 9 digits should match)."""
    if not raw:
        return ""
    digits = _PHONE_NON_DIGIT.sub("", raw)
    # Treat South African numbers — drop leading "27" if present so 27821234567
    # matches 0821234567 by the last-9-digit comparison.
    return digits[-9:] if len(digits) >= 9 else digits


def to_sa_canonical_mobile(raw: str) -> str:
    """Convert any reasonable input into canonical SA mobile format '0XXXXXXXXX'
    (10 digits, leading 0). Returns empty string if the number is not a valid
    SA mobile.

    Accepts:
        '0619197741'       ✓ canonical
        '619197741'        ✓ missing leading 0 (we add it)
        '+27619197741'     ✓ international
        '0027619197741'    ✓ international with 00 prefix
        '+27 61 919 7741'  ✓ formatting tolerated
        '0119197741'       ✗ landline (11x), not mobile — rejected
        '619197741X'       ✗ contains a letter
        '12345'            ✗ too short

    Validation rule: SA mobile prefixes are 06X / 07X / 08X (e.g. 060–069,
    071–076, 078–079, 081–084, 087–089). We accept any 0[678] for forward-
    compatibility — the regulator periodically opens new ranges."""
    if not raw:
        return ""
    digits = _PHONE_NON_DIGIT.sub("", str(raw))
    # International prefixes — handle "0027..." → "27..." → "0..."
    if digits.startswith("0027"):
        digits = digits[4:]  # drop the entire "0027" prefix; the 9 leftover
                             # digits will get a leading 0 added below
    elif digits.startswith("27") and len(digits) == 11:
        digits = "0" + digits[2:]
    # Missing leading 0 case (9 digits starting with 6/7/8)
    if len(digits) == 9 and digits[0] in ("6", "7", "8"):
        digits = "0" + digits
    if _SA_MOBILE_CANONICAL_RE.match(digits):
        return digits
    return ""


def is_valid_sa_mobile(raw: str) -> bool:
    """True if `raw` represents a valid SA mobile number in any format."""
    return bool(to_sa_canonical_mobile(raw))


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


def _name_tokens(s: str) -> set:
    """Tokenise a name: lowercased, split on whitespace and hyphens, drop empties.
    Handles 'Mary-Jane', 'Van Der Walt', 'Pastor John Smith' all sensibly."""
    if not s:
        return set()
    return {t for t in re.split(r"[\s\-]+", s.lower().strip()) if t}


# Title prefixes / honorifics — useless as search keywords because they're
# extremely common and not stored in the API's first_name / last_name fields.
_TITLE_TOKENS = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "professor",
    "rev", "reverend", "pastor", "pst", "ps",
    "evangelist", "elder", "deacon", "bishop", "apostle",
    "bro", "brother", "sis", "sister",
}


def _significant_name_tokens(full_name: str) -> list:
    """Return distinct meaningful name tokens for search queries. Drops
    titles and 1-character fragments. Order is preserved (so first/last
    bias is kept) but duplicates are removed."""
    if not full_name:
        return []
    tokens = re.split(r"[\s\-]+", full_name.lower().strip())
    seen = set()
    out = []
    for t in tokens:
        if not t or len(t) < 2 or t in _TITLE_TOKENS:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _smart_titlecase(s: str) -> str:
    """Title-case if the value is all-lowercase or all-uppercase (probably
    meant to be proper case), otherwise leave alone. Preserves things like
    'MacDonald', 'O'Brien', 'van der Walt' that humans wrote intentionally."""
    if not s:
        return s
    if s.islower() or s.isupper():
        return s[:1].upper() + s[1:].lower()
    return s


def derive_first_last_from_full(full_name: str) -> tuple:
    """Best-effort split of a single 'full_name' into (first, last). Used when
    the local portal has only a combined name field but the API expects them
    split. Strips title prefixes, splits on whitespace only (so 'Mary-Jane'
    stays as a single first name), and applies smart titlecasing.

      'Pastor Alfred John'  -> ('Alfred', 'John')
      'mary-jane smith'     -> ('Mary-jane', 'Smith')   (kept hyphenated)
      'MacDonald'           -> ('MacDonald', '')
      ''                    -> ('', '')
    """
    if not full_name:
        return ("", "")
    parts = full_name.strip().split()
    while parts and parts[0].lower().rstrip(".") in _TITLE_TOKENS:
        parts.pop(0)
    if not parts:
        return ("", "")
    first = _smart_titlecase(parts[0])
    last = _smart_titlecase(parts[-1]) if len(parts) > 1 else ""
    return (first, last)


def names_match_strict(local_full_name: str, api_member: dict) -> bool:
    """True if EITHER the API first_name OR the API last_name fully appears as
    tokens inside the local full_name. (Per user rule: phone match plus at
    least one of first/last matching → match. Tolerates hyphenated names,
    title prefixes like "Pastor", marital surname changes, missing middle
    names, etc.) Case- and hyphen-insensitive."""
    local = _name_tokens(local_full_name)
    first = _name_tokens(api_member.get("first_name"))
    last = _name_tokens(api_member.get("last_name"))
    if not first and not last:
        return False
    first_ok = bool(first) and first.issubset(local)
    last_ok = bool(last) and last.issubset(local)
    return first_ok or last_ok


# ---------------------------------------------------------------------------
# Matching service — figures out which API member a given local member is
# ---------------------------------------------------------------------------

# Tuned thresholds. Raise NAME_AUTO_THRESHOLD if we see false-positives.
NAME_AUTO_THRESHOLD = 0.7   # auto-confirm when name score >= this
NAME_CANDIDATE_THRESHOLD = 0.4  # show as candidate when name score >= this


def match_local_member(
    local_member,
    *,
    db=None,
    page_size: int = 50,
) -> dict:
    """Try to find the rfm-database equivalent of a local Member row.

    The user-stated rule:
      * Phone matches AND first+last names match  →  matched
      * Phone matches AND names disagree           →  ambiguous (admin review)
      * Phone is missing on the API side but names match  →  matched
        (and the caller should push our phone up to enrich the API record)
      * No phone or name match anywhere            →  unmatched

    Returns a dict:
      status:        'matched' | 'ambiguous' | 'unmatched' | 'disabled' | 'error'
      external_id:   UUID string when status='matched'
      assembly_id:   UUID string when status='matched'
      candidates:    list of API member dicts (for the admin review panel)
      reason:        short human-readable explanation
      enrich_hint:   dict of {field: value} that the caller should PATCH up
                     (only when status='matched'; e.g. when API was missing
                     phone or email and we have it locally)
      error:         only when status='error'
    """
    if not is_enabled(db):
        return {"status": "disabled", "reason": "rfm-db integration disabled"}

    phone_raw = (getattr(local_member, "phone", None) or "")
    phone_norm = normalise_phone(phone_raw)
    name = (getattr(local_member, "full_name", None) or "")
    email = (getattr(local_member, "email", None) or "").strip()

    # We always run the phone search (when we have a phone) AND a name search,
    # so we catch the "phone changed in central DB" and "phone missing in
    # central DB" cases too. Cost: 1-2 paginated requests per local member.
    raw_phone_hits = []
    if phone_norm:
        r = search_members(phone=phone_norm, page=1, size=page_size, db=db)
        if r.disabled:
            return {"status": "disabled", "reason": r.error}
        if not r.ok:
            return {"status": "error", "error": r.error or f"HTTP {r.status}"}
        raw_phone_hits = r.data or []
        if isinstance(raw_phone_hits, dict):
            raw_phone_hits = raw_phone_hits.get("data") or []

    # Name search: the API does substring match within each field independently
    # (first_name ILIKE %q% OR last_name ILIKE %q% OR ...). Searching with the
    # full name "Alfred john" therefore finds nothing because neither field
    # contains the whole string. Search with each significant token instead so
    # "Alfred" finds first_name=Alfred, "john" finds last_name=John, then we
    # union the results.
    raw_name_hits = []
    seen_name_ids = set()
    for token in _significant_name_tokens(name):
        r = search_members(search=token, page=1, size=page_size, db=db)
        if not r.ok or not r.data:
            continue
        items = r.data
        if isinstance(items, dict):
            items = items.get("data") or []
        for it in items:
            mid = it.get("id")
            if mid and mid not in seen_name_ids:
                seen_name_ids.add(mid)
                raw_name_hits.append(it)

    # Combine + dedupe by id
    seen = set()
    all_hits = []
    for m in (raw_phone_hits + raw_name_hits):
        mid = m.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            all_hits.append(m)

    # ---- Step 1: phone-exact bucket ----
    phone_exact = [m for m in all_hits if phone_norm and normalise_phone(m.get("phone") or "") == phone_norm]

    if phone_exact:
        # Apply strict first+last name rule
        full_match = [m for m in phone_exact if names_match_strict(name, m)]
        if len(full_match) == 1:
            best = full_match[0]
            return {
                "status": "matched",
                "external_id": best.get("id"),
                "assembly_id": best.get("assembly_id"),
                "candidates": [_score_and_strip(best, 1.0)],
                "reason": "Phone + first/last name match",
                "enrich_hint": _enrich_diff(best, email=email),  # phone already matches
            }
        if len(full_match) > 1:
            scored = sorted(
                (_score_and_strip(m, name_match_score(name, m)) for m in full_match),
                key=lambda x: x["_score"], reverse=True,
            )
            return {
                "status": "ambiguous",
                "candidates": scored,
                "reason": f"{len(full_match)} candidates share phone AND names — admin review",
            }
        # Phone matched but neither candidate has matching first+last — admin review
        scored = sorted(
            (_score_and_strip(m, name_match_score(name, m)) for m in phone_exact),
            key=lambda x: x["_score"], reverse=True,
        )
        return {
            "status": "ambiguous",
            "candidates": scored,
            "reason": "Phone matches but first/last name do not — admin review",
        }

    # ---- Step 2: no phone match — name-only matches go to AMBIGUOUS ----
    # Per user rule: auto-confirming purely on names is too risky (different
    # people share names; phones change rarely). When the phone doesn't match,
    # surface every name-match as a candidate and let an admin manually pick.
    # If the admin picks one, the confirm endpoint will run enrichment so the
    # API gets our phone if it was missing there.
    name_match_hits = [m for m in all_hits if names_match_strict(name, m)]

    if name_match_hits:
        scored = sorted(
            (_score_and_strip(m, name_match_score(name, m)) for m in name_match_hits),
            key=lambda x: x["_score"], reverse=True,
        )
        if len(name_match_hits) == 1:
            api_phone = (name_match_hits[0].get("phone") or "").strip()
            reason = (
                "Names match but phone is missing in central database — admin should confirm"
                if not api_phone else
                "Names match but phone differs from local — admin should confirm"
            )
        else:
            reason = f"{len(name_match_hits)} different people with these names — admin must pick"
        return {
            "status": "ambiguous",
            "candidates": scored,
            "reason": reason,
        }

    # ---- Step 3: no exact match — surface fuzzy candidates for the orphan view ----
    fuzzy = sorted(
        (_score_and_strip(m, name_match_score(name, m)) for m in all_hits),
        key=lambda x: x["_score"], reverse=True,
    )
    fuzzy = [c for c in fuzzy if c["_score"] >= NAME_CANDIDATE_THRESHOLD][:5]

    return {
        "status": "unmatched",
        "candidates": fuzzy,
        "reason": "No member with this phone or name in central database",
    }


def _enrich_diff(api_member: dict, *, phone: str = "", email: str = "") -> dict:
    """Return only the fields the API is missing where we have a valid value.
    Per user-stated rules:
      * Phone is only enriched when the local value is a valid SA mobile
        AND the API record has no phone. We push the canonical 0XXXXXXXXX
        form so the central database stays consistent.
      * Email is enriched when the API field is empty and we have a non-
        empty local value (very light validation — non-empty + has '@').
    """
    updates = {}
    api_phone = (api_member.get("phone") or "").strip()
    api_email = (api_member.get("email") or "").strip()

    if phone and not api_phone:
        canonical = to_sa_canonical_mobile(phone)
        if canonical:
            updates["phone"] = canonical
        # If local phone is invalid we silently skip — never push junk into the
        # central database. The admin sees the local member is matched; the
        # local invalid phone stays in the local table.

    if email and not api_email:
        clean = email.strip()
        if "@" in clean and "." in clean.split("@")[-1]:
            updates["email"] = clean

    return updates


def push_enrichment(api_member_id: str, fields: dict, *, db=None) -> ApiResult:
    """Convenience: PATCH only the enrichment fields back to the API.
    Returns the same ApiResult contract as other client functions so callers
    can log success/failure but never need to handle exceptions."""
    if not fields:
        return ApiResult(ok=True, data={"updated": False, "reason": "nothing to enrich"})
    return update_member(api_member_id, fields, db=db)


def _clean_phone_for_display(raw: str) -> str:
    """API phone for display/storage: canonical SA mobile if possible, else
    just the digits with all whitespace stripped. Never include spaces."""
    if not raw:
        return ""
    canonical = to_sa_canonical_mobile(raw)
    if canonical:
        return canonical
    # Not a valid SA mobile — strip whitespace so 'foo 123' becomes 'foo123'
    return re.sub(r"\s+", "", str(raw))


def _score_and_strip(api_member: dict, score: float) -> dict:
    """Return a UI-friendly subset of the API member with the score attached.
    The full payload is large; we only need a few fields for review."""
    return {
        "id": api_member.get("id"),
        "assembly_id": api_member.get("assembly_id"),
        "first_name": api_member.get("first_name"),
        "last_name": api_member.get("last_name"),
        "full_name": fullname_from_member(api_member),
        "phone": _clean_phone_for_display(api_member.get("phone")),
        "email": (api_member.get("email") or "").strip(),
        "address": address_from_member(api_member),
        "membership_status": api_member.get("membership_status"),
        "_score": round(score, 3),
    }
