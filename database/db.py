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

def _run_migrations(engine):
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE trades ADD COLUMN entry_order_id TEXT"))
                log_system("Migration: Added column 'entry_order_id' to trades table.")
            except Exception as e:
                err_msg = str(e).lower()
                if "duplicate" in err_msg or "already exists" in err_msg:
                    pass
                else:
                    log_system(f"Migration warning for entry_order_id: {e}", level=30)
            
            try:
                conn.execute(text("ALTER TABLE trades ADD COLUMN exit_order_id TEXT"))
                log_system("Migration: Added column 'exit_order_id' to trades table.")
            except Exception as e:
                err_msg = str(e).lower()
                if "duplicate" in err_msg or "already exists" in err_msg:
                    pass
                else:
                    log_system(f"Migration warning for exit_order_id: {e}", level=30)

            try:
                conn.execute(text("ALTER TABLE trades ADD COLUMN product_type TEXT DEFAULT 'MIS'"))
                log_system("Migration: Added column 'product_type' to trades table.")
            except Exception as e:
                err_msg = str(e).lower()
                if "duplicate" in err_msg or "already exists" in err_msg:
                    pass
                else:
                    log_system(f"Migration warning for product_type: {e}", level=30)

            try:
                conn.execute(text("ALTER TABLE trades ADD COLUMN gtt_id TEXT"))
                log_system("Migration: Added column 'gtt_id' to trades table.")
            except Exception as e:
                err_msg = str(e).lower()
                if "duplicate" in err_msg or "already exists" in err_msg:
                    pass
                else:
                    log_system(f"Migration warning for gtt_id: {e}", level=30)

            # Add ATR and blow-off indicator columns
            for col_name in ('atr', 'yesterday_close', 'average_price_20d', 'average_volume_20d'):
                try:
                    conn.execute(text(f"ALTER TABLE trades ADD COLUMN {col_name} REAL"))
                    log_system(f"Migration: Added column '{col_name}' to trades table.")
                except Exception as e:
                    err_msg = str(e).lower()
                    if "duplicate" in err_msg or "already exists" in err_msg:
                        pass
                    else:
                        log_system(f"Migration warning for {col_name}: {e}", level=30)
    except Exception as e:
        log_system(f"Migration engine connection error: {e}", level=40)

def init_db():
    log_system(f"Initializing database at {DATABASE_URL}...")
    # Import models here so Base knows about them
    import database.models
    Base.metadata.create_all(bind=engine)
    _run_migrations(engine)
    log_system("Database initialization complete.")

def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
