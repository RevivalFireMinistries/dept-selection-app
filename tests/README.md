# Portal tests

```bash
python -m pytest
```

Needs Postgres running. The suite creates and drops its own database
(`dptselection_test`), derived from `DATABASE_URL` — it never touches the
development one. Override the name with `PORTAL_TEST_DB`.

Nothing here requires the wider RFM stack. rfm-database, church-manager and
rfm-notify are all stubbed, so the suite runs on a laptop with only a
database. That is deliberate: tests that need four services running are
tests nobody runs.

## What's covered

| File | Subject |
|---|---|
| `test_sso_auth.py` | Verifying identity-provider tokens; mapping them onto a member |
| `test_login_sso.py` | Every population that must still be able to sign in after the SSO migration |
| `test_sso_export.py` | The one-off credential export, run as an admin endpoint |
| `test_events_portal.py` | The portal's events surface — identity injection, guest search, manager access |

## Coverage

Overall coverage is **25%**, well under the 80% CLAUDE.md asks for. The
suites above cover what they target closely (`sso_auth.py` at 83%), but
`routers/api.py` — about 10,000 lines carrying most of the portal — had no
tests before this and still has none. Reaching 80% means working through
that module, which is a separate piece of work from the SSO and events
features these tests were written for.

Run `pytest --cov=. --cov-report=term-missing` for the current picture.
