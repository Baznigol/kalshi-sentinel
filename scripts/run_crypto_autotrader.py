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
import json
import math
import datetime as dt
from collections import deque
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


def _now():
    return dt.datetime.now().astimezone()


def _log(line: str, *, log_path: str | None = None):
    ts = _now().strftime('%Y-%m-%d %H:%M:%S%z')
    msg = f"[{ts}] {line}"
    print(msg, flush=True)
    if log_path:
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(msg + "\n")
        except Exception:
            pass


def main():
    if os.getenv("AUTO_TRADING_ENABLED", "false").lower() != "true":
        print("AUTO_TRADING_ENABLED is false; refusing to trade.")
        return

    # Optional allowlist for what the autotrader is allowed to touch.
    allow_prefixes = [p.strip() for p in os.getenv("TRADER_TICKER_ALLOW_PREFIXES", "KXBTC,KXBTC15M,KXBTCD,KXETH").split(",") if p.strip()]

    # External price feed (Coinbase spot)
    price_feed_url = os.getenv("TRADER_PRICE_FEED_URL", "https://api.coinbase.com/v2/prices/BTC-USD/spot")
    momentum_lookback = int(os.getenv("TRADER_MOMENTUM_LOOKBACK_SECONDS", "180"))
    momentum_threshold_bps = float(os.getenv("TRADER_MOMENTUM_THRESHOLD_BPS", "8"))  # 8 bps = 0.08%
    min_minutes_to_close = float(os.getenv("TRADER_MIN_MINUTES_TO_CLOSE", "2"))

    # Maintain rolling BTC spot samples
    spot = deque(maxlen=5000)

    port = int(os.getenv("PORT", "8099"))
    base = f"http://127.0.0.1:{port}"

    log_path = os.getenv("TRADER_LOG_PATH", os.path.join(REPO_DIR, "data", "autotrader.log"))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # Heartbeat logging
    heartbeat_every = int(os.getenv("TRADER_HEARTBEAT_EVERY_LOOPS", "5"))

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

    _log("Kalshi Sentinel AUTOTRADER v0", log_path=log_path)
    _log(f"Base={base}", log_path=log_path)
    _log(f"Allow prefixes={allow_prefixes}", log_path=log_path)
    _log(f"Max trades={max_trades} Target trades={target_trades} Target spend={target_spend_cents}c ForceComplete={force_complete}", log_path=log_path)
    _log(f"Max cost/trade={max_cost_trade}c Daily max cost={daily_max_cost}c Interval={interval}s HoursAhead={hours_ahead}", log_path=log_path)
    _log(f"Baseline gates: max_entry={base_max_entry_price_cents}c min_top_qty={base_min_top_qty}", log_path=log_path)
    _log(f"Cutoff={cutoff_s or '(none)'}", log_path=log_path)

    loops = 0
    stats = {
        "paper_calls": 0,
        "paper_empty": 0,
        "ob_calls": 0,
        "skips_price": 0,
        "skips_qty": 0,
        "skips_allow": 0,
        "skips_exposure": 0,
        "candidates_checked": 0,
        "orders_posted": 0,
        "order_errors": 0,
        "fills": 0,
    }

    while True:
        loops += 1
        now = _now()
        if cutoff and now >= cutoff:
            print("Cutoff reached; stopping.")
            break
        if target_spend_cents > 0 and spent >= target_spend_cents:
            _log("Target spend reached; stopping.", log_path=log_path)
            break
        if fills >= target_trades:
            _log("Target fills reached; stopping.", log_path=log_path)
            break
        if fills >= max_trades:
            _log("Max trades reached; stopping.", log_path=log_path)
            break
        if spent >= daily_max_cost:
            _log("Daily spend cap reached; stopping.", log_path=log_path)
            break

        # Deadline-aware relaxation
        max_entry_price_cents = base_max_entry_price_cents
        min_top_qty = base_min_top_qty
        tif = "fill_or_kill"

        secs_left = None
        mins_left = None
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

        # Spot price update (for BTC momentum signal)
        spot_px = None
        spot_ret_bps = None
        try:
            pr = requests.get(price_feed_url, timeout=10).json()
            amt = (pr.get("data") or {}).get("amount")
            if amt is not None:
                spot_px = float(amt)
                spot.append((time.time(), spot_px))

                # compute lookback return
                t_now = spot[-1][0]
                t_cut = t_now - momentum_lookback
                p0 = None
                for (t, p) in reversed(spot):
                    if t <= t_cut:
                        p0 = p
                        break
                if p0:
                    spot_ret_bps = ((spot_px / p0) - 1.0) * 10000.0
        except Exception as e:
            _log(f"spot feed error: {e}", log_path=log_path)

        # 1) get paper proposals (crypto discovery + scoring)
        try:
            stats["paper_calls"] += 1
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
            _log(f"paper error: {e}", log_path=log_path)
            time.sleep(interval)
            continue

        if not props:
            stats["paper_empty"] += 1
            if loops % heartbeat_every == 0:
                _log(
                    f"heartbeat loops={loops} fills={fills} spent={spent}c budget_left={(target_spend_cents-spent) if target_spend_cents>0 else (daily_max_cost-spent)}c "
                    f"mins_left={mins_left if mins_left is not None else '—'} paper_calls={stats['paper_calls']} paper_empty={stats['paper_empty']} btc_exp={btc_exposure}c eth_exp={eth_exposure}c",
                    log_path=log_path,
                )
            _log("no proposals; sleeping", log_path=log_path)
            time.sleep(interval)
            continue

        chosen = None
        chosen_side = None
        chosen_price = None
        chosen_title = None
        chosen_close_time = None

        # Try multiple proposals until one passes gates
        for p in props[:10]:
            stats["candidates_checked"] += 1
            ticker = p.get("ticker")
            title = (p.get("title") or "")
            close_time = p.get("close_time")
            if not ticker:
                continue
            if allow_prefixes and not any(str(ticker).startswith(px) for px in allow_prefixes):
                stats["skips_allow"] += 1
                continue

            # exposure gate
            if str(ticker).startswith("KXETH") and eth_exposure >= max_eth:
                stats["skips_exposure"] += 1
                continue
            if (str(ticker).startswith("KXBTC") or str(ticker).startswith("KXBTCD")) and btc_exposure >= max_btc:
                stats["skips_exposure"] += 1
                continue

            # fetch orderbook depth 5
            try:
                stats["ob_calls"] += 1
                ob = requests.get(base + f"/api/kalshi/markets/{ticker}/orderbook", params={"depth": 5}, timeout=30).json()
            except Exception as e:
                _log(f"orderbook error: {ticker} {e}", log_path=log_path)
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

            # Decide direction using BTC momentum for "up in next 15 mins" markets.
            # - If momentum is positive enough -> buy YES
            # - If momentum is negative enough -> buy NO (i.e., bet NOT up)
            # Otherwise skip.
            want_side = None
            if "UP" in title.upper() and "15" in title:
                if spot_ret_bps is not None:
                    if spot_ret_bps >= momentum_threshold_bps:
                        want_side = "yes"
                    elif spot_ret_bps <= -momentum_threshold_bps:
                        want_side = "no"
                else:
                    # No signal -> skip
                    want_side = None
            else:
                # Unknown market semantics; skip for safety
                want_side = None

            if not want_side:
                continue

            side = want_side
            price = implied_yes_ask if side == "yes" else implied_no_ask

            if price > max_entry_price_cents:
                stats["skips_price"] += 1
                _log(f"skip {ticker}: entry too expensive {price}c > {max_entry_price_cents}c", log_path=log_path)
                continue

            # Time-to-close gate (avoid trading the last ~minutes)
            try:
                if close_time:
                    # close_time comes like 2026-02-10T19:45:00Z
                    ct = dt.datetime.fromisoformat(str(close_time).replace('Z', '+00:00')).astimezone()
                    mins_to_close = (ct - now).total_seconds() / 60.0
                    if mins_to_close < min_minutes_to_close:
                        continue
            except Exception:
                pass

            top_qty = best_no_qty if side == "yes" else best_yes_qty
            if top_qty < min_top_qty:
                stats["skips_qty"] += 1
                _log(f"skip {ticker}: top-of-book qty too low {top_qty} < {min_top_qty}", log_path=log_path)
                continue

            chosen = ticker
            chosen_side = side
            chosen_price = max(1, min(99, int(price)))
            chosen_title = title
            chosen_close_time = close_time
            break

        if not chosen:
            if loops % heartbeat_every == 0:
                _log(
                    f"heartbeat loops={loops} fills={fills} spent={spent}c mins_left={mins_left if mins_left is not None else '—'} "
                    f"paper_props={len(props)} checked={stats['candidates_checked']} ob_calls={stats['ob_calls']} skips_price={stats['skips_price']} skips_qty={stats['skips_qty']}",
                    log_path=log_path,
                )
            _log("no candidates passed gates; sleeping", log_path=log_path)
            time.sleep(interval)
            continue

        ticker = chosen
        side = chosen_side
        price = chosen_price

        _log(
            f"select ticker={ticker} side={side.upper()} px={price}c title={chosen_title!r} close={chosen_close_time} "
            f"spot={spot_px if spot_px is not None else '—'} ret_bps={spot_ret_bps if spot_ret_bps is not None else '—'}",
            log_path=log_path,
        )

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
            stats["orders_posted"] += 1
            resp = requests.post(base + "/api/kalshi/orders", json=payload, timeout=60)
            try:
                data = resp.json()
            except Exception:
                stats["order_errors"] += 1
                _log(f"order post non-json: {resp.status_code} {(resp.text or '')[:300]}", log_path=log_path)
                time.sleep(interval)
                continue
        except Exception as e:
            stats["order_errors"] += 1
            _log(f"order post error: {e}", log_path=log_path)
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
            _log(f"NO FILL: {ticker} {side.upper()} @ {price}c tif={payload.get('time_in_force')}", log_path=log_path)
        else:
            trade_cost = filled_cost if filled_cost is not None else payload["buy_max_cost"]
            spent += trade_cost
            fills += 1
            stats["fills"] = fills
            _log(
                f"FILL #{fills}: {ticker} BUY {side.upper()} @ {price}c qty={filled_qty} cost={trade_cost}c spent={spent}c "
                f"budget_left={(target_spend_cents-spent) if target_spend_cents>0 else (daily_max_cost-spent)}c tif={payload.get('time_in_force')}",
                log_path=log_path,
            )

        # brief structured response for debugging
        try:
            _log("response: " + json.dumps(data)[:400], log_path=log_path)
        except Exception:
            _log("response: " + str(data)[:400], log_path=log_path)

        time.sleep(interval)


if __name__ == "__main__":
    main()
