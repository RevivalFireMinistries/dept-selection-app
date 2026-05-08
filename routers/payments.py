"""Online payments via Yoco Checkout API.

Two endpoints:
  POST /api/payments/checkout            -> create Yoco checkout, return redirect URL
  POST /api/payments/yoco/webhook        -> receive Yoco webhook, mark transaction
                                            captured/failed, push to central DB

The webhook is the *source of truth* for status. The success/cancel/failure
return pages just trigger UI — they don't change DB state.
"""
from __future__ import annotations

import json
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
import yoco
import rfm_api_client as _rfm
from models import Member, PaymentTransaction, Settings


router = APIRouter()


VALID_CATEGORIES = {"TITHE", "OFFERING", "RENT_PARTNERSHIP", "TOUCH_OF_FIRE", "OTHER"}


def _setting(db: Session, key: str) -> str:
    row = db.query(Settings).filter(Settings.key == key).first()
    return (row.value or "").strip() if row and row.value else ""


def _absolute_base(request: Request, db: Session) -> str:
    """Resolve the public base URL for return links. Prefer an admin-configured
    portal_base_url Setting; fall back to the incoming request's origin."""
    base = _setting(db, "portal_base_url")
    if base:
        return base.rstrip("/")
    # Reconstruct from the request — works behind Railway's proxy
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


