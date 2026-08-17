"""Test configuration for the members' portal.

The portal builds its schema at startup rather than through migrations, so
tests run against a real Postgres database and let the app's own lifespan
create it. That keeps the tests honest about the schema the app actually
produces — a sqlite stand-in would diverge from it silently.

The database is separate from the development one and is dropped and
recreated per session, so a test run can never disturb local data.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

# Point the app at the test database BEFORE anything imports database.py,
# which reads DATABASE_URL at import time.
_DEV_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dptselection"
).strip('"')
TEST_DB_NAME = os.environ.get("PORTAL_TEST_DB", "dptselection_test")
TEST_DATABASE_URL = re.sub(r"/[^/?]+(\?|$)", f"/{TEST_DB_NAME}\\1", _DEV_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# The portal reaches four other services. Tests must not depend on any of
# them being up, so the integrations are off unless a test opts in.
os.environ.setdefault("RFM_API_INTEGRATION_ENABLED", "false")
os.environ.pop("SSO_ISSUER", None)
os.environ.pop("SSO_INTERNAL_URL", None)
os.environ.pop("SSO_JWKS_URL", None)


def _recreate_test_database() -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin_url = re.sub(r"/[^/?]+(\?|$)", "/postgres\\1", _DEV_URL)
    conn = psycopg2.connect(admin_url)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (TEST_DB_NAME,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
            cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def _database():
    _recreate_test_database()
    yield


@pytest.fixture(scope="session")
def app(_database):
    """The real FastAPI app, with the background scheduler stubbed out.

    The scheduler fires reminder emails on a timer. Left running it would
    make tests non-deterministic and could send mail, so it is replaced
    rather than started.
    """
    import scheduler

    scheduler.start_scheduler = lambda *a, **k: None
    scheduler.shutdown_scheduler = lambda *a, **k: None

    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture(scope="session")
def client(app):
    """A TestClient whose lifespan has run, so the schema exists."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(client):
    """A session on the test database. Committed writes are visible to the app."""
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── Factories ────────────────────────────────────────────────────────────────
#
# Every fixture generates its own identifiers. Members are keyed by phone
# throughout the portal, so tests that reuse a number collide in ways that
# look like product bugs.


def unique_phone() -> str:
    """A 10-digit number no other test will pick."""
    return "07" + uuid.uuid4().int.__str__()[:8]


def unique_email(prefix: str = "member") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@portal.example.com"


@pytest.fixture
def make_member(db):
    """Create a portal member. Cleans up everything it made."""
    from models import Member

    created = []

    def _make(*, password: str | None = None, external_member_id: str | None = None,
              full_name: str = "Test Member", email: str | None = None,
              phone: str | None = None, is_active: bool = True):
        from routers.api import _hash_password

        member = Member(
            full_name=full_name,
            phone=phone or unique_phone(),
            email=email if email is not None else unique_email(),
            address="1 Test Street",
            password_hash=_hash_password(password) if password else None,
            external_member_id=external_member_id,
            external_match_status="matched" if external_member_id else None,
            is_active=is_active,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        created.append(member.id)
        return member

    yield _make

    for member_id in created:
        obj = db.query(Member).filter(Member.id == member_id).first()
        if obj:
            db.delete(obj)
    db.commit()


@pytest.fixture
def settings_value(db):
    """Set a Settings key for the duration of one test, then restore it."""
    from models import Settings

    original: dict[str, str | None] = {}

    def _set(key: str, value: str):
        row = db.query(Settings).filter(Settings.key == key).first()
        if key not in original:
            original[key] = row.value if row else None
        if row:
            row.value = value
        else:
            db.add(Settings(key=key, value=value))
        db.commit()

    yield _set

    for key, value in original.items():
        row = db.query(Settings).filter(Settings.key == key).first()
        if value is None:
            if row:
                db.delete(row)
        elif row:
            row.value = value
    db.commit()
