"""One-shot backfill: copy portal push_subscriptions into rfm-notify.

Run once after deploying the rfm-notify push channel and pointing the
portal at it. Iterates every active row in the portal's
`push_subscriptions` table and POSTs to /api/v1/push-subscriptions/enroll,
which resolves the recipient by the member's email.

Idempotent — re-running re-syncs without creating duplicates because
the rfm-notify endpoint dedupes on the browser endpoint URL.

Usage (from the portal repo root):

    # Set env vars first (or rely on Railway's env injection):
    #   RFM_NOTIFY_URL=https://rfm-notify-production.up.railway.app
    #   RFM_NOTIFY_API_KEY=rfmn_...
    python -m scripts.migrate_push_to_notify

    # Optional flags:
    #   --dry-run        list what we'd send, don't POST
    #   --include-disabled   also copy is_enabled=false rows (rare)

After every active portal enrolment shows up in rfm-notify (you can
verify via the admin console → Channels → Push → Subscriptions), it's
safe to:
  1. Drop the portal's `push_subscriptions` table (Alembic migration).
  2. Delete `push_service.py` and the `PushSubscription` model.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable

# Allow running both as a module and as a plain script
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from database import get_db  # noqa: E402
from models import Member, PushSubscription  # noqa: E402
from notifications import rfm_notify_push as bridge  # noqa: E402


def _iter_subs(db, *, include_disabled: bool) -> Iterable[PushSubscription]:
    q = db.query(PushSubscription)
    if not include_disabled:
        q = q.filter(PushSubscription.is_enabled == True)  # noqa: E712
    return q.order_by(PushSubscription.member_id, PushSubscription.id).all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Don't POST, just print what we'd send")
    parser.add_argument("--include-disabled", action="store_true", help="Copy rows where is_enabled=False")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Stop after N rows (handy for a smoke test)",
    )
    args = parser.parse_args()

    if not bridge.is_configured() and not args.dry_run:
        print(
            "❌ RFM_NOTIFY_URL / RFM_NOTIFY_API_KEY are not set. "
            "Either export them or pass --dry-run."
        )
        sys.exit(1)

    # database.get_db is a generator dependency in FastAPI; pull a session
    db = next(get_db())
    try:
        subs = list(_iter_subs(db, include_disabled=args.include_disabled))
        if args.limit:
            subs = subs[: args.limit]
        print(f"Found {len(subs)} subscription(s) to migrate "
              f"({'including' if args.include_disabled else 'excluding'} disabled).\n")

        sent = skipped = failed = 0
        for s in subs:
            member: Member | None = db.query(Member).filter(Member.id == s.member_id).first()
            if member is None:
                print(f"  [skip] sub#{s.id} → member#{s.member_id} not found")
                skipped += 1
                continue
            if not member.email:
                print(f"  [skip] sub#{s.id} → member#{member.id} has no email "
                      f"(rfm-notify resolves by email; can't enrol without it)")
                skipped += 1
                continue

            label = f"sub#{s.id} member#{member.id} <{member.email}> ep…{(s.endpoint or '')[-12:]}"
            if args.dry_run:
                print(f"  [dry] would enrol {label}")
                continue

            ok, info = bridge.enroll_subscription(
                member_email=member.email,
                member_full_name=member.full_name,
                member_phone=member.phone,
                endpoint=s.endpoint,
                p256dh=s.p256dh_key,
                auth=s.auth_key,
                user_agent=s.user_agent,
            )
            if ok:
                print(f"  [ ok] {label}")
                sent += 1
            else:
                print(f"  [err] {label}\n        → {info}")
                failed += 1

        print(
            f"\nDone. enrolled={sent}  skipped={skipped}  failed={failed}  "
            f"({'dry-run, nothing actually sent' if args.dry_run else 'live'})"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
