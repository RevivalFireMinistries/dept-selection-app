"""Client for church-manager's event registry.

Events are owned by church-manager — created, administered and reported
there. The portal is the member-facing surface: it lists what's open, takes
registrations, and gives event managers a place to run the registry from
their phone. Everything here goes over the same X-Portal-API-Key channel
that already carries feature config.

Two rules this module exists to enforce:

  * The member's identity is never taken from the request. Callers pass the
    external_member_id resolved from the portal's own signed session, so a
    browser can't register — or read the registry — as somebody else.
  * Failures are returned, not raised. A church-manager outage should show
    "events are unavailable" on the portal, not a 500.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TIMEOUT = int(os.environ.get("CHURCH_MANAGER_TIMEOUT_SECONDS", "10"))


def _base_url() -> str:
    return (os.environ.get("CHURCH_MANAGER_URL") or "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("CHURCH_MANAGER_API_KEY") or ""


def is_configured() -> bool:
    return bool(_base_url() and _api_key())


@dataclass
class Result:
    ok: bool
    data: Any = None
    status: int = 0
    error: Optional[str] = None

    @property
    def unavailable(self) -> bool:
        """True when church-manager couldn't be reached or isn't configured —
        as opposed to a genuine 4xx answer about the request itself."""
        return not self.ok and self.status in (0, 502, 503, 504)


def _request(method: str, path: str, *, params: dict | None = None,
             body: dict | None = None, raw_body: bytes | None = None,
             content_type: str | None = None) -> Result:
    if not is_configured():
        return Result(False, error="church-manager is not configured")

    url = f"{_base_url()}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)

    # Proof-of-payment images go up as a raw body, like the event poster:
    # multipart adds a CSRF surface and buys nothing when the portal is
    # already authenticating with a shared secret.
    if raw_body is not None:
        data = raw_body
        ctype = content_type or "application/octet-stream"
    else:
        data = json.dumps(body).encode() if body is not None else None
        ctype = "application/json"

    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-Portal-API-Key": _api_key(),
            "Content-Type": ctype,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            if resp.status == 204 or not raw:
                return Result(True, data=None, status=resp.status)
            return Result(True, data=json.loads(raw), status=resp.status)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read() or b"{}")
            detail = payload.get("detail") or payload.get("message") or ""
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail)
        except Exception:
            pass
        return Result(False, status=e.code, error=detail or f"HTTP {e.code}")
    except Exception as e:
        logger.warning("[events_client] %s %s failed: %s", method, path, e)
        return Result(False, status=0, error="Could not reach church-manager")


# ── Member-facing ─────────────────────────────────────────────────────────────

def list_open_events(assembly_id: str, member_id: str | None = None) -> Result:
    """Published events still inside their date window."""
    return _request("GET", "/api/portal/events",
                    params={"assembly_id": assembly_id, "member_id": member_id})


def get_event(event_id: str, member_id: str | None = None) -> Result:
    return _request("GET", f"/api/portal/events/{event_id}",
                    params={"member_id": member_id})


def registration_status(event_id: str, member_id: str) -> Result:
    """Whether one named member is already registered for this event."""
    return _request("GET", f"/api/portal/events/{event_id}/registration-status",
                    params={"member_id": member_id})


def dashboard_summary(assembly_id: str, member_id: str | None = None) -> Result:
    """One call for the portal home card: open events, whether this member
    has registered, the countdown, and any announcements they should read."""
    return _request("GET", "/api/portal/events/dashboard/summary",
                    params={"assembly_id": assembly_id, "member_id": member_id})


def list_announcements(event_id: str, member_id: str) -> Result:
    return _request("GET", f"/api/portal/events/{event_id}/announcements",
                    params={"member_id": member_id})


def add_announcement(event_id: str, member_id: str, *, title: str, body: str,
                     is_pinned: bool = False, author_name: str | None = None) -> Result:
    return _request("POST", f"/api/portal/events/{event_id}/announcements",
                    params={"member_id": member_id, "author_name": author_name},
                    body={"title": title, "body": body, "is_pinned": is_pinned})


def delete_announcement(event_id: str, announcement_id: str, member_id: str) -> Result:
    return _request("DELETE",
                    f"/api/portal/events/{event_id}/announcements/{announcement_id}",
                    params={"member_id": member_id})


def register(event_id: str, *, external_member_id: str | None, full_name: str,
             phone: str | None = None, email: str | None = None,
             is_guest: bool = False, notes: str | None = None,
             source: str = "SELF_PORTAL",
             registered_by_member_id: str | None = None,
             registered_by_name: str | None = None) -> Result:
    return _request("POST", f"/api/portal/events/{event_id}/register", body={
        "external_member_id": external_member_id,
        "full_name": full_name,
        "phone": phone,
        "email": email,
        "is_guest": is_guest,
        "notes": notes,
        "source": source,
        "registered_by_member_id": registered_by_member_id,
        "registered_by_name": registered_by_name,
    })


# ── Event-manager surface ─────────────────────────────────────────────────────

def events_i_manage(member_id: str) -> Result:
    """Drives the portal's Events menu item — empty means no menu entry."""
    return _request("GET", "/api/portal/events/managed/list",
                    params={"member_id": member_id})


