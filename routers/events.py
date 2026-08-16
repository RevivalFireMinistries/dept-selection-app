"""Portal events — pages and the API behind them.

church-manager owns events; this router is the member-facing surface. It
does three jobs:

  1. Serves the event list, the registration page and the manager registry
     view as HTML.
  2. Proxies the browser's calls through to church-manager, injecting the
     member's identity server-side. The browser never sees the shared
     secret, and — more importantly — never gets to choose which member it
     is acting as. Every identity below comes from the portal's own signed
     session cookie.
  3. Streams the registry CSV so the download works without handing the
     shared secret to the page.

Guest lookups hit rfm-database through the existing directory search, so a
visitor who isn't logged in can still find themselves by name rather than
typing a duplicate record.
"""
from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

import events_client
import rfm_api_client as _rfm
from database import get_db
from models import Member

logger = logging.getLogger(__name__)

router = APIRouter()
api_router = APIRouter(prefix="/api/events", tags=["events"])


def _member(request: Request, db: Session) -> Optional[Member]:
    from routers.pages import get_current_member
    return get_current_member(request, db)


def _external_id(member: Optional[Member]) -> Optional[str]:
    """The member's central UUID — their identity everywhere in this stack."""
    return getattr(member, "external_member_id", None) if member else None


def _assembly_id(request: Request, member: Optional[Member]) -> Optional[str]:
    if member and getattr(member, "external_assembly_id", None):
        return str(member.external_assembly_id)
    assembly = getattr(request.state, "assembly", None) or {}
    return assembly.get("id")


def _unwrap(result: events_client.Result):
    """Turn a client Result into data, or raise the right HTTP error."""
    if result.ok:
        return result.data
    if result.unavailable:
        raise HTTPException(
            status_code=503,
            detail=result.error or "Events are temporarily unavailable — please try again shortly",
        )
    raise HTTPException(status_code=result.status or 400, detail=result.error or "Request failed")


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("/events")
async def events_page(request: Request, db: Session = Depends(get_db)):
    """Open events. Public — a visitor who isn't logged in can still see
    what's on and register as a guest."""
    from routers.pages import templates, _require_feature
    _require_feature(request, "events")
    return templates.TemplateResponse("events.html", {"request": request})


@router.get("/events/{event_id}")
async def event_detail_page(event_id: str, request: Request, db: Session = Depends(get_db)):
    from routers.pages import templates, _require_feature
    _require_feature(request, "events")
    return templates.TemplateResponse(
        "event_detail.html", {"request": request, "event_id": event_id}
    )


@router.get("/events/{event_id}/manage")
async def event_manage_page(event_id: str, request: Request, db: Session = Depends(get_db)):
    """Registry view for event managers. Membership of that role is checked
    server-side by church-manager on every call the page makes."""
    from routers.pages import templates, _require_feature
    _require_feature(request, "events")
    member = _member(request, db)
    if not member:
        return RedirectResponse(url=f"/?next=/events/{event_id}/manage", status_code=302)
    return templates.TemplateResponse(
        "event_manage.html", {"request": request, "event_id": event_id}
    )


# ── API ───────────────────────────────────────────────────────────────────────

@api_router.get("")
def list_events(request: Request, db: Session = Depends(get_db)):
    member = _member(request, db)
    assembly_id = _assembly_id(request, member)
    if not assembly_id:
        # No assembly context (logged-out visitor on a single-assembly
        # deployment) — nothing to scope events to.
        return {"events": [], "signed_in": bool(member)}
    data = _unwrap(events_client.list_open_events(assembly_id, _external_id(member)))
    return {
        "events": data.get("events", []),
        "signed_in": bool(member),
        "me": {
            "external_member_id": _external_id(member),
            "full_name": member.full_name if member else None,
            "email": member.email if member else None,
            "phone": member.phone if member else None,
        } if member else None,
    }


@api_router.get("/dashboard/summary")
def dashboard_summary(request: Request, db: Session = Depends(get_db)):
    """Feeds the portal home card.

    Never raises: the dashboard must render even when church-manager is
    down, so a failure here degrades to "no events" rather than breaking
    the whole page.
    """
    member = _member(request, db)
    assembly_id = _assembly_id(request, member)
    if not assembly_id:
        return {"events": []}
    result = events_client.dashboard_summary(assembly_id, _external_id(member))
    if not result.ok:
        logger.info("[events] dashboard summary unavailable: %s", result.error)
        return {"events": []}
    return result.data


@api_router.get("/mine/managed")
def my_managed_events(request: Request, db: Session = Depends(get_db)):
    """Events this member manages — the portal menu uses this to decide
    whether to show the Events management entry at all."""
    member = _member(request, db)
    ext = _external_id(member)
    if not ext:
        return {"events": []}
    result = events_client.events_i_manage(ext)
    if not result.ok:
        return {"events": []}   # never break the menu over a manager lookup
    return result.data


