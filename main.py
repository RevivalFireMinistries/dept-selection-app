from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from sqlalchemy import text

from database import engine
import models
from routers import api, pages
from routers import attendance, cells, directory


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

        # Add new settings if they don't exist
        new_settings = [
            ('resultsPublished', 'false'),
            ('publishedAt', ''),
            ('appealWindowOpen', 'false'),
            ('selectionYear', '2026'),
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

        # ============ CHURCH MANAGEMENT SYSTEM MIGRATIONS ============

        # Add new Member profile columns
        member_columns = [
            ('photo_url', 'VARCHAR'),
            ('birthday', 'DATE'),
            ('anniversary', 'DATE'),
            ('gender', 'VARCHAR'),
            ('marital_status', 'VARCHAR'),
            ('occupation', 'VARCHAR'),
            ('emergency_contact_name', 'VARCHAR'),
            ('emergency_contact_phone', 'VARCHAR'),
            ('member_since', 'DATE'),
            ('is_active', 'BOOLEAN DEFAULT TRUE'),
            ('updated_at', 'TIMESTAMP WITH TIME ZONE'),
        ]
        for col_name, col_type in member_columns:
            result = conn.execute(text(f"""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'members' AND column_name = '{col_name}'
            """))
            if not result.fetchone():
                conn.execute(text(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                conn.commit()
                print(f"Migration: Added {col_name} column to members")

        # Create services table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'services'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS services (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    day_of_week INTEGER NOT NULL,
                    start_time TIME NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
            print("Migration: Created services table")

        # Create service_instances table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'service_instances'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS service_instances (
                    id SERIAL PRIMARY KEY,
                    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    notes VARCHAR,
                    is_cancelled BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    UNIQUE(service_id, date)
                )
            """))
            conn.commit()
            print("Migration: Created service_instances table")

        # Create visitors table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'visitors'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS visitors (
                    id SERIAL PRIMARY KEY,
                    full_name VARCHAR NOT NULL,
                    phone VARCHAR,
                    email VARCHAR,
                    address VARCHAR,
                    first_visit_date DATE NOT NULL,
                    notes VARCHAR,
                    converted_to_member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
            print("Migration: Created visitors table")

        # Create attendance table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'attendance'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id SERIAL PRIMARY KEY,
                    service_instance_id INTEGER NOT NULL REFERENCES service_instances(id) ON DELETE CASCADE,
                    member_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
                    visitor_id INTEGER REFERENCES visitors(id) ON DELETE CASCADE,
                    check_in_method VARCHAR NOT NULL DEFAULT 'admin',
                    check_in_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    checked_in_by INTEGER REFERENCES members(id) ON DELETE SET NULL,
                    UNIQUE(service_instance_id, member_id),
                    UNIQUE(service_instance_id, visitor_id)
                )
            """))
            conn.commit()
            print("Migration: Created attendance table")

        # Create member_qr_codes table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'member_qr_codes'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS member_qr_codes (
                    id SERIAL PRIMARY KEY,
                    member_id INTEGER NOT NULL UNIQUE REFERENCES members(id) ON DELETE CASCADE,
                    code VARCHAR NOT NULL UNIQUE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
            print("Migration: Created member_qr_codes table")

        # Create cell_groups table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'cell_groups'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cell_groups (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    meeting_day INTEGER,
                    meeting_time TIME,
                    meeting_location VARCHAR,
                    leader_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
                    assistant_leader_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
            print("Migration: Created cell_groups table")

        # Create cell_group_memberships table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'cell_group_memberships'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cell_group_memberships (
                    id SERIAL PRIMARY KEY,
                    cell_group_id INTEGER NOT NULL REFERENCES cell_groups(id) ON DELETE CASCADE,
                    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                    role VARCHAR NOT NULL DEFAULT 'member',
                    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    left_at TIMESTAMP WITH TIME ZONE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    UNIQUE(cell_group_id, member_id)
                )
            """))
            conn.commit()
            print("Migration: Created cell_group_memberships table")

        # Create cell_meetings table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'cell_meetings'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cell_meetings (
                    id SERIAL PRIMARY KEY,
                    cell_group_id INTEGER NOT NULL REFERENCES cell_groups(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    topic VARCHAR,
                    notes VARCHAR,
                    offering_amount NUMERIC(10, 2),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
            print("Migration: Created cell_meetings table")

        # Create cell_meeting_attendance table
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables WHERE table_name = 'cell_meeting_attendance'
        """))
        if not result.fetchone():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cell_meeting_attendance (
                    id SERIAL PRIMARY KEY,
                    meeting_id INTEGER NOT NULL REFERENCES cell_meetings(id) ON DELETE CASCADE,
                    member_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
                    visitor_name VARCHAR,
                    visitor_phone VARCHAR,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
            print("Migration: Created cell_meeting_attendance table")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run migrations first
    run_migrations()
    # Create any new tables on startup
    models.Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Department Selection App", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(pages.router, tags=["pages"])
app.include_router(attendance.router, tags=["attendance"])
app.include_router(cells.router, tags=["cell-groups"])
app.include_router(directory.router, tags=["directory"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
