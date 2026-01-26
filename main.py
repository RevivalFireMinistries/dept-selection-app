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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
