import os
import sqlite3
import time
from typing import Any, Dict, List, Tuple


def _repo_dir() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _ledger_path() -> str:
    # Should match autotrader default; allow override for backend too.
    p = os.getenv("TRADER_LEDGER_PATH", os.path.join(_repo_dir(), "data", "trades.sqlite"))
    # Allow relative paths
    if not os.path.isabs(p):
        p = os.path.join(_repo_dir(), p)
    return p


def ledger_summary(days: int = 7, limit: int = 200) -> Dict[str, Any]:
    """Summarize local autotrader ledger.

    This is *local* truth (what the bot recorded), not canonical Kalshi truth.
    Good enough for iterative ops + dashboards.
    """

    path = _ledger_path()
    if not os.path.exists(path):
        return {"error": "ledger_not_found", "path": path}

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    # Daily aggregates
    cur = conn.execute(
        """
        SELECT
          day,
          SUM(CASE WHEN action='buy' THEN cost_cents ELSE 0 END) AS buy_cents,
          SUM(CASE WHEN action='sell' THEN cost_cents ELSE 0 END) AS sell_cents,
          COUNT(*) AS trades
        FROM live_trades
        WHERE day >= date('now', ?)
        GROUP BY day
        ORDER BY day DESC
        """,
        (f"-{int(days)} day",),
    )
    daily = []
    for r in cur.fetchall():
        buy_cents = int(r["buy_cents"] or 0)
        sell_cents = int(r["sell_cents"] or 0)
        realized = sell_cents - buy_cents
        daily.append(
            {
                "day": r["day"],
                "buy_cents": buy_cents,
                "sell_cents": sell_cents,
                "realized_pnl_cents": realized,
                "trades": int(r["trades"] or 0),
            }
        )

    # Recent rows
    cur2 = conn.execute(
        "SELECT ts, day, ticker, side, action, price_cents, qty, cost_cents, order_id FROM live_trades ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    recent = [dict(x) for x in cur2.fetchall()]

    # Totals (over window)
    total_buy = sum(d["buy_cents"] for d in daily)
    total_sell = sum(d["sell_cents"] for d in daily)

    return {
        "updated_ms": int(time.time() * 1000),
        "path": path,
        "days": days,
        "totals": {
            "buy_cents": total_buy,
            "sell_cents": total_sell,
            "realized_pnl_cents": total_sell - total_buy,
        },
        "daily": daily,
        "recent": recent,
    }
