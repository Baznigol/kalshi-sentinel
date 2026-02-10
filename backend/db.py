import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
DB_PATH = os.path.join(REPO_DIR, "data", "kalshi.db")


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db = get_db()
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                payload TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                payload TEXT NOT NULL
            )
            """
        )

        # Paper trading + audit trail
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                component TEXT NOT NULL,
                message TEXT NOT NULL,
                data_json TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,          -- YES or NO
                action TEXT NOT NULL,        -- BUY or SELL
                limit_price_cents INTEGER,
                contracts INTEGER,
                estimated_max_loss_cents INTEGER,
                status TEXT NOT NULL,        -- PROPOSED / FILLED / CANCELED / CLOSED
                rationale TEXT,
                market_json TEXT,
                orderbook_json TEXT
            )
            """
        )
        db.commit()
    finally:
        db.close()


def db_health():
    try:
        db = get_db()
        db.execute("SELECT 1")
        db.close()
        return True, "connected"
    except Exception as e:
        return False, str(e)
