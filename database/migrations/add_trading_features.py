import sqlite3
import os
from database.db import DATABASE_URL

def run_migration():
    # Extract path from sqlite:///trades.db
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        print(f"Database {db_path} not found. init_db will create it automatically.")
        return

    print(f"Migrating database at {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # List of new columns to add to 'trades' table
    new_columns = [
        ("initial_risk_amount", "REAL"),
        ("risk_per_share", "REAL"),
        ("max_mfe", "REAL"),
        ("max_mae", "REAL"),
        ("highest_price_reached", "REAL"),
        ("trailing_stop_events", "TEXT"),
        ("exit_reason", "VARCHAR(50)"),
        ("r_multiple_at_exit", "REAL"),
        ("signal_source", "VARCHAR(50) DEFAULT 'manual'"),
        ("allocation_percentage", "REAL"),
        ("r_thresholds_crossed", "TEXT")
    ]

    # Get existing columns
    cursor.execute("PRAGMA table_info(trades)")
    existing_columns = [row[1] for row in cursor.fetchall()]

    for col_name, col_type in new_columns:
        if col_name not in existing_columns:
            print(f"Adding column {col_name} to trades table...")
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
        else:
            print(f"Column {col_name} already exists in trades table.")

    # Create daily_summary table if not exists
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date DATE UNIQUE,
        total_trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        sl_hits INTEGER DEFAULT 0,
        total_pnl REAL DEFAULT 0,
        win_rate REAL DEFAULT 0,
        avg_r_multiple REAL DEFAULT 0,
        max_r_multiple_achieved REAL DEFAULT 0,
        trades_reached_3r INTEGER DEFAULT 0,
        trades_reached_4r INTEGER DEFAULT 0,
        trades_reached_5r_plus INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Create system_config table if not exists
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_config (
        key VARCHAR(100) PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    run_migration()
