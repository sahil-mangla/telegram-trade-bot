import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from utils.logger import log_system

# Pull DATABASE_URL from environment, fallback to sqlite for local dev
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///trades.db")

# Render uses 'postgres://' but sqlalchemy requires 'postgresql://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=False)

# For SQLite, we might need to enforce foreign keys if needed, but standard config is fine
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def init_db():
    log_system(f"Initializing database at {DATABASE_URL}...")
    # Import models here so Base knows about them
    import database.models
    Base.metadata.create_all(bind=engine)
    log_system("Database initialization complete.")

def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
