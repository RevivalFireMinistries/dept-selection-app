from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from sqlalchemy import text

from database import engine
import models
from routers import api, pages


def run_migrations():
    """Add new columns to existing tables if they don't exist"""
    with engine.connect() as conn:
        # Check if source column exists in member_departments
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'member_departments' AND column_name = 'source'
        """))
        if not result.fetchone():
            # Add the new approval workflow columns
            conn.execute(text("""
                ALTER TABLE member_departments
                ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'member',
                ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'pending',
                ADD COLUMN IF NOT EXISTS replaced_by_id INTEGER REFERENCES member_departments(id),
                ADD COLUMN IF NOT EXISTS admin_note VARCHAR,
                ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMP WITH TIME ZONE
            """))
            conn.commit()
            print("Migration: Added approval workflow columns to member_departments")

        # Check if appeals table exists
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'appeals'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS appeals (
                    id SERIAL PRIMARY KEY,
                    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                    unwanted_department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
                    wanted_department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
                    reason VARCHAR,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    admin_response VARCHAR,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    resolved_at TIMESTAMP WITH TIME ZONE
                )
            """))
            conn.commit()
            print("Migration: Created appeals table")

        # Add hod_member_id to departments table
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'departments' AND column_name = 'hod_member_id'
        """))
        if not result.fetchone():
            conn.execute(text("""
                ALTER TABLE departments
                ADD COLUMN IF NOT EXISTS hod_member_id INTEGER REFERENCES members(id) ON DELETE SET NULL
            """))
            conn.commit()
            print("Migration: Added hod_member_id column to departments")

        # Check if meetings table exists (created by create_all, log for visibility)
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'meetings'
        """))
        if not result.fetchone():
            print("Migration: meetings and meeting_rsvps tables will be created by create_all()")
        else:
            # Add is_general and target_department_ids columns to meetings if they don't exist
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'meetings' AND column_name = 'is_general'
            """))
            if not result.fetchone():
                conn.execute(text("""
                    ALTER TABLE meetings
                    ADD COLUMN IF NOT EXISTS is_general INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS target_department_ids TEXT
                """))
                # Also make department_id nullable for general meetings
                conn.execute(text("""
                    ALTER TABLE meetings
                    ALTER COLUMN department_id DROP NOT NULL
                """))
                conn.commit()
                print("Migration: Added is_general and target_department_ids columns to meetings")

        # Add recurrence_group_id column to meetings
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'meetings' AND column_name = 'recurrence_group_id'
        """))
        if not result.fetchone():
            conn.execute(text("""
                ALTER TABLE meetings
                ADD COLUMN IF NOT EXISTS recurrence_group_id VARCHAR(36)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_meetings_recurrence_group_id
                ON meetings (recurrence_group_id)
            """))
            conn.commit()
            print("Migration: Added recurrence_group_id column to meetings")

        # Add target_member_ids column to meetings for individual invites
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'meetings' AND column_name = 'target_member_ids'
        """))
        if not result.fetchone():
            conn.execute(text("""
                ALTER TABLE meetings
                ADD COLUMN IF NOT EXISTS target_member_ids TEXT
            """))
            conn.commit()
            print("Migration: Added target_member_ids column to meetings")

        # Add leadership_roles column to members for leadership tags (deacon, elder, etc.)
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'members' AND column_name = 'leadership_roles'
        """))
        if not result.fetchone():
            conn.execute(text("""
                ALTER TABLE members
                ADD COLUMN IF NOT EXISTS leadership_roles TEXT
            """))
            conn.commit()
            print("Migration: Added leadership_roles column to members")

        # Add target_leadership_roles column to meetings for leadership role-based meetings
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'meetings' AND column_name = 'target_leadership_roles'
        """))
        if not result.fetchone():
            conn.execute(text("""
                ALTER TABLE meetings
                ADD COLUMN IF NOT EXISTS target_leadership_roles TEXT
            """))
            conn.commit()
            print("Migration: Added target_leadership_roles column to meetings")

        # Add new settings if they don't exist
        # SMTP settings can be overridden by environment variables
        import os
        new_settings = [
            ('resultsPublished', 'false'),
            ('publishedAt', ''),
            ('appealWindowOpen', 'false'),
            ('selectionYear', '2026'),
            # SMTP settings for notifications (env vars take precedence)
            ('smtp_enabled', os.getenv('SMTP_ENABLED', 'false')),
            ('smtp_host', os.getenv('SMTP_HOST', 'smtp.gmail.com')),
            ('smtp_port', os.getenv('SMTP_PORT', '587')),
            ('smtp_username', os.getenv('SMTP_USERNAME', '')),
            ('smtp_password', os.getenv('SMTP_PASSWORD', '')),
            ('smtp_from_name', os.getenv('SMTP_FROM_NAME', 'RFM Stellenbosch')),
            ('smtp_from_email', os.getenv('SMTP_FROM_EMAIL', '')),
            # Resend settings (preferred for cloud platforms like Railway)
            ('resend_enabled', os.getenv('RESEND_ENABLED', 'false')),
            ('resend_api_key', os.getenv('RESEND_API_KEY', '')),
            ('resend_from_name', os.getenv('RESEND_FROM_NAME', 'RFM Stellenbosch')),
            ('resend_from_email', os.getenv('RESEND_FROM_EMAIL', '')),
            # Poster request settings
            ('poster_request_department_id', ''),  # Department ID that handles poster requests
        ]
        for key, value in new_settings:
            result = conn.execute(text(
                "SELECT key FROM settings WHERE key = :key"
            ), {"key": key})
            if not result.fetchone():
                conn.execute(text(
                    "INSERT INTO settings (key, value) VALUES (:key, :value)"
                ), {"key": key, "value": value})
                conn.commit()
                print(f"Migration: Added setting {key}={value}")

        # Migrate poster_requests table: rename speaker_host to speakers, add output_formats, make ministry_department nullable
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'poster_requests' AND column_name = 'speakers'
        """))
        if not result.fetchone():
            # Check if table exists first
            table_exists = conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'poster_requests'
            """)).fetchone()
            if table_exists:
                # Rename speaker_host to speakers
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'poster_requests' AND column_name = 'speaker_host'
                """))
                if result.fetchone():
                    conn.execute(text("""
                        ALTER TABLE poster_requests RENAME COLUMN speaker_host TO speakers
                    """))
                    conn.commit()
                    print("Migration: Renamed speaker_host to speakers in poster_requests")
                else:
                    # Add speakers column if it doesn't exist
                    conn.execute(text("""
                        ALTER TABLE poster_requests ADD COLUMN IF NOT EXISTS speakers TEXT
                    """))
                    conn.commit()
                    print("Migration: Added speakers column to poster_requests")

        # Add output_formats column to poster_requests
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'poster_requests' AND column_name = 'output_formats'
        """))
        if not result.fetchone():
            table_exists = conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'poster_requests'
            """)).fetchone()
            if table_exists:
                conn.execute(text("""
                    ALTER TABLE poster_requests ADD COLUMN IF NOT EXISTS output_formats TEXT
                """))
                conn.commit()
                print("Migration: Added output_formats column to poster_requests")

        # Make ministry_department nullable in poster_requests
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'poster_requests'
        """))
        if result.fetchone():
            conn.execute(text("""
                ALTER TABLE poster_requests ALTER COLUMN ministry_department DROP NOT NULL
            """))
            conn.commit()
            print("Migration: Made ministry_department nullable in poster_requests")

        # service_programs table is created by create_all() - no migration needed
        # Auto-cleanup of past programs happens on GET /api/programs/today

        # Seed default notification configs for all event types
        event_types = [
            'member_approved',
            'member_rejected',
            'department_assigned',
            'results_published',
            'appeal_submitted',
            'appeal_resolved',
            'meeting_created',
            'meeting_reminder',
            'meeting_updated',
            'meeting_cancelled',
            'poster_request_submitted',
            'poster_request_acknowledged',
            'poster_request_completed',
        ]
        # Check if notification_configs table exists (created by create_all)
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'notification_configs'
        """))
        if result.fetchone():
            for event_type in event_types:
                result = conn.execute(text(
                    "SELECT event_type FROM notification_configs WHERE event_type = :event_type"
                ), {"event_type": event_type})
                if not result.fetchone():
                    conn.execute(text(
                        "INSERT INTO notification_configs (event_type, email_enabled) VALUES (:event_type, 1)"
                    ), {"event_type": event_type})
                    conn.commit()
                    print(f"Migration: Added notification config for {event_type}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run migrations first
    run_migrations()
    # Create any new tables on startup
    models.Base.metadata.create_all(bind=engine)

    # Start background scheduler for reminders
    from scheduler import start_scheduler, shutdown_scheduler
    start_scheduler()

    yield

    # Shutdown scheduler on app exit
    shutdown_scheduler()


app = FastAPI(title="Department Selection App", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(pages.router, tags=["pages"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
