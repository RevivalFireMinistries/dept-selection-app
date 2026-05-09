"""rfm-notify channel — routes pre-rendered emails through the central
notification service for logging, opt-out enforcement, and auto-footer
unsubscribe injection.

The portal continues to render its own HTML and subject (no template
migration needed); we just hand the result to rfm-notify with the
`body_override` / `subject_override` knobs and let it deliver.

Configuration is environment-driven only — there's no admin-UI for
this channel, the way Resend/SMTP had. Set:

    RFM_NOTIFY_URL          (e.g. https://notify.rfm.org.za)
    RFM_NOTIFY_API_KEY      (rfmn_… key with `send` scope)
    RFM_NOTIFY_APP_CODE     (defaults to "rfm-portal")
    RFM_NOTIFY_EVENT_PREFIX (optional, prepended to every event_code)
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

import requests

from notifications.channels.base import NotificationChannel

log = logging.getLogger(__name__)


class RfmNotifyChannel(NotificationChannel):
    """Send via rfm-notify's POST /api/v1/notify with body_override."""

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        app_code: str | None = None,
    ) -> None:
        self.url = (url or os.getenv("RFM_NOTIFY_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("RFM_NOTIFY_API_KEY", "")
        self.app_code = app_code or os.getenv("RFM_NOTIFY_APP_CODE", "rfm-portal")

    # --- NotificationChannel interface ---

    def is_configured(self) -> bool:
        return bool(self.url and self.api_key)

    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        **kwargs,
    ) -> Tuple[bool, Optional[str]]:
        """Deliver one email through rfm-notify.

        kwargs accepts:
            event_code        — the portal EventType.value (recommended)
            recipient_id      — UUID for cross-system member match (optional)
            recipient_name    — full name (improves audit trail)
            idempotency_key   — for retry safety (recommended)
            priority          — "normal" | "high" (defaults "normal")
        """
        if not self.is_configured():
            return False, "rfm-notify not configured (RFM_NOTIFY_URL / RFM_NOTIFY_API_KEY missing)"

        event_code = kwargs.get("event_code", "portal.email")
        prefix = os.getenv("RFM_NOTIFY_EVENT_PREFIX", "")
        if prefix:
            event_code = f"{prefix}{event_code}"

        recipient_block: dict = {"email": recipient}
        # Only forward recipient_id if it parses as a UUID. The portal's
        # member.id is a SERIAL integer that has no meaning in rfm-notify's
        # universe; rfm-notify's RecipientSpec requires UUID, so passing
        # the int would 422 the whole request. Drop silently when it's
        # not a UUID — email is enough for matching downstream.
        rid_raw = kwargs.get("recipient_id")
        if rid_raw is not None:
            import uuid as _uuid
            try:
                _uuid.UUID(str(rid_raw))
                recipient_block["member_id"] = str(rid_raw)
            except (ValueError, TypeError):
                pass  # not a UUID — fine, just don't include it
        if kwargs.get("recipient_name"):
            recipient_block["full_name"] = kwargs["recipient_name"]

        payload: dict = {
            "app_code": self.app_code,
            "event_code": event_code,
            "recipient": recipient_block,
            # Override knobs — skip template lookup, use the portal's HTML
            "body_override": body,
            "subject_override": subject,
            "priority": kwargs.get("priority", "normal"),
        }
        if kwargs.get("idempotency_key"):
            payload["idempotency_key"] = kwargs["idempotency_key"]

        url = f"{self.url}/api/v1/notify"
        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
        except requests.RequestException as exc:
            log.warning("[rfm_notify] network error -> %s: %s", url, exc)
            return False, f"rfm-notify network error: {exc}"

        if response.status_code >= 400:
            log.warning(
                "[rfm_notify] non-2xx for %s/%s -> %s %s",
                self.app_code, event_code, response.status_code, response.text[:300],
            )
            return False, f"rfm-notify {response.status_code}: {response.text[:200]}"

        # Inspect the dispatched array to confirm the email channel sent.
        # Anything other than `sent` / `already-sent` we treat as a failure
        # so the dispatcher can log it correctly upstream.
        try:
            data = response.json()
        except ValueError:
            return True, None  # 2xx without body — treat as success

        dispatched = (data or {}).get("dispatched") or []
        email_results = [d for d in dispatched if d.get("channel") == "email"]
        if not email_results:
            # No email branch fired — likely route config didn't include email
            return False, "rfm-notify dispatched no email channel for this event"
        first = email_results[0]
        status = first.get("status")
        if status in ("sent", "already-sent"):
            return True, None
        return False, first.get("error") or f"rfm-notify status: {status}"

    def test_connection(self) -> Tuple[bool, Optional[str]]:
        """Hit /health on rfm-notify to verify the URL is reachable.
        Doesn't require the API key — just checks the service is up."""
        if not self.url:
            return False, "RFM_NOTIFY_URL not set"
        try:
            r = requests.get(f"{self.url}/health", timeout=10)
        except requests.RequestException as exc:
            return False, f"Cannot reach rfm-notify: {exc}"
        if r.status_code != 200:
            return False, f"rfm-notify /health returned {r.status_code}"
        return True, None