@router.post("/payments/checkout")
def create_checkout(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Member-authed: build a Yoco hosted checkout for the logged-in member."""
    from routers.pages import get_current_member
    member = get_current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")
    if not member.external_member_id:
        raise HTTPException(
            status_code=400,
            detail="Your record isn't linked to the central database yet. Ask an admin to run Member Sync.",
        )

    secret_key = _setting(db, "yoco_secret_key")
    if not secret_key:
        raise HTTPException(status_code=503, detail="Online payments aren't configured yet. Please use the bank details below.")

    category = (payload.get("category") or "").strip().upper()
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Pick a category: {', '.join(sorted(VALID_CATEGORIES))}")

    custom_label = (payload.get("custom_label") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None

    # Amount: accept rands (float) and convert to cents — guard against weird floats
    try:
        amount_rand = float(payload.get("amount") or 0)
    except (TypeError, ValueError):
        amount_rand = 0
    amount_cents = int(round(amount_rand * 100))
    if amount_cents < 100:  # R1 minimum
        raise HTTPException(status_code=400, detail="Amount must be at least R1.00")
    if amount_cents > 1_000_000 * 100:  # R1,000,000 sanity cap
        raise HTTPException(status_code=400, detail="Amount is too large; please contact the church office.")

    base = _absolute_base(request, db)
    success_url = f"{base}/portal/payment/success"
    cancel_url = f"{base}/portal/payment/cancel"
    failure_url = f"{base}/portal/payment/failure"

    try:
        result = yoco.create_checkout(
            secret_key=secret_key,
            amount_cents=amount_cents,
            success_url=success_url,
            cancel_url=cancel_url,
            failure_url=failure_url,
            metadata={
                "memberId": str(member.id),
                "externalMemberId": str(member.external_member_id),
                "category": category,
            },
        )
    except yoco.YocoError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # CRITICAL: write the pending row BEFORE returning so the webhook can
    # reconcile by external_reference, no matter how fast it arrives.
    txn = PaymentTransaction(
        member_id=member.id,
        external_reference=result.checkout_id,
        provider="yoco",
        status="pending",
        amount_cents=amount_cents,
        currency="ZAR",
        category=category,
        custom_label=custom_label,
        notes=notes,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

    return {
        "checkout_id": result.checkout_id,
        "redirect_url": result.redirect_url,
        "status": result.status,
        "transaction_id": txn.id,
    }


@router.get("/payments/transactions/{txn_id}")
def get_transaction_status(txn_id: int, request: Request, db: Session = Depends(get_db)):
    """Member-authed status check used by the success page to poll for the
    webhook landing. Members only see their own transactions."""
    from routers.pages import get_current_member
    member = get_current_member(request, db)
    if not member:
        raise HTTPException(status_code=401, detail="Please log in")
    txn = db.query(PaymentTransaction).filter(PaymentTransaction.id == txn_id).first()
    if not txn or txn.member_id != member.id:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {
        "id": txn.id,
        "status": txn.status,
        "amount_cents": txn.amount_cents,
        "currency": txn.currency,
        "category": txn.category,
        "custom_label": txn.custom_label,
        "central_pushed_at": txn.central_pushed_at.isoformat() if txn.central_pushed_at else None,
        "central_push_error": txn.central_push_error,
        "updated_at": txn.updated_at.isoformat() if txn.updated_at else None,
    }


def _push_to_central(txn: PaymentTransaction, db: Session) -> None:
    """Best-effort: record the captured contribution in the central rfm-database.
    Failures are logged on the txn so an admin can retry; webhook still 200s."""
    if txn.central_contribution_id:
        return  # already pushed (idempotency on webhook retries)
    if not _rfm.is_enabled(db) or not _rfm.is_configured(db):
        txn.central_push_error = "Central API integration disabled"
        return

    member = db.query(Member).filter(Member.id == txn.member_id).first() if txn.member_id else None
    external_member_id = member.external_member_id if member else None
    if not external_member_id:
        txn.central_push_error = "Member is not linked to the central database"
        return

    payload = {
        "member_id": str(external_member_id),
        "category": txn.category,
        "amount": float(txn.amount_cents) / 100.0,
        "currency": txn.currency or "ZAR",
        "contribution_date": date.today().isoformat(),
        "payment_method": "ONLINE_YOCO",
        "reference": txn.external_reference,
        "notes": txn.notes,
        "gateway_provider": "yoco",
        "gateway_transaction_id": txn.external_reference,
        "gateway_status": "captured",
    }
    if txn.custom_label:
        payload["custom_label"] = txn.custom_label
    if member and member.external_assembly_id:
        payload["assembly_id"] = member.external_assembly_id

    r = _rfm.create_contribution(payload, db=db)
    if not r.ok:
        txn.central_push_error = (r.error or "Unknown central API error")[:1000]
        return
    txn.central_push_error = None
    txn.central_pushed_at = datetime.utcnow()
    if isinstance(r.data, dict):
        cid = r.data.get("id")
        if cid:
            txn.central_contribution_id = str(cid)


# ============ ADMIN: Yoco configuration ============

YOCO_SETTING_KEYS = {
    "yoco_public_key",
    "yoco_secret_key",
    "yoco_webhook_secret",
    "yoco_test_mode",
    "portal_base_url",
}


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep + 4:
        return "•" * len(value)
    return value[:keep] + "•" * (len(value) - keep - 4) + value[-4:]


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Settings(key=key, value=value))


def _require_admin(request: Request) -> None:
    from routers.pages import is_authenticated
    if not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/admin/yoco/config")
def admin_yoco_config(request: Request, db: Session = Depends(get_db)):
    """Returns Yoco config with the secret values masked. Use this to
    populate the admin settings form."""
    _require_admin(request)
    sk = _setting(db, "yoco_secret_key")
    whsec = _setting(db, "yoco_webhook_secret")
    return {
        "yoco_public_key": _setting(db, "yoco_public_key"),
        "yoco_secret_key_masked": _mask(sk),
        "yoco_secret_key_set": bool(sk),
        "yoco_webhook_secret_masked": _mask(whsec),
        "yoco_webhook_secret_set": bool(whsec),
        "yoco_test_mode": _setting(db, "yoco_test_mode") or "true",
        "portal_base_url": _setting(db, "portal_base_url"),
        "is_live_key": sk.startswith("sk_live_") if sk else False,
    }


@router.put("/admin/yoco/config")
def admin_yoco_config_save(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Save any subset of Yoco settings. Empty strings clear a key.
    Sending the masked placeholder for a secret leaves the existing value alone."""
    _require_admin(request)
    updated: list = []
    for key, value in (payload or {}).items():
        if key not in YOCO_SETTING_KEYS:
            continue
        if value is None:
            continue
        if not isinstance(value, str):
            value = str(value)
        # Don't overwrite a stored secret with the masked placeholder
        if value and "•" in value:
            continue
        _set(db, key, value.strip())
        updated.append(key)
    db.commit()
    return {"updated": updated}


@router.post("/admin/yoco/webhook/register")
def admin_yoco_register_webhook(payload: dict = Body(default={}), request: Request = None, db: Session = Depends(get_db)):
    """Register a Yoco webhook subscription using the stored secret key,
    persist the returned whsec_… into Settings.yoco_webhook_secret, and
    optionally clean up old subscriptions pointing at the same URL."""
    _require_admin(request)
    secret_key = _setting(db, "yoco_secret_key")
    if not secret_key:
        raise HTTPException(status_code=400, detail="Save a Yoco secret key first.")

    base = (payload.get("portal_base_url") or _setting(db, "portal_base_url") or "").strip()
    if not base:
        # Reconstruct from the request — works behind Railway's proxy
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        base = f"{proto}://{host}"
    base = base.rstrip("/")
    webhook_url = f"{base}/api/payments/yoco/webhook"
    name = (payload.get("name") or "RFM portal").strip() or "RFM portal"

    # Remove any prior subscriptions pointing at the same URL — keeps things tidy
    removed: list = []
    try:
        existing = yoco.list_webhooks(secret_key=secret_key)
        for sub in existing:
            if sub.get("url") == webhook_url and sub.get("id"):
                try:
                    yoco.delete_webhook(secret_key=secret_key, subscription_id=sub["id"])
                    removed.append(sub["id"])
                except yoco.YocoError:
                    pass
    except yoco.YocoError as e:
        # If listing fails (e.g. permissions), proceed with just create
        pass

    try:
        result = yoco.register_webhook(secret_key=secret_key, name=name, url=webhook_url)
    except yoco.YocoError as e:
        raise HTTPException(status_code=502, detail=str(e))

    secret = result.get("secret")
    if not secret:
        raise HTTPException(status_code=502, detail="Yoco didn't return a webhook secret. Try again.")
    _set(db, "yoco_webhook_secret", str(secret))
    if base != _setting(db, "portal_base_url"):
        _set(db, "portal_base_url", base)
    db.commit()
    return {
        "registered_url": webhook_url,
        "subscription_id": result.get("id"),
        "name": result.get("name") or name,
        "secret_saved": True,
        "removed_old_subscriptions": removed,
    }


@router.get("/admin/yoco/webhook/list")
def admin_yoco_list_webhooks(request: Request, db: Session = Depends(get_db)):
    """List all webhook subscriptions Yoco has on file for this secret key."""
    _require_admin(request)
    secret_key = _setting(db, "yoco_secret_key")
    if not secret_key:
        raise HTTPException(status_code=400, detail="Save a Yoco secret key first.")
    try:
        subs = yoco.list_webhooks(secret_key=secret_key)
    except yoco.YocoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"subscriptions": subs}


