import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Construct DATABASE_URL from environment variables if not directly provided
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Try to construct from individual PostgreSQL variables (Railway style)
    host = os.getenv("PGHOST") or os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST")
    database = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or os.getenv("DB_NAME") or "railway"
    user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or os.getenv("DB_USER") or "postgres"
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or os.getenv("DB_PASSWORD")
    port = os.getenv("PGPORT") or os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT") or "5432"

    if host and password:
        DATABASE_URL = f"postgresql://{user}:{password}@{host}:{port}/{database}"
    else:
        raise ValueError("DATABASE_URL not configured. Set DATABASE_URL or individual PG* variables.")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()                                                                                                                
