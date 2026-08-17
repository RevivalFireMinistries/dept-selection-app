"""Push portal member credentials into rfm-database for single sign-on.

One-off migration step. Members currently hold their password in the
portal's own `members` table; SSO needs it in rfm-database so the same
credential works across every RFM app.

Bcrypt hashes are sent as-is, not re-hashed, so every member keeps the
password they already have — nobody is forced to reset. Only members that
are LINKED to the central directory are sent: an unlinked member has no
person record to attach a login to, and inventing one would create exactly
the duplicate identities this migration exists to remove.

Safe to re-run: the receiving end is idempotent, and it never overwrites a
password it didn't issue.

    python scripts/export_identities_to_sso.py --dry-run
    python scripts/export_identities_to_sso.py

For PRODUCTION use `POST /api/admin/sso/export-identities` instead. This
script has to reach both the portal's database and rfm-database from
wherever it runs, and on Railway neither is true from a laptop: `railway
run` executes locally with remote env vars, and `*.railway.internal` only
resolves inside the platform. The endpoint runs in the deployed service,
which already has both. This script stays for local runs and for an
operator who has a superadmin login.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal  # noqa: E402
from models import Member  # noqa: E402


def collect() -> tuple[list[dict], dict]:
    db = SessionLocal()
    try:
        members = db.query(Member).all()
        payload, skipped = [], {"no_password": 0, "not_linked": 0, "inactive": 0}
        for m in members:
            if not m.password_hash:
                skipped["no_password"] += 1
                continue
            if not m.external_member_id:
                skipped["not_linked"] += 1
                continue
            if not m.is_active:
                skipped["inactive"] += 1
                continue
            payload.append({
                "external_member_id": m.external_member_id,
                "password_hash": m.password_hash,
                "full_name": m.full_name,
                "email": (m.email or "").strip() or None,
                "phone": (m.phone or "").strip() or None,
                "role": "MEMBER",
            })
        return payload, skipped
    finally:
        db.close()


def push(identities: list[dict], base_url: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1/auth/import-identities",
        data=json.dumps({"source": "portal", "identities": identities}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent")
    ap.add_argument("--url", default=os.environ.get("RFM_API_URL", "http://localhost:8001"))
    ap.add_argument("--email", default=os.environ.get("SSO_ADMIN_EMAIL", "admin@rfm.org.za"))
    ap.add_argument("--password", default=os.environ.get("SSO_ADMIN_PASSWORD", "changeme"))
    args = ap.parse_args()

    identities, skipped = collect()
    print(f"{len(identities)} member credential(s) ready to migrate")
    print(f"  skipped: {skipped['no_password']} without a password, "
          f"{skipped['not_linked']} not linked to the directory, "
          f"{skipped['inactive']} inactive")

    if args.dry_run:
        for i in identities:
            print(f"    {i['full_name']:<24} {i['phone'] or i['email']}  -> {i['external_member_id'][:8]}…")
        print("\ndry run — nothing sent")
        return 0

    if not identities:
        print("nothing to migrate")
        return 0

    login = urllib.request.Request(
        f"{args.url.rstrip('/')}/api/v1/auth/login",
        data=json.dumps({"email": args.email, "password": args.password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(login, timeout=30) as resp:
        body = json.loads(resp.read())
    token = body.get("access_token") or body.get("data", {}).get("access_token")
    if not token:
        print("could not authenticate against rfm-database", file=sys.stderr)
        return 1

    try:
        result = push(identities, args.url, token)
    except urllib.error.HTTPError as e:
        print(f"import failed: HTTP {e.code} {e.read()[:300]}", file=sys.stderr)
        return 1

    data = result.get("data", result)
    print(f"\ncreated {data['created']} · linked {data['linked']} · skipped {data['skipped']}")
    for c in data.get("collisions", []):
        print(f"  ! {c['name']}: {c['note']}")
    for e in data.get("errors", []):
        print(f"  x {e.get('name')}: {e.get('why')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
