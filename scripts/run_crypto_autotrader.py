#!/usr/bin/env python3
"""Crypto autotrader (v0).

WARNING: This is an early execution loop. It is intentionally conservative:
- Uses Kalshi orderbook to compute implied ask
- Uses Fill-or-Kill orders
- Hard caps max spend per trade and max trades per day
- Stops at a configured cutoff time

It trades only when AUTO_TRADING_ENABLED=true in config/.env

Config (config/.env):
- AUTO_TRADING_ENABLED=true
- TRADER_MAX_TRADES=5
- TRADER_MAX_COST_CENTS_PER_TRADE=200
- TRADER_DAILY_MAX_COST_CENTS=500
- TRADER_INTERVAL_SECONDS=120
- TRADER_HOURS_AHEAD=8
- TRADER_CUTOFF_LOCAL=2026-02-10T17:00:00-05:00

This script calls the local backend (http://127.0.0.1:8099), so backend must be running.
"""

import os
import time
import datetime as dt
from dotenv import load_dotenv
import requests

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(REPO_DIR, "config", ".env"))


def parse_iso(s: str):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def main():
    if os.getenv("AUTO_TRADING_ENABLED", "false").lower() != "true":
        print("AUTO_TRADING_ENABLED is false; refusing to trade.")
        return

    # Optional allowlist for what the autotrader is allowed to touch.
    allow_prefixes = [p.strip() for p in os.getenv("TRADER_TICKER_ALLOW_PREFIXES", "KXBTC,KXBTC15M,KXBTCD,KXETH").split(",") if p.strip()]

    port = int(os.getenv("PORT", "8099"))
    base = f"http://127.0.0.1:{port}"

    max_trades = int(os.getenv("TRADER_MAX_TRADES", "5"))
    target_trades = int(os.getenv("TRADER_TARGET_TRADES", str(max_trades)))
    target_spend_cents = int(os.getenv("TRADER_TARGET_SPEND_CENTS", "0"))

    max_cost_trade = int(os.getenv("TRADER_MAX_COST_CENTS_PER_TRADE", "200"))
    daily_max_cost = int(os.getenv("TRADER_DAILY_MAX_COST_CENTS", "500"))
    force_complete = os.getenv("TRADER_FORCE_COMPLETE", "false").lower() == "true"

    interval = int(os.getenv("TRADER_INTERVAL_SECONDS", "120"))
    hours_ahead = int(os.getenv("TRADER_HOURS_AHEAD", "8"))

    # Microstructure gates (baseline; may relax as cutoff approaches)
    base_max_entry_price_cents = int(os.getenv("TRADER_MAX_ENTRY_PRICE_CENTS", "30"))
    base_min_top_qty = int(os.getenv("TRADER_MIN_TOP_QTY", "50"))

    # If user wants "get N fills no matter what", ensure spend cap at least allows N * max_cost_trade.
    if force_complete:
        daily_max_cost = max(daily_max_cost, target_trades * max_cost_trade)

    cutoff_s = os.getenv("TRADER_CUTOFF_LOCAL", "")
    cutoff = parse_iso(cutoff_s) if cutoff_s else None

    spent = 0
    fills = 0

    print("Kalshi Sentinel AUTOTRADER v0")
    print("Base:", base)
    print("Max trades:", max_trades)
    print("Target trades:", target_trades)
    print("Force complete:", force_complete)
    print("Max cost/trade (cents):", max_cost_trade)
    print("Daily max cost (cents):", daily_max_cost)
    print("Interval (sec):", interval)
    print("Cutoff:", cutoff_s or "(none)")

    while True:
        now = dt.datetime.now().astimezone()
        if cutoff and now >= cutoff:
            print("Cutoff reached; stopping.")
            break
        if target_spend_cents > 0 and spent >= target_spend_cents:
            print("Target spend reached; stopping.")
            break
        if fills >= target_trades:
            print("Target fills reached; stopping.")
            break
        if fills >= max_trades:
            print("Max trades reached; stopping.")
            break
        if spent >= daily_max_cost:
            print("Daily spend cap reached; stopping.")
            break

        # Deadline-aware relaxation
        max_entry_price_cents = base_max_entry_price_cents
        min_top_qty = base_min_top_qty
        tif = "fill_or_kill"

        if cutoff:
            secs_left = max(0, int((cutoff - now).total_seconds()))
            mins_left = secs_left / 60.0
            fills_left = max(0, target_trades - fills)
            loops_left = max(1, int(secs_left // max(1, interval)))

            # If we're behind pace (need >=1 fill per remaining loop), relax aggressively.
            behind = fills_left >= loops_left

            if mins_left <= 5 or behind:
                max_entry_price_cents = max(max_entry_price_cents, 70)
                min_top_qty = min(min_top_qty, 10)
                tif = "immediate_or_cancel"
            elif mins_left <= 20:
                max_entry_price_cents = max(max_entry_price_cents, 60)
                min_top_qty = min(min_top_qty, 20)
                tif = "immediate_or_cancel"
            elif mins_left <= 45:
                max_entry_price_cents = max(max_entry_price_cents, 50)
                min_top_qty = min(min_top_qty, 30)
            elif mins_left <= 90:
                max_entry_price_cents = max(max_entry_price_cents, 40)
                min_top_qty = min(min_top_qty, 40)

        # 0) position-aware throttling (avoid stacking correlated BTC/ETH exposure)
        try:
            pos = requests.get(base + "/api/kalshi/portfolio/positions", timeout=30).json()
            mpos = pos.get("market_positions", []) or []
            btc_exposure = sum(int(x.get("market_exposure") or 0) for x in mpos if str(x.get("ticker","")).startswith("KXBTC") or str(x.get("ticker","")).startswith("KXBTCD"))
            eth_exposure = sum(int(x.get("market_exposure") or 0) for x in mpos if str(x.get("ticker","")).startswith("KXETH"))
        except Exception:
            btc_exposure = 0
            eth_exposure = 0

        max_btc = int(os.getenv("TRADER_MAX_BTC_EXPOSURE_CENTS", "2000"))
        max_eth = int(os.getenv("TRADER_MAX_ETH_EXPOSURE_CENTS", "2000"))

        # available cash guard
        try:
            bal = requests.get(base + "/api/kalshi/portfolio/balance", timeout=30).json()
            avail_cents = int(float(bal.get("balance", 0)) * 100)
        except Exception:
            avail_cents = 0

        # 1) get paper proposals (crypto discovery + scoring)
        try:
            r = requests.post(
                base + "/api/paper/run_today",
                json={
                    "hours_ahead": hours_ahead,
                    "budget_dollars": 10,
                    "max_trades": max_trades,
                    "ticker_prefixes": allow_prefixes,
                },
                timeout=30,
            )
            j = r.json()
            props = j.get("proposed", [])
        except Exception as e:
            print("paper error:", e)
            time.sleep(interval)
            continue

        if not props:
            print("no proposals; sleeping")
            time.sleep(interval)
            continue

        chosen = None
        chosen_ob = None
        chosen_side = None
        chosen_price = None

        # Try multiple proposals until one passes gates
        for p in props[:10]:
            ticker = p.get("ticker")
            if not ticker:
                continue
            if allow_prefixes and not any(str(ticker).startswith(px) for px in allow_prefixes):
                continue

            # exposure gate
            if str(ticker).startswith("KXETH") and eth_exposure >= max_eth:
                continue
            if (str(ticker).startswith("KXBTC") or str(ticker).startswith("KXBTCD")) and btc_exposure >= max_btc:
                continue

            # fetch orderbook depth 5
            try:
                ob = requests.get(base + f"/api/kalshi/markets/{ticker}/orderbook", params={"depth": 5}, timeout=30).json()
            except Exception as e:
                print("orderbook error:", ticker, e)
                continue

            book = (ob.get("orderbook") or {})
            yes = book.get("yes") or []  # list of [price, qty]
            no = book.get("no") or []
            if not yes or not no:
                continue

            best_yes_bid, best_yes_qty = int(yes[0][0]), int(yes[0][1])
            best_no_bid, best_no_qty = int(no[0][0]), int(no[0][1])

            implied_yes_ask = 100 - best_no_bid
            implied_no_ask = 100 - best_yes_bid

            side = "yes" if implied_yes_ask <= implied_no_ask else "no"
            price = implied_yes_ask if side == "yes" else implied_no_ask

            if price > max_entry_price_cents:
                print(f"skip {ticker}: entry too expensive {price}c > {max_entry_price_cents}c")
                continue

            top_qty = best_no_qty if side == "yes" else best_yes_qty
            if top_qty < min_top_qty:
                print(f"skip {ticker}: top-of-book qty too low {top_qty} < {min_top_qty}")
                continue

            chosen = ticker
            chosen_ob = ob
            chosen_side = side
            chosen_price = max(1, min(99, int(price)))
            break

        if not chosen:
            print("no candidates passed gates; sleeping")
            time.sleep(interval)
            continue

        ticker = chosen
        side = chosen_side
        price = chosen_price

        # Set count consistent with buy_max_cost so exchange doesn't reject on worst-case notional.
        # For a BUY at price cents, max fillable contracts <= floor(max_cost_cents / price).
        # We'll compute count after we compute buy_max_cost.
        count = None

        # 3) place FoK order (limit)
        buy_max_cost = min(max_cost_trade, daily_max_cost - spent)
        if target_spend_cents > 0:
            buy_max_cost = min(buy_max_cost, max(0, target_spend_cents - spent))
        if avail_cents > 0:
            buy_max_cost = min(buy_max_cost, max(0, avail_cents - 25))  # keep small buffer
        if buy_max_cost <= 0:
            print("No remaining budget/cash; stopping")
            break
        # Determine a sensible contract count that cannot exceed the max cost.
        count = max(1, buy_max_cost // max(1, price))

        payload = {
            "ticker": ticker,
            "side": side,
            "action": "buy",
            "type": "limit",
            "count": count,
            "buy_max_cost": buy_max_cost,
            "time_in_force": tif,
        }
        if side == "yes":
            payload["yes_price"] = price
        else:
            payload["no_price"] = price

        try:
            resp = requests.post(base + "/api/kalshi/orders", json=payload, timeout=60)
            try:
                data = resp.json()
            except Exception:
                print("order post non-json:", resp.status_code, (resp.text or "")[:300])
                time.sleep(interval)
                continue
        except Exception as e:
            print("order post error:", e)
            time.sleep(interval)
            continue

        # If the order API is disabled, stop.
        if isinstance(data, dict) and data.get("error") == "AUTO_TRADING_ENABLED is false":
            print("AUTO_TRADING disabled on server; stopping")
            break

        # Cost accounting using returned Order fields when available.
        filled_cost = None
        filled_qty = None
        if isinstance(data, dict) and isinstance(data.get('order'), dict):
            o = data['order']
            try:
                filled_qty = int(o.get('fill_count', 0) or 0)
                taker_cost = int(o.get('taker_fill_cost', 0) or 0)
                maker_cost = int(o.get('maker_fill_cost', 0) or 0)
                taker_fees = int(o.get('taker_fees', 0) or 0)
                maker_fees = int(o.get('maker_fees', 0) or 0)
                filled_cost = taker_cost + maker_cost + taker_fees + maker_fees
            except Exception:
                filled_cost = None

        if filled_qty is None:
            filled_qty = 0

        if filled_qty <= 0:
            print(f"NO FILL: {ticker} {side.upper()} @ {price}c ({payload.get('time_in_force')})")
        else:
            trade_cost = filled_cost if filled_cost is not None else payload["buy_max_cost"]
            spent += trade_cost
            fills += 1
            print(f"FILL #{fills}: {ticker} BUY {side.upper()} @ {price}c qty={filled_qty} cost={trade_cost}c (spent={spent}c) tif={payload.get('time_in_force')}")

        print("response:", str(data)[:400])

        time.sleep(interval)


if __name__ == "__main__":
    main()
