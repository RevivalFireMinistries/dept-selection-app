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


def _registration_url(request: Request, event_id: str, db: Session) -> str:
    """The public link people scan or tap to register.

    Built from the incoming request so it's correct on localhost, on a
    Railway domain, and behind a custom domain — without another env var to
    keep in step.
    """
    from routers.payments import _absolute_base

    # Same reasoning as the checkout URLs: this ends up in a QR code and a
    # WhatsApp message, so it must be the public address rather than
    # whatever origin the request happened to arrive on.
    return f"{_absolute_base(request, db)}/events/{event_id}"


@api_router.get("/{event_id}/share")
def share_links(event_id: str, request: Request = None, db: Session = Depends(get_db)):
    """Everything the share sheet needs: the link, a ready-made WhatsApp
    message, and the QR image URL."""
    member = _member(request, db)
    data = _unwrap(events_client.get_event(event_id, _external_id(member)))
    url = _registration_url(request, event_id, db)

    # The dates the event RUNS on, not the registration window. Sharing
    # "1 August to 20 September" for a three-day camp is how this read
    # before run dates existed; runs_from/runs_to fall back to the
    # registration window for events that predate them.
    when = data.get("runs_from") or data.get("start_date", "")
    runs_to = data.get("runs_to") or data.get("end_date")
    if runs_to and runs_to != when:
        when = f"{when} to {runs_to}"
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
def qr_code(event_id: str, request: Request = None, download: bool = False,
            db: Session = Depends(get_db)):
    """QR code for the registration link, as a PNG.

    Rendered server-side rather than with a JS library so the file can be
    handed straight to the Web Share API (which needs a real File), saved,
    or printed on a poster.
    """
    import io

    import qrcode

    url = _registration_url(request, event_id, db)
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


# ── Paying for an event ───────────────────────────────────────────────────────
#
# Two routes for a member's money. A card payment goes through the portal's
# existing Yoco checkout and lands already settled. An EFT is a claim until
# a manager has seen it in the bank, so the upload creates a pending payment
# and the manager's confirm step is what makes it count.


@api_router.post("/{event_id}/pay")
def start_card_payment(event_id: str, payload: dict = Body(default={}),
                       request: Request = None, db: Session = Depends(get_db)):
    """Begin a Yoco checkout for what this member still owes on an event.

    The amount is taken from the registration's balance rather than from the
    request: letting the browser name its own price is how someone pays R1
    for a R500 camp.
    """
    import yoco
    from models import PaymentTransaction

    member = _member(request, db)
    ext = _external_id(member)
    if not member or not ext:
        raise HTTPException(status_code=401, detail="Please sign in")

    data = _unwrap(events_client.get_event(event_id, ext))
    if "YOCO" not in (data.get("payment_methods") or []):
        raise HTTPException(status_code=400,
                            detail="Card payment isn't offered for this event")

    reg = data.get("my_registration")
    if not reg:
        raise HTTPException(status_code=400, detail="Register for the event first")

    cost = float(data.get("cost") or 0)
    paid = float(reg.get("amount_paid") or 0)
    outstanding = max(0.0, cost - paid)
    if outstanding <= 0:
        raise HTTPException(status_code=400, detail="Nothing outstanding on this event")

    # A part-payment is allowed, but never more than is owed.
    try:
        requested = float(payload.get("amount") or outstanding)
    except (TypeError, ValueError):
        requested = outstanding
    amount = min(max(requested, 1.0), outstanding)
    amount_cents = int(round(amount * 100))
    if amount_cents < 100:
        raise HTTPException(
            status_code=400,
            detail="Card payments start at R1.00 — please pay the balance another way.",
        )

    secret_key = yoco.get_setting(db, "yoco_secret_key")
    if not secret_key:
        raise HTTPException(
            status_code=503,
            detail="Card payments aren't set up yet — please use the bank details shown.",
        )

    # Yoco has to be able to reach these, and behind Railway's proxy
    # request.base_url is the INTERNAL origin — http://…:8080 — which Yoco
    # rejects. _absolute_base prefers the admin-configured portal_base_url
    # and otherwise rebuilds from the x-forwarded-* headers, which is why
    # the giving flow has always worked where this did not.
    from routers.payments import _absolute_base

    base = _absolute_base(request, db)
    try:
        checkout = yoco.create_checkout(
            secret_key=secret_key,
            amount_cents=amount_cents,
            # Shaped to match the giving checkout, which has always worked:
            # plain paths with no query string, and camelCase metadata keys.
            # This call differed from that one in exactly those two ways and
            # got a 400 back. Which of the two Yoco actually objected to is
            # unconfirmed — their docs were not reachable to check — so both
            # were brought into line rather than guessing at one.
            success_url=f"{base}/events/{event_id}",
            cancel_url=f"{base}/events/{event_id}",
            failure_url=f"{base}/events/{event_id}",
            metadata={
                "kind": "event",
                "eventId": event_id,
                "registrationId": reg["id"],
                "externalMemberId": ext,
            },
        )
    except yoco.YocoError as e:
        logger.warning("[events] Yoco checkout failed (base=%s, amount=%s): %s",
                       base, amount_cents, e)
        raise HTTPException(status_code=502, detail="Could not start the card payment")

    # Recorded before the redirect so the webhook has something to match
    # against whichever way the member's browser goes next.
    txn = PaymentTransaction(
        member_id=member.id,
        external_reference=checkout.checkout_id,
        provider="yoco",
        status="pending",
        amount_cents=amount_cents,
        currency=data.get("currency") or "ZAR",
        category="EVENT",
        custom_label=data.get("title"),
        event_id=event_id,
        event_registration_id=reg["id"],
    )
    db.add(txn)
    db.commit()

    return {"redirect_url": checkout.redirect_url, "amount": amount}