@api_router.get("/{event_id}")
def get_event(event_id: str, request: Request, db: Session = Depends(get_db)):
    member = _member(request, db)
    data = _unwrap(events_client.get_event(event_id, _external_id(member)))
    data["signed_in"] = bool(member)
    if member:
        data["me"] = {
            "external_member_id": _external_id(member),
            "full_name": member.full_name,
            "email": member.email,
            "phone": member.phone,
        }
    return data


@api_router.get("/{event_id}/directory-search")
def directory_search(event_id: str, q: str = Query(..., min_length=2),
                     request: Request = None, db: Session = Depends(get_db)):
    """Find yourself in rfm-database when you're not logged in.

    Deliberately returns only a name and a masked phone — enough to
    recognise yourself, not enough to harvest the directory.
    """
    result = _rfm.search_members(search=q, page=1, size=10, db=db)
    if not result.ok or not result.data:
        return {"results": []}
    items = result.data if isinstance(result.data, list) else (result.data.get("data") or [])
    out = []
    for item in items:
        phone = (item.get("phone") or "").strip()
        masked = f"{phone[:5]}…{phone[-2:]}" if len(phone) > 7 else ""
        out.append({
            "external_member_id": item.get("id"),
            "full_name": _rfm.fullname_from_member(item),
            "phone_hint": masked,
        })
    return {"results": out}


@api_router.get("/{event_id}/registration-status")
def registration_status(event_id: str, member_id: str = Query(..., min_length=8),
                        request: Request = None, db: Session = Depends(get_db)):
    """Used by the registration page after someone picks themselves out of
    the directory, so we can tell them they're already on the list rather
    than letting them fill in a form that will 409."""
    result = events_client.registration_status(event_id, member_id)
    if not result.ok:
        # Not knowing is not an error — fall through to the normal form and
        # let the register call be the authority.
        return {"registered": False}
    return result.data


@api_router.post("/{event_id}/register")
def register(event_id: str, payload: dict = Body(...), request: Request = None,
             db: Session = Depends(get_db)):
    """Register for an event.

    A signed-in member always registers as themselves — the payload cannot
    override that. Everyone else either claims a directory record they
    found by name, or registers as a guest.
    """
    member = _member(request, db)
    ext = _external_id(member)

    if member:
        full_name = member.full_name
        email = member.email or payload.get("email")
        phone = member.phone or payload.get("phone")
        is_guest = False
    else:
        ext = (payload.get("external_member_id") or "").strip() or None
        full_name = (payload.get("full_name") or "").strip()
        email = (payload.get("email") or "").strip() or None
        phone = (payload.get("phone") or "").strip() or None
        is_guest = not ext
        if not full_name:
            raise HTTPException(status_code=400, detail="Your name is required")

    return _unwrap(events_client.register(
        event_id,
        external_member_id=ext,
        full_name=full_name,
        phone=phone,
        email=email,
        is_guest=is_guest,
        notes=payload.get("notes"),
        source="SELF_PORTAL",
    ))


# ── Event-manager actions ─────────────────────────────────────────────────────

def _require_manager_identity(request: Request, db: Session) -> tuple[Member, str]:
    member = _member(request, db)
    ext = _external_id(member)
    if not member or not ext:
        raise HTTPException(status_code=401, detail="Please sign in")
    return member, ext


