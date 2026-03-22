import sqlite3
import os

DB_PATH = 'trades.db'

def get_connection():
    # Return a connection that generates dict-like row objects
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            entry_price REAL,
            stop_loss REAL,
            target_price REAL,
            quantity INTEGER,
            status TEXT,
            buy_price REAL,
            sell_price REAL,
            pnl REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
    ''')
    
    # User risk settings table
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            account_size REAL DEFAULT 10000.0,
            risk_pct REAL DEFAULT 1.0,
            max_daily_loss REAL DEFAULT 500.0,
            max_daily_trades INTEGER DEFAULT 5
        )
    ''')
    
    # Run migrations for existing DBs
    try:
        c.execute("ALTER TABLE trades ADD COLUMN buy_price REAL")
        c.execute("ALTER TABLE trades ADD COLUMN sell_price REAL")
        c.execute("ALTER TABLE trades ADD COLUMN pnl REAL")
    except sqlite3.OperationalError:
        pass # Columns already exist
        
    try:
        c.execute("ALTER TABLE trades ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        c.execute("ALTER TABLE trades ADD COLUMN closed_at TIMESTAMP")
    except sqlite3.OperationalError:
        pass
        
    conn.commit()
    conn.close()

def get_user_settings(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        # Create default
        c.execute('''
            INSERT INTO user_settings (user_id) VALUES (?)
        ''', (user_id,))
        conn.commit()
        c.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
        row = c.fetchone()
    conn.close()
    return dict(row)

def update_user_settings(user_id, field, value):
    conn = get_connection()
    c = conn.cursor()
    # ensure it exists
    get_user_settings(user_id)
    # dynamically update field (always safe names in code)
    c.execute(f"UPDATE user_settings SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def get_daily_metrics(user_id):
    conn = get_connection()
    c = conn.cursor()
    # Number of trades taken today (based on creation)
    c.execute("SELECT COUNT(*) as trades_today FROM trades WHERE user_id = ? AND date(created_at) = date('now')", (user_id,))
    trades_today = c.fetchone()['trades_today']
    
    # PnL realized today (based on closure)
    c.execute("SELECT SUM(pnl) as pnl_today FROM trades WHERE user_id = ? AND status IN ('CLOSED_SL', 'CLOSED_TARGET') AND date(closed_at) = date('now')", (user_id,))
    pnl_today = c.fetchone()['pnl_today'] or 0.0
    conn.close()
    
    return {'trades_today': trades_today, 'pnl_today': pnl_today}

def add_trade(user_id, symbol, entry_price, stop_loss, target_price, quantity):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO trades (user_id, symbol, entry_price, stop_loss, target_price, quantity, status)
        VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
    ''', (user_id, symbol.upper(), entry_price, stop_loss, target_price, quantity))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def get_trades_by_status(status_list):
    conn = get_connection()
    c = conn.cursor()
    placeholders = ','.join(['?'] * len(status_list))
    c.execute(f'SELECT * FROM trades WHERE status IN ({placeholders})', status_list)
    trades = c.fetchall()
    conn.close()
    return [dict(t) for t in trades]

def get_user_trades(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM trades WHERE user_id = ? ORDER BY id DESC', (user_id,))
    trades = c.fetchall()
    conn.close()
    return [dict(t) for t in trades]

def update_trade_execution(trade_id, status, current_price=None, pnl=None):
    conn = get_connection()
    c = conn.cursor()
    if status == 'ACTIVE' and current_price is not None:
        c.execute('UPDATE trades SET status = ?, buy_price = ? WHERE id = ?', (status, current_price, trade_id))
    elif status in ['CLOSED_SL', 'CLOSED_TARGET'] and current_price is not None:
        c.execute("UPDATE trades SET status = ?, sell_price = ?, pnl = ?, closed_at = datetime('now') WHERE id = ?", (status, current_price, pnl, trade_id))
    else:
        c.execute('UPDATE trades SET status = ? WHERE id = ?', (status, trade_id))
    conn.commit()
    conn.close()

def get_user_trade_history(user_id, limit=10):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE user_id = ? AND status IN ('CLOSED_SL', 'CLOSED_TARGET') ORDER BY id DESC LIMIT ?", (user_id, limit))
    trades = c.fetchall()
    conn.close()
    return [dict(t) for t in trades]

def get_user_trade_stats(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT status, COUNT(*) as cnt, SUM(pnl) as total_pnl FROM trades WHERE user_id = ? GROUP BY status", (user_id,))
    rows = c.fetchall()
    conn.close()
    
    stats = {'total_trades': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0.0}
    for row in rows:
        status, cnt, pnl = row['status'], row['cnt'], row['total_pnl']
        stats['total_trades'] += cnt
        if status in ['CLOSED_SL', 'CLOSED_TARGET']:
            stats['total_pnl'] += pnl or 0.0
            if (pnl or 0.0) > 0:
                stats['wins'] += cnt
            else:
                stats['losses'] += cnt
    
    # Handle division safely for win rate
    total_closed = stats['wins'] + stats['losses']
    stats['win_rate'] = (stats['wins'] / total_closed * 100) if total_closed > 0 else 0.0
    
    return stats
    
def get_trade_by_id(trade_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM trades WHERE id = ?', (trade_id,))
    trade = c.fetchone()
    conn.close()
    return dict(trade) if trade else None