@api_router.post("/{event_id}/proof")
async def upload_proof_of_payment(event_id: str, request: Request,
                                  amount: float = Query(..., gt=0),
                                  reference: str | None = Query(None),
                                  filename: str | None = Query(None),
                                  db: Session = Depends(get_db)):
    """A member uploads proof of an EFT.

    The image is forwarded as a raw body, same as the event poster — see
    events_client._request for why multipart is avoided here.
    """
    member = _member(request, db)
    ext = _external_id(member)
    if not member or not ext:
        raise HTTPException(status_code=401, detail="Please sign in")

    data = _unwrap(events_client.get_event(event_id, ext))
    reg = data.get("my_registration")
    if not reg:
        raise HTTPException(status_code=400, detail="Register for the event first")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="No file received")
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()

    return _unwrap(events_client.submit_proof(
        event_id, reg["id"], member_id=ext, amount=amount,
        reference=reference, filename=filename,
        data=body, content_type=content_type,
    ))


@api_router.get("/{event_id}/payments/pending")
def list_pending_payments(event_id: str, request: Request = None,
                          db: Session = Depends(get_db)):
    """The manager's queue of claims to check against the bank."""
    member, ext = _require_manager_identity(request, db)
    return _unwrap(events_client.pending_payments(event_id, ext))


@api_router.get("/{event_id}/payments/{payment_id}/proof")
def view_proof(event_id: str, payment_id: str, request: Request = None,
               db: Session = Depends(get_db)):
    """Stream the uploaded proof to the manager.

    Proxied rather than linked so the shared secret never reaches the page,
    and so church-manager can keep checking that the viewer really manages
    this event.
    """
    member, ext = _require_manager_identity(request, db)
    fetched = events_client.fetch_proof(event_id, payment_id, ext)
    if not fetched:
        raise HTTPException(status_code=404, detail="No proof available")
    body, content_type = fetched
    return Response(content=body, media_type=content_type,
                    headers={"Cache-Control": "private, max-age=300"})


@api_router.post("/{event_id}/payments/{payment_id}/confirm")
def confirm_payment(event_id: str, payment_id: str, request: Request = None,
                    db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    return _unwrap(events_client.confirm_payment(
        event_id, payment_id, member_id=ext, manager_name=member.full_name))


@api_router.post("/{event_id}/payments/{payment_id}/reject")
def reject_payment(event_id: str, payment_id: str, payload: dict = Body(default={}),
                   request: Request = None, db: Session = Depends(get_db)):
    member, ext = _require_manager_identity(request, db)
    return _unwrap(events_client.reject_payment(
        event_id, payment_id, member_id=ext, manager_name=member.full_name,
        reason=(payload.get("reason") or "").strip() or None))