@api_router.get("/{event_id}/registry")
def registry(event_id: str, include_removed: bool = False, search: str | None = None,
             request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    return _unwrap(events_client.registry(
        event_id, ext, include_removed=include_removed, search=search
    ))


@api_router.post("/{event_id}/registry/add")
def manager_add_person(event_id: str, payload: dict = Body(...),
                       request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    full_name = (payload.get("full_name") or "").strip()
    if not full_name:
        raise HTTPException(status_code=400, detail="Name is required")
    external = (payload.get("external_member_id") or "").strip() or None
    return _unwrap(events_client.register(
        event_id,
        external_member_id=external,
        full_name=full_name,
        phone=(payload.get("phone") or "").strip() or None,
        email=(payload.get("email") or "").strip() or None,
        is_guest=not external,
        notes=payload.get("notes"),
        source="EVENT_MANAGER",
        registered_by_member_id=ext,
        registered_by_name=member.full_name,
    ))


@api_router.post("/{event_id}/registry/{registration_id}/payment")
def manager_add_payment(event_id: str, registration_id: str, payload: dict = Body(...),
                        request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    try:
        amount = float(payload.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="A valid amount is required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    return _unwrap(events_client.add_payment(
        event_id, registration_id, ext,
        amount=amount,
        method=payload.get("method"),
        reference=payload.get("reference"),
        captured_by_name=member.full_name,
    ))


@api_router.put("/{event_id}/registry/{registration_id}")
def manager_update(event_id: str, registration_id: str, payload: dict = Body(...),
                   request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    return _unwrap(events_client.update_registration(
        event_id, registration_id, ext,
        status=payload.get("status"),
        phone=payload.get("phone"),
        email=payload.get("email"),
        notes=payload.get("notes"),
    ))


@api_router.delete("/{event_id}/registry/{registration_id}")
def manager_remove(event_id: str, registration_id: str,
                   request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    return _unwrap(events_client.remove_registration(
        event_id, registration_id, ext, removed_by_name=member.full_name
    ))


@api_router.get("/{event_id}/announcements")
def list_announcements(event_id: str, request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    return _unwrap(events_client.list_announcements(event_id, ext))


@api_router.post("/{event_id}/announcements")
def add_announcement(event_id: str, payload: dict = Body(...),
                     request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    title = (payload.get("title") or "").strip()
    body = (payload.get("body") or "").strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="A title and a message are both required")
    return _unwrap(events_client.add_announcement(
        event_id, ext, title=title, body=body,
        is_pinned=bool(payload.get("is_pinned")),
        author_name=member.full_name,
    ))


@api_router.delete("/{event_id}/announcements/{announcement_id}")
def delete_announcement(event_id: str, announcement_id: str,
                        request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    _unwrap(events_client.delete_announcement(event_id, announcement_id, ext))
    return {"deleted": True}


@api_router.get("/{event_id}/registry.csv")
def registry_csv(event_id: str, request: Request = None, db: Session = Depends(get_db)):
    """Stream the CSV from church-manager.

    Proxied rather than linked so the shared secret stays server-side and
    the manager check still happens upstream.
    """
    member, ext = _require_manager_identity(request, db)
    url = events_client.registry_csv_url(event_id, ext)
    req = urllib.request.Request(
        url, headers={"X-Portal-API-Key": events_client._api_key()}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            disposition = resp.headers.get(
                "Content-Disposition", 'attachment; filename="registry.csv"'
            )
    except Exception as e:
        logger.warning("[events] CSV proxy failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not fetch the registry export")
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition": disposition})


def _registration_url(request: Request, event_id: str) -> str:
    """The public link people scan or tap to register.

    Built from the incoming request so it's correct on localhost, on a
    Railway domain, and behind a custom domain — without another env var to
    keep in step.
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/events/{event_id}"


@api_router.get("/{event_id}/share")
def share_links(event_id: str, request: Request = None, db: Session = Depends(get_db)):
    """Everything the share sheet needs: the link, a ready-made WhatsApp
    message, and the QR image URL."""
    member = _member(request, db)
    data = _unwrap(events_client.get_event(event_id, _external_id(member)))
    url = _registration_url(request, event_id)

    when = data.get("start_date", "")
    if data.get("end_date") and data["end_date"] != when:
        when = f"{when} to {data['end_date']}"
    cost = data.get("cost")
    lines = [
        f"*{data.get('title', 'Event')}*",
        f"🗓 {when}",
    ]
    if data.get("venue"):
        lines.append(f"📍 {data['venue']}")
    if cost:
        lines.append(f"💰 {data.get('currency', 'ZAR')} {float(cost):,.2f} per person")
    lines += ["", "Register here:", url]
    message = "\n".join(lines)

    return {
        "url": url,
        "title": data.get("title"),
        "message": message,
        # wa.me with no number opens the contact picker, so it works on
        # both mobile and WhatsApp Web.
        "whatsapp_url": "https://wa.me/?text=" + urllib.parse.quote(message),
        "qr_url": f"/api/events/{event_id}/qr.png",
    }


@api_router.get("/{event_id}/qr.png")
def qr_code(event_id: str, request: Request = None, download: bool = False):
    """QR code for the registration link, as a PNG.

    Rendered server-side rather than with a JS library so the file can be
    handed straight to the Web Share API (which needs a real File), saved,
    or printed on a poster.
    """
    import io

    import qrcode

    url = _registration_url(request, event_id)
    img = qrcode.make(url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    headers = {"Cache-Control": "public, max-age=3600"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="event-{event_id[:8]}-qr.png"'
    return Response(content=buf.getvalue(), media_type="image/png", headers=headers)


@api_router.get("/{event_id}/poster")
def poster(event_id: str):
    """Proxy the poster so the page never needs church-manager's URL."""
    try:
        with urllib.request.urlopen(events_client.poster_url(event_id), timeout=15) as resp:
            return Response(
                content=resp.read(),
                media_type=resp.headers.get("Content-Type", "image/png"),
                headers={"Cache-Control": "public, max-age=3600"},
            )
    except Exception:
        raise HTTPException(status_code=404, detail="No poster for this event")
