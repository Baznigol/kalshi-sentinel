import time
from typing import Any, Dict, List

from kalshi_client import KalshiClient


def _best_bids_from_orderbook(ob: Dict[str, Any]) -> Dict[str, int]:
    book = (ob or {}).get("orderbook") or {}
    yes = book.get("yes") or []
    no = book.get("no") or []
    best_yes_bid = int(yes[0][0]) if yes else 0
    best_no_bid = int(no[0][0]) if no else 0
    implied_yes_ask = 100 - best_no_bid if best_no_bid else 0
    implied_no_ask = 100 - best_yes_bid if best_yes_bid else 0
    return {
        "best_yes_bid": best_yes_bid,
        "best_no_bid": best_no_bid,
        "implied_yes_ask": implied_yes_ask,
        "implied_no_ask": implied_no_ask,
    }


def positions_mtm(kc: KalshiClient, *, limit: int = 50) -> Dict[str, Any]:
    """Return a human-friendly mark-to-market snapshot for positions.

    Uses:
    - /portfolio/positions for sizes + total_traded cost basis (cents)
    - /markets/{ticker}/orderbook for best bids

    NOTE: This is an approximation (uses best bid as liquidation price, ignores slippage).
    """

    pos = kc.get("/portfolio/positions")
    mpos: List[Dict[str, Any]] = pos.get("market_positions", []) or []

    rows = []
    for p in mpos[:limit]:
        ticker = p.get("ticker")
        position = float(p.get("position_fp") or p.get("position") or 0)
        position = int(position)
        total_traded = int(p.get("total_traded") or 0)  # cents
        fees_paid = int(p.get("fees_paid") or 0)
        if not ticker or position == 0:
            continue

        try:
            ob = kc.get(f"/markets/{ticker}/orderbook", params={"depth": 1})
            bids = _best_bids_from_orderbook(ob)
        except Exception:
            bids = {"best_yes_bid": 0, "best_no_bid": 0, "implied_yes_ask": 0, "implied_no_ask": 0}

        # We assume these positions are YES-only for now (your bot currently only buys YES).
        # If you later trade NO or sell, we should extend this with side-aware tracking from fills.
        best_exit_cents = bids["best_yes_bid"]
        liq_value = position * best_exit_cents
        unreal_pnl = liq_value - total_traded

        avg_entry = (total_traded / position) if position else 0.0

        rows.append(
            {
                "ticker": ticker,
                "position": position,
                "avg_entry_cents": round(avg_entry, 2),
                "cost_basis_cents": total_traded,
                "fees_paid_cents": fees_paid,
                "best_yes_bid": bids["best_yes_bid"],
                "implied_yes_ask": bids["implied_yes_ask"],
                "liq_value_cents": liq_value,
                "unreal_pnl_cents": unreal_pnl,
                "updated_ms": int(time.time() * 1000),
            }
        )

    total_cost = sum(r["cost_basis_cents"] for r in rows)
    total_liq = sum(r["liq_value_cents"] for r in rows)
    total_unreal = sum(r["unreal_pnl_cents"] for r in rows)

    return {
        "rows": rows,
        "totals": {
            "cost_basis_cents": total_cost,
            "liq_value_cents": total_liq,
            "unreal_pnl_cents": total_unreal,
        },
    }
