import os
import sqlite3
import time
from collections import defaultdict
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


def round_trips(days: int = 30, limit: int = 200) -> Dict[str, Any]:
    """FIFO round-trip pairing of BUY→SELL on (ticker, side).

    For each (ticker, side), consume buys in order, pair them with sells in order.
    A round trip is closed when sell qty fully matches a buy (or partial).
    Returns per-trip PnL and aggregate stats.
    """

    path = _ledger_path()
    if not os.path.exists(path):
        return {"error": "ledger_not_found", "path": path}

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    # Fetch all trades in the window, ordered chronologically
    cur = conn.execute(
        """
        SELECT id, ts, day, ticker, side, action, price_cents, qty, cost_cents, order_id
        FROM live_trades
        WHERE day >= date('now', ?)
        ORDER BY id ASC
        """,
        (f"-{int(days)} day",),
    )
    rows = cur.fetchall()
    conn.close()

    # Group trades by (ticker, side)
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = (r["ticker"], r["side"])
        groups[key].append(dict(r))

    trips: List[Dict[str, Any]] = []
    summary = {
        "total_trips": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "total_pnl_cents": 0,
        "total_buy_cost_cents": 0,
        "total_sell_proceeds_cents": 0,
        "open_positions": 0,
    }

    for (ticker, side), trades in groups.items():
        buys = [t for t in trades if t["action"] == "buy"]
        sells = [t for t in trades if t["action"] == "sell"]

        # FIFO pairing
        bi = 0  # buy index
        si = 0  # sell index
        buy_remaining = 0  # remaining qty from current buy
        buy_avg_cost_per_unit = 0.0

        while bi < len(buys) and si < len(sells):
            if buy_remaining <= 0:
                b = buys[bi]
                buy_remaining = int(b["qty"])
                buy_avg_cost_per_unit = int(b["cost_cents"]) / max(1, int(b["qty"]))

            s = sells[si]
            sell_qty = int(s["qty"])
            sell_cost_per_unit = int(s["cost_cents"]) / max(1, sell_qty)

            matched_qty = min(buy_remaining, sell_qty)
            if matched_qty <= 0:
                si += 1
                continue

            entry_cost = int(round(buy_avg_cost_per_unit * matched_qty))
            exit_proceeds = int(round(sell_cost_per_unit * matched_qty))
            pnl = exit_proceeds - entry_cost

            trip = {
                "ticker": ticker,
                "side": side,
                "qty": matched_qty,
                "entry_price_cents": int(buys[bi]["price_cents"]),
                "exit_price_cents": int(s["price_cents"]),
                "entry_cost_cents": entry_cost,
                "exit_proceeds_cents": exit_proceeds,
                "pnl_cents": pnl,
                "entry_ts": buys[bi]["ts"],
                "exit_ts": s["ts"],
                "entry_order_id": buys[bi].get("order_id"),
                "exit_order_id": s.get("order_id"),
            }

            if len(trips) < limit:
                trips.append(trip)

            summary["total_trips"] += 1
            summary["total_pnl_cents"] += pnl
            summary["total_buy_cost_cents"] += entry_cost
            summary["total_sell_proceeds_cents"] += exit_proceeds
            if pnl > 0:
                summary["wins"] += 1
            elif pnl < 0:
                summary["losses"] += 1
            else:
                summary["breakeven"] += 1

            buy_remaining -= matched_qty
            sell_qty -= matched_qty

            if sell_qty <= 0:
                si += 1
            # Consume partial sell; update sell record for next iteration
            if sell_qty > 0:
                sells[si] = dict(sells[si])
                sells[si]["qty"] = sell_qty
                sells[si]["cost_cents"] = int(round(sell_cost_per_unit * sell_qty))

            if buy_remaining <= 0:
                bi += 1

        # Count remaining open buys
        open_qty = buy_remaining
        for remaining_b in buys[bi + (1 if buy_remaining <= 0 else 0):]:
            open_qty += int(remaining_b["qty"])
        if open_qty > 0:
            summary["open_positions"] += 1

    # Win rate
    closed = summary["wins"] + summary["losses"] + summary["breakeven"]
    summary["win_rate"] = round(summary["wins"] / max(1, closed), 4)
    summary["avg_pnl_cents"] = round(summary["total_pnl_cents"] / max(1, closed), 2)

    return {
        "updated_ms": int(time.time() * 1000),
        "days": days,
        "summary": summary,
        "trips": trips,
    }