@router.delete("/admin/yoco/webhook/{sub_id}")
def admin_yoco_delete_webhook(sub_id: str, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    secret_key = _setting(db, "yoco_secret_key")
    if not secret_key:
        raise HTTPException(status_code=400, detail="Save a Yoco secret key first.")
    try:
        yoco.delete_webhook(secret_key=secret_key, subscription_id=sub_id)
    except yoco.YocoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"deleted": sub_id}


@router.post("/payments/yoco/webhook")
async def yoco_webhook(request: Request, db: Session = Depends(get_db)):
    """Yoco webhook receiver. Source of truth for payment outcome."""
    body = await request.body()  # MUST be raw bytes — do not call .json() first

    secret = _setting(db, "yoco_webhook_secret")
    if not secret:
        # 503 lets Yoco retry until we configure
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    if not yoco.verify_webhook_signature(
        body=body,
        webhook_id=request.headers.get("webhook-id") or request.headers.get("Webhook-Id"),
        webhook_timestamp=request.headers.get("webhook-timestamp") or request.headers.get("Webhook-Timestamp"),
        webhook_signature=request.headers.get("webhook-signature") or request.headers.get("Webhook-Signature"),
        webhook_secret=secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        event = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return {"status": "ignored", "reason": "invalid json"}

    event_type = (event.get("type") or "").strip()
    payload = event.get("payload") or {}
    metadata = payload.get("metadata") or {}

    checkout_id = (
        metadata.get("checkoutId")
        or payload.get("checkoutId")
        or metadata.get("checkout_id")
        or payload.get("checkout_id")
    )
    if not checkout_id:
        return {"status": "ignored", "reason": "no checkout id"}

    txn = db.query(PaymentTransaction).filter(
        PaymentTransaction.external_reference == str(checkout_id)
    ).first()
    if not txn:
        return {"status": "ignored", "reason": "unknown transaction"}

    txn.last_event_type = event_type[:60]
    txn.last_event_at = datetime.utcnow()

    if event_type == "payment.succeeded" and txn.status == "pending":
        txn.status = "captured"
        _push_to_central(txn, db)
    elif event_type == "payment.failed" and txn.status == "pending":
        txn.status = "failed"
    # Refunds, duplicates, unknown event types — fall through and 200

    db.commit()
    return {"status": "ok", "transaction_id": txn.id, "txn_status": txn.status}
