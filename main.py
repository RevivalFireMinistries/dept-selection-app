from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import text

from database import engine
import models
from routers import api, pages, display, songlist, payments, reading_plans, push, external


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

        # Add announcement and prayer_points columns to service_programs and program_templates
        for table_name in ['service_programs', 'program_templates']:
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = :table AND column_name = 'admin_announcements'
            """), {"table": table_name})
            if not result.fetchone():
                table_exists = conn.execute(text("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_name = :table
                """), {"table": table_name}).fetchone()
                if table_exists:
                    conn.execute(text(f"""
                        ALTER TABLE {table_name}
                        ADD COLUMN IF NOT EXISTS admin_announcements TEXT NOT NULL DEFAULT '[]',
                        ADD COLUMN IF NOT EXISTS pastors_announcements TEXT NOT NULL DEFAULT '[]'
                    """))
                    conn.commit()
                    print(f"Migration: Added announcement columns to {table_name}")

            # Add prayer_points column
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = :table AND column_name = 'prayer_points'
            """), {"table": table_name})
            if not result.fetchone():
                table_exists = conn.execute(text("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_name = :table
                """), {"table": table_name}).fetchone()
                if table_exists:
                    conn.execute(text(f"""
                        ALTER TABLE {table_name}
                        ADD COLUMN IF NOT EXISTS prayer_points TEXT NOT NULL DEFAULT '[]'
                    """))
                    conn.commit()
                    print(f"Migration: Added prayer_points column to {table_name}")

        # Add support_roles column to program_templates
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'program_templates' AND column_name = 'support_roles'
        """))
        if not result.fetchone():
            table_exists = conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'program_templates'
            """)).fetchone()
            if table_exists:
                conn.execute(text("""
                    ALTER TABLE program_templates
                    ADD COLUMN IF NOT EXISTS support_roles TEXT NOT NULL DEFAULT '[]'
                """))
                conn.commit()
                print("Migration: Added support_roles column to program_templates")

        # Add created_by_member_id column to service_programs
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'service_programs' AND column_name = 'created_by_member_id'
        """))
        if not result.fetchone():
            table_exists = conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'service_programs'
            """)).fetchone()
            if table_exists:
                conn.execute(text("""
                    ALTER TABLE service_programs
                    ADD COLUMN IF NOT EXISTS created_by_member_id INTEGER REFERENCES members(id) ON DELETE SET NULL
                """))
                conn.commit()
                print("Migration: Added created_by_member_id column to service_programs")

        # Add location_type column to program_templates and service_programs
        for table_name in ['program_templates', 'service_programs']:
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = :table AND column_name = 'location_type'
            """), {"table": table_name})
            if not result.fetchone():
                table_exists = conn.execute(text("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_name = :table
                """), {"table": table_name}).fetchone()
                if table_exists:
                    conn.execute(text(f"""
                        ALTER TABLE {table_name}
                        ADD COLUMN IF NOT EXISTS location_type VARCHAR(20) NOT NULL DEFAULT 'onsite'
                    """))
                    conn.commit()
                    print(f"Migration: Added location_type column to {table_name}")

        # Add status column to service_programs for draft/published workflow
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'service_programs' AND column_name = 'status'
        """))
        if not result.fetchone():
            conn.execute(text("""
                ALTER TABLE service_programs
                ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft'
            """))
            conn.commit()
            print("Migration: Added status column to service_programs")

        # Create service_schedules table for future service planning
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'service_schedules'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE service_schedules (
                    id SERIAL PRIMARY KEY,
                    service_date DATE NOT NULL,
                    template_id INTEGER REFERENCES program_templates(id) ON DELETE SET NULL,
                    service_manager_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
                    notes TEXT,
                    notified_at TIMESTAMP WITH TIME ZONE,
                    reminded_at TIMESTAMP WITH TIME ZONE,
                    program_id INTEGER REFERENCES service_programs(id) ON DELETE SET NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
            print("Migration: Created service_schedules table")

        # Create display_submissions table for FirePresenter integration
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'display_submissions'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE display_submissions (
                    id SERIAL PRIMARY KEY,
                    submitter_name VARCHAR NOT NULL,
                    submitter_dept VARCHAR,
                    submitter_phone VARCHAR,
                    content_type VARCHAR NOT NULL,
                    title VARCHAR NOT NULL,
                    body TEXT,
                    subtitle VARCHAR,
                    comments TEXT,
                    file_path VARCHAR,
                    file_name VARCHAR,
                    file_size INTEGER,
                    service_date DATE NOT NULL,
                    display_slot VARCHAR NOT NULL DEFAULT 'announcements',
                    display_duration INTEGER,
                    display_order INTEGER,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    reviewed_by VARCHAR,
                    review_note VARCHAR,
                    fetched BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
            print("Migration: Created display_submissions table")

        # Add authentication columns to members
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'members' AND column_name = 'password_hash'
        """))
        if not result.fetchone():
            conn.execute(text("""
                ALTER TABLE members
                ADD COLUMN IF NOT EXISTS password_hash VARCHAR,
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS reset_token VARCHAR,
                ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMP WITH TIME ZONE
            """))
            # Existing members are active by default
            conn.execute(text("UPDATE members SET is_active = TRUE WHERE is_active IS NULL"))
            conn.commit()
            print("Migration: Added authentication columns to members")

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
            'program_participant_added',
            'home_church_roster_published',
            'home_church_preacher_assigned',
            'home_church_reminder_leader',
            'home_church_reminder_preacher',
            'home_church_attendance_reminder',
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


        # Migration: Add actor_type column to admin_audit_logs
        try:
            conn.execute(text("""
                ALTER TABLE admin_audit_logs
                ADD COLUMN IF NOT EXISTS actor_type VARCHAR(20) NOT NULL DEFAULT 'admin'
            """))
            conn.commit()
            print("Migration: Added actor_type column to admin_audit_logs")
        except Exception as e:
            print(f"Migration note (actor_type): {e}")

        # Migration: Add template_id to service_programs so we can preserve
        # which template a program was created from (used to keep support_roles
        # available in the role dropdown when editing).
        try:
            conn.execute(text("""
                ALTER TABLE service_programs
                ADD COLUMN IF NOT EXISTS template_id INTEGER REFERENCES program_templates(id) ON DELETE SET NULL
            """))
            conn.commit()
            print("Migration: Added template_id column to service_programs")
        except Exception as e:
            print(f"Migration note (template_id): {e}")

        # Migration: rfm-database integration columns. Nullable so adding them
        # is a zero-impact change. Behaviour is gated by RFM_API_INTEGRATION_ENABLED.
        try:
            conn.execute(text("""
                ALTER TABLE members
                ADD COLUMN IF NOT EXISTS external_member_id VARCHAR(36),
                ADD COLUMN IF NOT EXISTS external_match_status VARCHAR(20),
                ADD COLUMN IF NOT EXISTS external_synced_at TIMESTAMP WITH TIME ZONE,
                ADD COLUMN IF NOT EXISTS external_assembly_id VARCHAR(36)
            """))
            # Indexes for fast lookup by external id / unmatched listing
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_members_external_member_id
                ON members(external_member_id)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_members_external_match_status
                ON members(external_match_status)
            """))
            conn.commit()
            print("Migration: Added rfm-database integration columns to members")
        except Exception as e:
            print(f"Migration note (rfm-db integration): {e}")

        # Migration: same external link columns on departments — central API
        # now supports departments, so we sync local <-> central by UUID.
        try:
            conn.execute(text("""
                ALTER TABLE departments
                ADD COLUMN IF NOT EXISTS external_department_id VARCHAR(36),
                ADD COLUMN IF NOT EXISTS external_synced_at TIMESTAMP WITH TIME ZONE
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_departments_external_department_id
                ON departments(external_department_id)
            """))
            conn.commit()
            print("Migration: Added rfm-db integration columns to departments")
        except Exception as e:
            print(f"Migration note (departments rfm-db): {e}")

        # Migration: rfm-db link columns on home_churches
        try:
            conn.execute(text("""
                ALTER TABLE home_churches
                ADD COLUMN IF NOT EXISTS external_home_church_id VARCHAR(36),
                ADD COLUMN IF NOT EXISTS external_synced_at TIMESTAMP WITH TIME ZONE
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_home_churches_external_id
                ON home_churches(external_home_church_id)
            """))
            conn.commit()
            print("Migration: Added rfm-db integration columns to home_churches")
        except Exception as e:
            print(f"Migration note (home_churches rfm-db): {e}")

        # Migration: allow_multiple_responses on surveys (added after initial ship)
        try:
            conn.execute(text("""
                ALTER TABLE surveys
                ADD COLUMN IF NOT EXISTS allow_multiple_responses BOOLEAN NOT NULL DEFAULT FALSE
            """))
            conn.commit()
            print("Migration: Added allow_multiple_responses to surveys")
        except Exception as e:
            print(f"Migration note (allow_multiple_responses): {e}")

        # Migration: surveys feature — members.can_create_surveys flag.
        # Survey/Question/Response/Answer tables are created by metadata.create_all
        # on lifespan startup (no manual ALTER needed for new tables).
        try:
            conn.execute(text("""
                ALTER TABLE members
                ADD COLUMN IF NOT EXISTS can_create_surveys BOOLEAN NOT NULL DEFAULT FALSE
            """))
            conn.commit()
            print("Migration: Added can_create_surveys flag to members")
        except Exception as e:
            print(f"Migration note (can_create_surveys): {e}")

        # Seed starter reading plans (drafts) if the table is empty so the
        # Bible Reading Plan team has something to clone-and-edit instead
        # of an empty page on day one.
        try:
            existing = conn.execute(text("SELECT COUNT(*) FROM reading_plans")).scalar()
            if existing == 0:
                from datetime import datetime as _dt
                starter_plans = [
                    {
                        "title": "Psalms in 30",
                        "slug": "psalms-30",
                        "description": "Walk through the Psalter five psalms at a time over a month.",
                        "cover_emoji": "🎵",
                        "plan_type": "BOOK",
                        "cadence": "DAILY",
                        "tags": ["psalms", "worship", "30-day"],
                        "days": [
                            (i + 1, f"Psalm {a}-{b}", f"Day {i + 1}")
                            for i, (a, b) in enumerate(
                                [(1,5),(6,10),(11,15),(16,20),(21,25),(26,30),(31,35),(36,40),
                                 (41,45),(46,50),(51,55),(56,60),(61,65),(66,70),(71,75),(76,80),
                                 (81,85),(86,90),(91,95),(96,100),(101,105),(106,110),(111,115),
                                 (116,118),(119,119),(120,124),(125,131),(132,138),(139,145),(146,150)]
                            )
                        ],
                    },
                    {
                        "title": "Proverbs in 31",
                        "slug": "proverbs-31",
                        "description": "A chapter a day matched to the calendar — wisdom for every day of the month.",
                        "cover_emoji": "📜",
                        "plan_type": "BOOK",
                        "cadence": "DAILY",
                        "tags": ["proverbs", "wisdom"],
                        "days": [(i, f"Proverbs {i}", None) for i in range(1, 32)],
                    },
                    {
                        "title": "John in 21",
                        "slug": "john-21",
                        "description": "The fourth gospel, one chapter a day. See and believe.",
                        "cover_emoji": "✝️",
                        "plan_type": "BOOK",
                        "cadence": "DAILY",
                        "tags": ["gospel", "jesus", "21-day"],
                        "days": [(i, f"John {i}", None) for i in range(1, 22)],
                    },
                    {
                        "title": "Elijah in 14",
                        "slug": "elijah-14",
                        "description": "Two weeks with the prophet who called down fire — courage, doubt, and rest.",
                        "cover_emoji": "🔥",
                        "plan_type": "PERSON",
                        "cadence": "MEMBER_PACED",
                        "tags": ["prophet", "elijah", "14-day"],
                        "days": [
                            (1,  "1 Kings 16:29-17:7",  "Drought announced"),
                            (2,  "1 Kings 17:8-24",     "Cared for at Zarephath"),
                            (3,  "1 Kings 18:1-19",     "Confronting Ahab"),
                            (4,  "1 Kings 18:20-40",    "Mount Carmel"),
                            (5,  "1 Kings 18:41-46",    "Rain returns"),
                            (6,  "1 Kings 19:1-9a",     "Burned out under the broom tree"),
                            (7,  "1 Kings 19:9b-18",    "The still, small voice"),
                            (8,  "1 Kings 19:19-21",    "Calling Elisha"),
                            (9,  "1 Kings 21",          "Naboth's vineyard"),
                            (10, "2 Kings 1",           "Fire from heaven"),
                            (11, "2 Kings 2:1-12",      "Taken up to heaven"),
                            (12, "Malachi 4:5-6",       "The Elijah to come"),
                            (13, "Matthew 17:1-13",     "Elijah on the mountain with Jesus"),
                            (14, "James 5:13-18",       "A man like us — pray like him"),
                        ],
                    },
                ]
                for plan in starter_plans:
                    duration = len(plan["days"])
                    plan_id = conn.execute(text("""
                        INSERT INTO reading_plans
                            (title, slug, description, cover_emoji, plan_type, cadence,
                             duration_days, tags, status, visibility, featured, is_default)
                        VALUES (:title, :slug, :description, :cover_emoji, :plan_type, :cadence,
                                :duration, :tags, 'draft', 'INTERNAL', false, false)
                        RETURNING id
                    """), {
                        "title": plan["title"],
                        "slug": plan["slug"],
                        "description": plan["description"],
                        "cover_emoji": plan["cover_emoji"],
                        "plan_type": plan["plan_type"],
                        "cadence": plan["cadence"],
                        "duration": duration,
                        "tags": __import__("json").dumps(plan["tags"]),
                    }).scalar()
                    for day_number, passages, theme in plan["days"]:
                        conn.execute(text("""
                            INSERT INTO reading_plan_days
                                (plan_id, day_number, passages, theme)
                            VALUES (:plan_id, :day_number, :passages, :theme)
                        """), {
                            "plan_id": plan_id,
                            "day_number": day_number,
                            "passages": passages,
                            "theme": theme,
                        })
                conn.commit()
                print(f"Migration: Seeded {len(starter_plans)} draft reading plans")
        except Exception as e:
            print(f"Migration note (reading plans seed): {e}")

        # Seed default home church program types (only if table is empty)
        try:
            existing = conn.execute(text("SELECT COUNT(*) FROM home_church_program_types")).scalar()
            if existing == 0:
                defaults = [
                    ("Preaching Service", True, "blue", "📖", 10),
                    ("Fellowship Night", False, "purple", "🤝", 20),
                    ("Prayer Night", False, "emerald", "🙏", 30),
                    ("Bible Study", False, "amber", "📚", 40),
                ]
                for name, requires, color, icon, sort in defaults:
                    conn.execute(text("""
                        INSERT INTO home_church_program_types
                            (name, requires_preacher, color, icon, sort_order, is_active)
                        VALUES (:name, :requires, :color, :icon, :sort, TRUE)
                    """), {"name": name, "requires": requires, "color": color, "icon": icon, "sort": sort})
                conn.commit()
                print(f"Migration: Seeded {len(defaults)} default home church program types")
        except Exception as e:
            print(f"Migration note (home church program types): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create any new tables on startup (before migrations that alter them)
    models.Base.metadata.create_all(bind=engine)
    # Run migrations to add columns to existing tables
    run_migrations()

    # Start background scheduler for reminders
    from scheduler import start_scheduler, shutdown_scheduler
    start_scheduler()

    yield

    # Shutdown scheduler on app exit
    shutdown_scheduler()


app = FastAPI(title="RFM Stellenbosch Portal", lifespan=lifespan)

# ─── Verbose request/response logging ───────────────────────────────
import time as _time, logging as _logging

_logging.basicConfig(level=_logging.INFO)
_logger = _logging.getLogger("rfm")

@app.middleware("http")
async def verbose_logging_middleware(request, call_next):
    start = _time.time()
    method = request.method
    path = request.url.path
    qs = str(request.url.query)
    _logger.info(f"→ {method} {path}{'?' + qs if qs else ''}")
    try:
        response = await call_next(request)
        elapsed = (_time.time() - start) * 1000
        _logger.info(f"← {method} {path} → {response.status_code} ({elapsed:.0f}ms)")
        return response
    except Exception as exc:
        elapsed = (_time.time() - start) * 1000
        _logger.error(f"✗ {method} {path} → EXCEPTION ({elapsed:.0f}ms): {exc}")
        raise

# CORS — allow FirePresenter and other local apps to access the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(payments.router, prefix="/api", tags=["payments"])
app.include_router(reading_plans.router, prefix="/api", tags=["reading-plans"])
app.include_router(push.router, prefix="/api", tags=["push"])
app.include_router(external.router, prefix="/api", tags=["external"])
app.include_router(display.router, prefix="/api/display", tags=["display"])
app.include_router(songlist.router, tags=["songlist"])
app.include_router(pages.router, tags=["pages"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