def registry(event_id: str, member_id: str, *, include_removed: bool = False,
             search: str | None = None, method: str | None = None) -> Result:
    return _request("GET", f"/api/portal/events/{event_id}/registry",
                    params={"member_id": member_id,
                            "include_removed": str(include_removed).lower(),
                            "search": search,
                            "method": method})


def add_payment(event_id: str, registration_id: str, member_id: str, *,
                amount: float, method: str | None = None,
                reference: str | None = None,
                captured_by_name: str | None = None) -> Result:
    return _request(
        "POST",
        f"/api/portal/events/{event_id}/registrations/{registration_id}/payments",
        params={"member_id": member_id},
        body={"amount": amount, "method": method, "reference": reference,
              "captured_by_name": captured_by_name},
    )


def update_registration(event_id: str, registration_id: str, member_id: str,
                        **fields) -> Result:
    return _request("PUT",
                    f"/api/portal/events/{event_id}/registrations/{registration_id}",
                    params={"member_id": member_id},
                    body={k: v for k, v in fields.items() if v is not None})


def remove_registration(event_id: str, registration_id: str, member_id: str,
                        removed_by_name: str | None = None) -> Result:
    return _request("DELETE",
                    f"/api/portal/events/{event_id}/registrations/{registration_id}",
                    params={"member_id": member_id,
                            "removed_by_name": removed_by_name})


def registry_csv_url(event_id: str, member_id: str) -> str:
    """church-manager URL for the CSV. The portal proxies this rather than
    exposing it to the browser, since it carries the shared secret."""
    return (f"{_base_url()}/api/portal/events/{event_id}/registry.csv"
            f"?member_id={urllib.parse.quote(member_id)}")


def poster_url(event_id: str) -> str:
    """Public poster image URL — no key needed, carries no personal data."""
    return f"{_base_url()}/api/events/{event_id}/poster"


# ── Paying for an event ───────────────────────────────────────────────────────

def submit_proof(event_id: str, registration_id: str, *, member_id: str,
                 amount: float, reference: str | None, filename: str | None,
                 data: bytes, content_type: str) -> Result:
    """Upload a member's proof of an EFT. Creates a PENDING payment there."""
    return _request(
        "POST",
        f"/api/portal/events/{event_id}/registrations/{registration_id}/proof",
        params={"amount": amount, "member_id": member_id,
                "reference": reference, "filename": filename},
        raw_body=data, content_type=content_type,
    )


def pending_payments(event_id: str, member_id: str) -> Result:
    """The queue of claims a manager still has to check."""
    return _request("GET", f"/api/portal/events/{event_id}/payments/pending",
                    params={"member_id": member_id})


def confirm_payment(event_id: str, payment_id: str, *, member_id: str,
                    manager_name: str | None = None) -> Result:
    return _request("POST", f"/api/portal/events/{event_id}/payments/{payment_id}/confirm",
                    params={"member_id": member_id, "manager_name": manager_name})


def reject_payment(event_id: str, payment_id: str, *, member_id: str,
                   manager_name: str | None = None, reason: str | None = None) -> Result:
    return _request("POST", f"/api/portal/events/{event_id}/payments/{payment_id}/reject",
                    params={"member_id": member_id, "manager_name": manager_name},
                    body={"reason": reason})


def proof_url(event_id: str, payment_id: str, member_id: str) -> str:
    """Where the portal proxies the proof image from."""
    return (f"{_base_url()}/api/portal/events/{event_id}/payments/{payment_id}/proof"
            f"?{urllib.parse.urlencode({'member_id': member_id})}")


def fetch_proof(event_id: str, payment_id: str, member_id: str):
    """Stream a proof image back. Returns (bytes, content_type) or None."""
    url = proof_url(event_id, payment_id, member_id)
    req = urllib.request.Request(url, headers={"X-Portal-API-Key": _api_key()})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read(), resp.headers.get("Content-Type", "application/octet-stream")
    except Exception as e:
        logger.info("[events_client] could not fetch proof: %s", e)
        return None


def record_gateway_payment(event_id: str, registration_id: str, *, amount: float,
                           reference: str, member_id: str | None,
                           name: str | None) -> Result:
    """Tell church-manager a card payment settled. Idempotent on reference."""
    return _request(
        "POST",
        f"/api/portal/events/{event_id}/registrations/{registration_id}/gateway-payment",
        body={
            "amount": amount,
            "method": "YOCO",
            "reference": reference,
            "note": "Paid by card through the portal",
            "captured_by_member_id": member_id,
            "captured_by_name": name,
        },
    )
