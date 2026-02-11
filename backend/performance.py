import datetime as dt
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from kalshi_client import KalshiClient


def _parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _cents_from_fill(fill: Dict[str, Any]) -> Tuple[int, int]:
    """Return (notional_cents, fee_cents) for a Kalshi fill.

    Kalshi fills include `price` as dollars (float) and `count` as contracts.
    Fee is `fee_cost` in dollars string.
    """
    cnt = int(float(fill.get("count_fp") or fill.get("count") or 0) or 0)
    px = fill.get("price")
    if px is None:
        # fallback to yes/no price cents when present
        if fill.get("side") == "yes":
            px = float(fill.get("yes_price", 0)) / 100.0
        else:
            px = float(fill.get("no_price", 0)) / 100.0
    notional_cents = int(round(float(px or 0) * 100.0 * cnt))

    fee_cost = fill.get("fee_cost")
    try:
        fee_cents = int(round(float(fee_cost or 0) * 100.0))
    except Exception:
        fee_cents = 0

    return notional_cents, fee_cents


def kalshi_performance(kc: KalshiClient, *, hours: int = 24, limit: int = 200) -> Dict[str, Any]:
    """Compute a lightweight performance snapshot from Kalshi fills (source of truth).

    - realized_pnl is approximated from buys vs sells cashflows over the window.
    - For buys: cashflow = -(notional + fee)
    - For sells: cashflow = +(notional - fee)

    This ignores inventory mark-to-market; pair with positions_mtm for unrealized.
    """

    data = kc.get("/portfolio/fills", params={"limit": limit})
    fills: List[Dict[str, Any]] = data.get("fills", []) or []

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=int(hours))

    cashflow_cents = 0
    by_ticker = defaultdict(lambda: {"buy_cents": 0, "sell_cents": 0, "fees_cents": 0, "fills": 0})

    considered = 0
    for f in fills:
        t = _parse_time(f.get("created_time") or f.get("executed_time"))
        if t and t < cutoff:
            continue

        notional_c, fee_c = _cents_from_fill(f)
        act = (f.get("action") or "").lower()
        ticker = f.get("ticker") or f.get("market_ticker") or ""

        if act == "buy":
            cashflow_cents -= (notional_c + fee_c)
            by_ticker[ticker]["buy_cents"] += notional_c
            by_ticker[ticker]["fees_cents"] += fee_c
        elif act == "sell":
            cashflow_cents += (notional_c - fee_c)
            by_ticker[ticker]["sell_cents"] += notional_c
            by_ticker[ticker]["fees_cents"] += fee_c
        else:
            # unknown action
            continue

        by_ticker[ticker]["fills"] += 1
        considered += 1

    rows = []
    for ticker, v in by_ticker.items():
        rows.append({"ticker": ticker, **v, "net_cashflow_cents": v["sell_cents"] - v["buy_cents"] - v["fees_cents"]})
    rows.sort(key=lambda r: abs(r["net_cashflow_cents"]), reverse=True)

    return {
        "hours": hours,
        "limit": limit,
        "fills_considered": considered,
        "realized_cashflow_cents": cashflow_cents,
        "by_ticker": rows[:50],
    }
