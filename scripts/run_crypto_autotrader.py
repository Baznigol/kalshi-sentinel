#!/usr/bin/env python3
"""Crypto autotrader (v0).

WARNING: This is an early execution loop. It is intentionally conservative:
- Uses Kalshi orderbook to compute implied ask
- Uses Fill-or-Kill orders
- Hard caps max spend per trade and max net spend per day
- Runs continuously until you stop it (Ctrl+C)

It trades only when AUTO_TRADING_ENABLED=true in config/.env

Config (config/.env):
- AUTO_TRADING_ENABLED=true
- TRADER_MAX_COST_CENTS_PER_TRADE=200
- TRADER_DAILY_MAX_COST_CENTS=1000   # interpreted as max NET spend per local day
- TRADER_INTERVAL_SECONDS=120
- TRADER_HOURS_AHEAD=8

This script calls the local backend (http://127.0.0.1:8099), so backend must be running.
"""

import os
import time
import json
import math
import sqlite3
import datetime as dt
from collections import deque
from dotenv import load_dotenv
import requests


def _norm_cdf(x: float) -> float:
    # standard normal CDF via erf
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _parse_price(s: str) -> float | None:
    try:
        return float(str(s).replace('$','').replace(',','').strip())
    except Exception:
        return None


def _parse_range_subtitle(subtitle: str) -> tuple[float | None, float | None]:
    """Parse KXBTC range subtitles.

    Examples:
      "$78,250 or above" -> (78250, None)
      "$59,999.99 or below" -> (None, 59999.99)
      "$77,500 to 77,749.99" -> (77500, 77749.99)
    """
    if not subtitle:
        return (None, None)
    t = subtitle.replace('\u00a0', ' ').strip()
    u = t.upper()
    if "OR ABOVE" in u:
        a = t.split("or", 1)[0]
        return (_parse_price(a), None)
    if "OR BELOW" in u:
        a = t.split("or", 1)[0]
        return (None, _parse_price(a))
    if "TO" in u:
        parts = t.split("to")
        if len(parts) >= 2:
            lo = _parse_price(parts[0])
            hi = _parse_price(parts[1])
            return (lo, hi)
    return (None, None)

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


def _add_reject(rejects: list[dict], reason: str, *, penalty: float | None = None, **fields):
    """Collect non-spammy reject diagnostics.

    We only log these on heartbeat when no candidate passes.
    `penalty` is "distance from passing"; smaller is closer.
    """
    try:
        if rejects is None:
            return
        if len(rejects) >= int(os.getenv("TRADER_REJECT_LOG_MAX", "30")):
            return
        rec = {"reason": reason}
        if penalty is not None:
            rec["penalty"] = float(penalty)
        for k, v in fields.items():
            rec[k] = v
        rejects.append(rec)
    except Exception:
        return


def _send_telegram(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _db(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_trades (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          day TEXT NOT NULL,
          ticker TEXT NOT NULL,
          side TEXT NOT NULL,          -- yes|no
          action TEXT NOT NULL,        -- buy|sell
          price_cents INTEGER NOT NULL,
          qty INTEGER NOT NULL,
          cost_cents INTEGER NOT NULL,
          order_id TEXT,
          raw_json TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_live_trades_day ON live_trades(day)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_live_trades_ticker ON live_trades(ticker)")
    conn.commit()
    return conn


def _record_trade(conn, *, ticker: str, side: str, action: str, price_cents: int, qty: int, cost_cents: int, order_id: str | None, raw: dict):
    ts = _now().isoformat()
    day = str(_now().date())
    conn.execute(
        "INSERT INTO live_trades(ts, day, ticker, side, action, price_cents, qty, cost_cents, order_id, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, day, ticker, side, action, int(price_cents), int(qty), int(cost_cents), order_id, json.dumps(raw)[:5000]),
    )
    conn.commit()


def _last_entry_ts(conn, *, ticker: str, side: str) -> str | None:
    cur = conn.execute(
        "SELECT ts FROM live_trades WHERE ticker=? AND side=? AND action='buy' ORDER BY id DESC LIMIT 1",
        (ticker, side),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _today_pnl(conn) -> int:
    """Realized PnL in cents for today from our local ledger.

    Approximation: realized = sells - buys (same day). Good enough as a guardrail.
    """
    day = str(_now().date())
    cur = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN action='sell' THEN cost_cents ELSE 0 END),0) - COALESCE(SUM(CASE WHEN action='buy' THEN cost_cents ELSE 0 END),0) FROM live_trades WHERE day=?",
        (day,),
    )
    v = cur.fetchone()[0]
    return int(v or 0)


def main():
    if os.getenv("AUTO_TRADING_ENABLED", "false").lower() != "true":
        print("AUTO_TRADING_ENABLED is false; refusing to trade.")
        return

    # Optional allowlist for what the autotrader is allowed to touch.
    allow_prefixes = [p.strip() for p in os.getenv("TRADER_TICKER_ALLOW_PREFIXES", "KXBTC,KXBTC15M,KXBTCD,KXETH").split(",") if p.strip()]

    # External price feed (Coinbase spot)
    price_feed_url = os.getenv("TRADER_PRICE_FEED_URL", "https://api.coinbase.com/v2/prices/BTC-USD/spot")
    momentum_lookback = int(os.getenv("TRADER_MOMENTUM_LOOKBACK_SECONDS", "120"))
    momentum_threshold_bps = float(os.getenv("TRADER_MOMENTUM_THRESHOLD_BPS", "4"))  # 4 bps = 0.04%
    min_minutes_to_close = float(os.getenv("TRADER_MIN_MINUTES_TO_CLOSE", "1.5"))

    # Microstructure gates + sizing
    max_spread_cents = int(os.getenv("TRADER_MAX_SPREAD_CENTS", "10"))
    depth_within_cents = int(os.getenv("TRADER_DEPTH_WITHIN_CENTS", "2"))
    min_depth_within_qty = int(os.getenv("TRADER_MIN_DEPTH_WITHIN_QTY", "50"))
    top_qty_fraction = float(os.getenv("TRADER_TOP_QTY_FRACTION", "0.30"))

    # Edge model (fair prob vs market)
    min_edge_bps = float(os.getenv("TRADER_MIN_EDGE_BPS", "12"))  # require 0.12% edge vs market-implied probability
    min_mkt_prob = float(os.getenv("TRADER_MIN_MKT_PROB", "0.12"))
    max_mkt_prob = float(os.getenv("TRADER_MAX_MKT_PROB", "0.88"))

    # Optional: separate prob bands for different market types
    min_mkt_prob_up = float(os.getenv("TRADER_MIN_MKT_PROB_UP", str(min_mkt_prob)))
    max_mkt_prob_up = float(os.getenv("TRADER_MAX_MKT_PROB_UP", str(max_mkt_prob)))
    min_mkt_prob_range = float(os.getenv("TRADER_MIN_MKT_PROB_RANGE", str(min_mkt_prob)))
    max_mkt_prob_range = float(os.getenv("TRADER_MAX_MKT_PROB_RANGE", str(max_mkt_prob)))

    lottery_max_cost_cents = int(os.getenv("TRADER_LOTTERY_MAX_COST_CENTS", "300"))
    fair_k = float(os.getenv("TRADER_FAIR_K", "0.8"))            # maps momentum bps -> prob shift
    fair_vol_window = int(os.getenv("TRADER_FAIR_VOL_WINDOW_SECONDS", "300"))
    fair_max_shift_prob = float(os.getenv("TRADER_FAIR_MAX_SHIFT_PROB", "0.03"))  # cap |p_fair-0.5|

    # Rotation entry/exit liquidity
    min_exit_bid_cents = int(os.getenv("TRADER_MIN_EXIT_BID_CENTS", "1"))

    # Per-ticker position cap (contracts)
    max_pos_per_ticker = int(os.getenv("TRADER_MAX_POSITION_PER_TICKER", "80"))

    # Rotation / timeout exits
    exit_edge_eps_bps = float(os.getenv("TRADER_EXIT_EDGE_EPS_BPS", "4"))  # exit when edge compresses within 0.04%
    max_hold_seconds = int(os.getenv("TRADER_MAX_HOLD_SECONDS", "900"))    # 15 minutes default

    # Exits / risk guards
    exits_enabled = os.getenv("TRADER_EXITS_ENABLED", "false").lower() == "true"
    take_profit_cents = int(os.getenv("TRADER_TAKE_PROFIT_UNREAL_CENTS", "0"))     # per-position unrealized pnl threshold
    stop_loss_cents = int(os.getenv("TRADER_STOP_LOSS_UNREAL_CENTS", "0"))         # per-position unrealized pnl threshold (negative)
    exit_max_slip = int(os.getenv("TRADER_EXIT_MAX_SLIPPAGE_CENTS", "0"))          # sell at (best_bid - slip) to increase fill probability

    # Defaults (picked for your $10/day cap): take profit at +$1.00, stop at -$1.50 per position.
    if exits_enabled:
        if take_profit_cents <= 0:
            take_profit_cents = 100
        if stop_loss_cents == 0:
            stop_loss_cents = 150
    daily_loss_limit_cents = int(os.getenv("TRADER_DAILY_REALIZED_LOSS_LIMIT_CENTS", "0"))  # stops trading if realized pnl <= -limit

    # Local ledger
    ledger_path = os.getenv("TRADER_LEDGER_PATH", os.path.join(REPO_DIR, "data", "trades.sqlite"))
    conn = _db(ledger_path)

    # Maintain rolling BTC spot samples
    spot = deque(maxlen=5000)

    port = int(os.getenv("PORT", "8099"))
    base = f"http://127.0.0.1:{port}"

    log_path = os.getenv("TRADER_LOG_PATH", os.path.join(REPO_DIR, "data", "autotrader.log"))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # Heartbeat logging
    heartbeat_every = int(os.getenv("TRADER_HEARTBEAT_EVERY_LOOPS", "5"))

    # How many proposals to evaluate per loop
    candidates_to_check = int(os.getenv("TRADER_CANDIDATES_TO_CHECK", "25"))
    if candidates_to_check <= 0:
        candidates_to_check = 25

    # Avoid ping-pong / overtrading the same ticker
    ticker_cooldown_seconds = int(os.getenv("TRADER_TICKER_COOLDOWN_SECONDS", "90"))
    last_trade_ts_by_ticker = {}

    # Optional stop conditions (default disabled)
    max_trades = int(os.getenv("TRADER_MAX_TRADES", "0"))
    target_trades = int(os.getenv("TRADER_TARGET_TRADES", "0"))
    target_spend_cents = int(os.getenv("TRADER_TARGET_SPEND_CENTS", "0"))

    # Paper-runner proposal batch size (separate from stop conditions).
    paper_max_trades = int(os.getenv("TRADER_PAPER_MAX_TRADES", "5"))
    if paper_max_trades <= 0:
        paper_max_trades = 5

    max_cost_trade = int(os.getenv("TRADER_MAX_COST_CENTS_PER_TRADE", "200"))
    # Daily budget cap (NET spend): computed from balance delta since local midnight.
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
    cutoff = parse_iso(cutoff_s) if cutoff_s else None  # deprecated (no hard stop)

    # Daily accounting (resets at local midnight)
    day_key = _now().date()
    day_start_avail_cents = None  # set after first successful balance fetch
    net_spent_today_cents = 0

    # Persist day-start balance so the daily cap survives script restarts.
    day_state_path = os.getenv("TRADER_DAY_STATE_PATH", os.path.join(REPO_DIR, "data", "day_state.json"))
    try:
        if os.path.exists(day_state_path):
            with open(day_state_path, "r", encoding="utf-8") as f:
                st = json.load(f) or {}
            if st.get("day") == str(day_key) and st.get("day_start_avail_cents") is not None:
                day_start_avail_cents = int(st.get("day_start_avail_cents"))
    except Exception:
        pass

    fills = 0

    _log("Kalshi Sentinel AUTOTRADER v0", log_path=log_path)
    _send_telegram("Kalshi Sentinel autotrader started")
    _log(f"Base={base}", log_path=log_path)
    _log(f"Allow prefixes={allow_prefixes}", log_path=log_path)
    _log(f"Max trades={max_trades or '∞'} Target trades={target_trades or '—'} Target spend={target_spend_cents or '—'}c ForceComplete={force_complete}", log_path=log_path)
    _log(f"Max cost/trade={max_cost_trade}c Daily net spend cap={daily_max_cost}c Interval={interval}s HoursAhead={hours_ahead}", log_path=log_path)
    _log(f"Baseline gates: max_entry={base_max_entry_price_cents}c min_top_qty={base_min_top_qty}", log_path=log_path)
    _log(f"Cutoff={(cutoff_s or '(none)')} (deprecated; no hard stop)", log_path=log_path)

    loops = 0
    stats = {
        "paper_calls": 0,
        "paper_empty": 0,
        "ob_calls": 0,
        "skips_price": 0,
        "skips_qty": 0,
        "skips_allow": 0,
        "skips_exposure": 0,
        "skips_edge": 0,
        "skips_direction": 0,
        "skips_prob_band": 0,
        "skips_lottery_cap": 0,
        "skips_no_exit_bid": 0,
        "skips_spread": 0,
        "skips_poscap": 0,
        "skips_cooldown": 0,
        "skips_depth": 0,
        "skips_semantics": 0,
        "skips_near": 0,
        "candidates_checked": 0,
        "orders_posted": 0,
        "order_errors": 0,
        "fills": 0,
    }

    while True:
        loops += 1
        now = _now()

        # Reset daily accounting at local midnight
        if now.date() != day_key:
            day_key = now.date()
            day_start_avail_cents = None
            net_spent_today_cents = 0
            _log(f"new day {day_key.isoformat()}: resetting daily accounting", log_path=log_path)
            _send_telegram(f"Kalshi Sentinel: new day {day_key.isoformat()} (daily limits reset)")
            try:
                with open(day_state_path, "w", encoding="utf-8") as f:
                    json.dump({"day": str(day_key), "day_start_avail_cents": None}, f)
            except Exception:
                pass

        # Daily realized loss guard (requires sells to matter)
        if daily_loss_limit_cents > 0:
            realized = _today_pnl(conn)
            if realized <= -abs(daily_loss_limit_cents):
                _log(f"Daily realized loss limit hit: realized={realized}c <= -{abs(daily_loss_limit_cents)}c; sleeping", log_path=log_path)
                _send_telegram(f"Kalshi Sentinel: daily realized loss limit hit ({realized}c). Pausing trading.")
                time.sleep(max(60, interval))
                continue

        # Optional stop conditions (disabled when 0)
        if target_spend_cents > 0 and net_spent_today_cents >= target_spend_cents:
            _log("Target spend reached; stopping.", log_path=log_path)
            break
        if target_trades > 0 and fills >= target_trades:
            _log("Target fills reached; stopping.", log_path=log_path)
            break
        if max_trades > 0 and fills >= max_trades:
            _log("Max trades reached; stopping.", log_path=log_path)
            break

        # Gates (optionally can be relaxed in the future; cutoff-based pacing is disabled when running continuously)
        max_entry_price_cents = base_max_entry_price_cents
        min_top_qty = base_min_top_qty
        tif = "fill_or_kill"

        # Entries disabled close to expiry (rotation mode): exits may still run.
        entries_enabled = True
        try:
            if min_minutes_to_close > 0 and mins_left is not None and mins_left < min_minutes_to_close:
                entries_enabled = False
        except Exception:
            pass

        mins_left = None
        if cutoff:
            # informational only (no hard stop)
            try:
                mins_left = max(0.0, (cutoff - now).total_seconds() / 60.0)
            except Exception:
                mins_left = None

        # 0) position-aware throttling (avoid stacking correlated BTC/ETH exposure)
        try:
            pos = requests.get(base + "/api/kalshi/portfolio/positions", timeout=30).json()
            mpos = pos.get("market_positions", []) or []
            btc_exposure = sum(int(x.get("market_exposure") or 0) for x in mpos if str(x.get("ticker","")).startswith("KXBTC") or str(x.get("ticker","")).startswith("KXBTCD"))
            eth_exposure = sum(int(x.get("market_exposure") or 0) for x in mpos if str(x.get("ticker","")).startswith("KXETH"))
            pos_by_ticker = {str(x.get("ticker")): int(float(x.get("position_fp") or x.get("position") or 0)) for x in mpos if x.get("ticker")}
        except Exception:
            btc_exposure = 0
            eth_exposure = 0
            pos_by_ticker = {}

        max_btc = int(os.getenv("TRADER_MAX_BTC_EXPOSURE_CENTS", "2000"))
        max_eth = int(os.getenv("TRADER_MAX_ETH_EXPOSURE_CENTS", "2000"))

        # available cash guard
        try:
            bal = requests.get(base + "/api/kalshi/portfolio/balance", timeout=30).json()
            b = bal.get("balance", 0)
            # Our backend currently returns cents (int) for balance/portfolio_value.
            # But keep this robust in case it changes to dollars.
            if isinstance(b, (int,)):
                avail_cents = int(b)
            else:
                bf = float(b or 0)
                # Heuristic: values < 1000 are probably dollars; otherwise cents.
                avail_cents = int(round(bf * 100)) if bf < 1000 else int(round(bf))
        except Exception:
            avail_cents = 0

        # Initialize day-start balance and compute net spend today.
        if avail_cents > 0:
            if day_start_avail_cents is None:
                day_start_avail_cents = avail_cents
                try:
                    os.makedirs(os.path.dirname(day_state_path), exist_ok=True)
                    with open(day_state_path, "w", encoding="utf-8") as f:
                        json.dump({"day": str(day_key), "day_start_avail_cents": int(day_start_avail_cents)}, f)
                except Exception:
                    pass
            net_spent_today_cents = max(0, int(day_start_avail_cents - avail_cents))

        # Daily net spend cap check (runs continuously until stopped)
        if daily_max_cost > 0 and net_spent_today_cents >= daily_max_cost:
            _log(f"Daily net spend cap reached ({net_spent_today_cents}c >= {daily_max_cost}c); sleeping", log_path=log_path)
            time.sleep(max(30, interval))
            continue

        # Spot price update (for BTC momentum signal)
        spot_px = None
        spot_ret_bps = None
        spot_vol_bps = None
        p_fair_yes = None
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

                # realized vol over fair_vol_window (std of 1-step bps returns)
                t_vol_cut = t_now - fair_vol_window
                xs = [p for (t, p) in spot if t >= t_vol_cut]
                if len(xs) >= 5:
                    rets = []
                    for i in range(1, len(xs)):
                        if xs[i-1] > 0:
                            rets.append(((xs[i] / xs[i-1]) - 1.0) * 10000.0)
                    if len(rets) >= 4:
                        mu = sum(rets) / len(rets)
                        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
                        spot_vol_bps = math.sqrt(max(0.0, var))

                # fair probability model for YES (BTC up in next 15 mins)
                # Start at 50%, shift by momentum (in bps) scaled down to probability units.
                # IMPORTANT: bps -> fraction uses /10000, not /100.
                if spot_ret_bps is not None:
                    damp = 1.0
                    if spot_vol_bps is not None:
                        damp = 1.0 / (1.0 + (spot_vol_bps / 50.0))  # higher vol => less confident

                    shift = fair_k * damp * (spot_ret_bps / 10000.0)
                    # cap the shift so we don't hallucinate huge edges on tiny moves
                    cap = abs(fair_max_shift_prob)
                    if cap > 0:
                        shift = max(-cap, min(cap, shift))

                    p_fair_yes = 0.5 + shift
                    p_fair_yes = max(0.02, min(0.98, p_fair_yes))
        except Exception as e:
            _log(f"spot feed error: {e}", log_path=log_path)

        # Optional exit logic (edge compression / rotation + TP/SL fallback)
        if exits_enabled:
            try:
                mtm = requests.get(base + "/api/status/positions_mtm", timeout=30).json()
                rows = mtm.get("rows", []) or []
                for r in rows[:50]:
                    tkr = r.get("ticker")
                    if not tkr:
                        continue
                    if allow_prefixes and not any(str(tkr).startswith(px) for px in allow_prefixes):
                        continue
                    pos_qty = int(r.get("position") or 0)
                    if pos_qty <= 0:
                        continue
                    unreal = int(r.get("unreal_pnl_cents") or 0)

                    side0 = (r.get("side") or "yes").lower()
                    if side0 not in ("yes", "no"):
                        side0 = "yes"

                    # Edge compression: compute market-implied P(YES) from exit bid on your side.
                    best_bid = int(r.get("best_exit_bid") or 0)
                    if best_bid <= 0:
                        continue
                    if side0 == "yes":
                        p_mkt_yes = best_bid / 100.0
                    else:
                        p_mkt_yes = 1.0 - (best_bid / 100.0)

                    # Compute fair prob now (if unavailable, fall back to TP/SL)
                    edge_bps_now = None
                    if p_fair_yes is not None:
                        edge_bps_now = (p_fair_yes - p_mkt_yes) * 10000.0

                    # Rotation / timeout
                    too_old = False
                    try:
                        ts = _last_entry_ts(conn, ticker=tkr, side=side0)
                        if ts:
                            age = (now - dt.datetime.fromisoformat(ts)).total_seconds()
                            too_old = (max_hold_seconds > 0 and age >= max_hold_seconds)
                    except Exception:
                        too_old = False

                    hit_edge_compress = (edge_bps_now is not None and abs(edge_bps_now) <= exit_edge_eps_bps)
                    hit_tp = (take_profit_cents > 0 and unreal >= take_profit_cents)
                    hit_sl = (stop_loss_cents != 0 and unreal <= -abs(stop_loss_cents))

                    if not (hit_edge_compress or too_old or hit_tp or hit_sl):
                        continue

                    # Sell at best bid (or a bit below if exit_max_slip is set)
                    sell_qty = pos_qty

                    side = side0

                    sell_px = max(1, best_bid - max(0, exit_max_slip))

                    payload = {
                        "ticker": tkr,
                        "side": side,
                        "action": "sell",
                        "type": "limit",
                        "count": sell_qty,
                        "time_in_force": "immediate_or_cancel",
                    }
                    if side == "yes":
                        payload["yes_price"] = sell_px
                    else:
                        payload["no_price"] = sell_px

                    _log(
                        f"EXIT signal: {tkr} SELL {side.upper()} qty={sell_qty} @ {sell_px}c (best={best_bid} slip={exit_max_slip}) "
                        f"unreal={unreal}c edge_bps={edge_bps_now if edge_bps_now is not None else '—'} too_old={too_old}",
                        log_path=log_path,
                    )
                    resp = requests.post(base + "/api/kalshi/orders", json=payload, timeout=60)
                    try:
                        data = resp.json()
                    except Exception:
                        _log(f"exit order non-json: {resp.status_code} {(resp.text or '')[:300]}", log_path=log_path)
                        continue

                    # record if filled
                    filled_qty = 0
                    filled_cost = 0
                    order_id = None
                    if isinstance(data, dict) and isinstance(data.get("order"), dict):
                        o = data["order"]
                        order_id = o.get("order_id")
                        try:
                            filled_qty = int(o.get("fill_count", 0) or 0)
                            # for sells, treat proceeds as cost_cents for ledger purposes
                            taker = int(o.get("taker_fill_cost", 0) or 0)
                            maker = int(o.get("maker_fill_cost", 0) or 0)
                            fees = int(o.get("taker_fees", 0) or 0) + int(o.get("maker_fees", 0) or 0)
                            filled_cost = taker + maker - fees
                        except Exception:
                            filled_qty = 0
                            filled_cost = 0

                    if filled_qty > 0:
                        _record_trade(conn, ticker=tkr, side=side, action="sell", price_cents=sell_px, qty=filled_qty, cost_cents=filled_cost, order_id=order_id, raw=data)
                        _send_telegram(f"Kalshi Sentinel EXIT: SOLD {tkr} {side.upper()} qty={filled_qty} @ {sell_px}c")
            except Exception as e:
                _log(f"exit loop error: {e}", log_path=log_path)

        # 1) get paper proposals (crypto discovery + scoring)
        try:
            stats["paper_calls"] += 1
            r = requests.post(
                base + "/api/paper/run_today",
                json={
                    "hours_ahead": hours_ahead,
                    "budget_dollars": 10,
                    "max_trades": paper_max_trades,
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
                    f"heartbeat loops={loops} fills={fills} net_spent_today={net_spent_today_cents}c budget_left={(daily_max_cost-net_spent_today_cents) if daily_max_cost>0 else '—'}c "
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
        chosen_lottery = False

        # Collect a few "closest rejects" for clean, math-based logging
        rejects: list[dict] = []
        reject_topn = int(os.getenv("TRADER_REJECT_LOG_TOPN", "3"))

        # Try multiple proposals until one passes gates
        for p in props[:candidates_to_check]:
            stats["candidates_checked"] += 1
            ticker = p.get("ticker")
            title = (p.get("title") or "")
            close_time = p.get("close_time")
            if not ticker:
                continue
            if allow_prefixes and not any(str(ticker).startswith(px) for px in allow_prefixes):
                stats["skips_allow"] += 1
                continue

            # cooldown gate
            if ticker_cooldown_seconds > 0:
                last_ts = last_trade_ts_by_ticker.get(str(ticker))
                if last_ts is not None and (time.time() - last_ts) < ticker_cooldown_seconds:
                    stats["skips_cooldown"] += 1
                    continue

            # per-ticker position cap gate
            if max_pos_per_ticker > 0:
                cur_pos = abs(int(pos_by_ticker.get(str(ticker), 0) or 0))
                if cur_pos >= max_pos_per_ticker:
                    stats["skips_poscap"] += 1
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

            spread_yes = implied_yes_ask - best_yes_bid
            spread_no = implied_no_ask - best_no_bid

            # Decide direction using BTC momentum for "up in next 15 mins" markets.
            # - If momentum is positive enough -> buy YES
            # - If momentum is negative enough -> buy NO (i.e., bet NOT up)
            # Decide side using fair probability vs market price.
            want_side = None
            want_lottery = False

            # Market semantics detection
            tkr_u = str(ticker).upper()
            title_u = title.upper()
            is_btc_up_15m = tkr_u.startswith("KXBTC15M") and ("BTC" in title_u and "PRICE" in title_u and "UP" in title_u)
            is_kxbtc_range = tkr_u.startswith("KXBTC-") and ("PRICE RANGE" in title_u)

            if spot_px is None:
                stats["skips_direction"] += 1
                _add_reject(rejects, "no_spot", penalty=1.0, ticker=ticker)
                continue

            # Compute p_fair_yes depending on market type
            if is_btc_up_15m:
                if p_fair_yes is None:
                    stats["skips_direction"] += 1
                    _add_reject(rejects, "no_fair_prob", penalty=1.0, ticker=ticker)
                    continue
            elif is_kxbtc_range:
                sub = p.get("subtitle") or ""
                lo, hi = _parse_range_subtitle(sub)
                if lo is None and hi is None:
                    stats["skips_semantics"] += 1
                    _add_reject(rejects, "bad_subtitle", penalty=1.0, ticker=ticker, subtitle=sub)
                    continue

                # Near-the-money bucket filter: only trade buckets close to spot.
                # For bounded ranges, use midpoint. For one-sided buckets, use the boundary as proxy.
                try:
                    s0 = float(spot_px)
                    if lo is not None and hi is not None:
                        anchor = 0.5 * (float(lo) + float(hi))
                    else:
                        anchor = float(lo) if lo is not None else float(hi)
                    rel = abs(anchor - s0) / max(1.0, s0)
                    if range_near_pct > 0 and rel > range_near_pct:
                        stats["skips_near"] += 1
                        _add_reject(rejects, "not_near_money", penalty=(rel - range_near_pct), ticker=ticker, rel=round(rel, 6), near_pct=range_near_pct)
                        continue
                except Exception:
                    pass

                # Horizon to close in seconds
                try:
                    if close_time:
                        ct = dt.datetime.fromisoformat(str(close_time).replace('Z', '+00:00')).astimezone(dt.timezone.utc)
                        horizon_s = max(1.0, (ct - now.astimezone(dt.timezone.utc)).total_seconds())
                    else:
                        horizon_s = 3600.0
                except Exception:
                    horizon_s = 3600.0

                # Drift from recent return
                mu = 0.0
                if spot_ret_bps is not None and momentum_lookback > 0:
                    mu = (spot_ret_bps / 10000.0) * (horizon_s / float(momentum_lookback))

                # Vol scaling (treat spot_vol_bps roughly as per-minute). If not available yet,
                # fall back to a conservative default to avoid skipping all early trades.
                default_vol_bps = float(os.getenv("TRADER_DEFAULT_VOL_BPS", "60"))
                vol_bps = float(spot_vol_bps) if spot_vol_bps is not None else default_vol_bps
                sigma = abs(vol_bps / 10000.0) * math.sqrt(horizon_s / 60.0)
                sigma = max(1e-6, sigma)

                mlog = math.log(float(spot_px)) + mu

                def cdf_price(x: float) -> float:
                    return _norm_cdf((math.log(x) - mlog) / sigma)

                if lo is None:
                    p_fair_yes = cdf_price(float(hi))
                elif hi is None:
                    p_fair_yes = 1.0 - cdf_price(float(lo))
                else:
                    p_fair_yes = max(0.0, min(1.0, cdf_price(float(hi)) - cdf_price(float(lo))))

                p_fair_yes = max(0.001, min(0.999, float(p_fair_yes)))
            else:
                stats["skips_semantics"] += 1
                _add_reject(rejects, "unsupported_market", penalty=1.0, ticker=ticker, title=title)
                continue

            # Market-implied P(YES) for buying each side at the implied ask:
            p_mkt_yes_if_buy_yes = implied_yes_ask / 100.0
            p_mkt_yes_if_buy_no = 1.0 - (implied_no_ask / 100.0)

            # Market sanity band: if extreme, allow only as small lottery trade.
            # Use split bands by market type.
            if is_btc_up_15m:
                band_lo, band_hi = min_mkt_prob_up, max_mkt_prob_up
                band_tag = "up"
            else:
                band_lo, band_hi = min_mkt_prob_range, max_mkt_prob_range
                band_tag = "range"

            lot_yes = (p_mkt_yes_if_buy_yes < band_lo) or (p_mkt_yes_if_buy_yes > band_hi)
            lot_no = (p_mkt_yes_if_buy_no < band_lo) or (p_mkt_yes_if_buy_no > band_hi)
            if lot_yes or lot_no:
                stats["skips_prob_band"] += 1
                # HARD GATE: if lottery mode is disabled (cap <= 0), do not trade outside the band.
                if lottery_max_cost_cents <= 0:
                    # distance to band (0 means inside)
                    p = p_mkt_yes_if_buy_yes if lot_yes else p_mkt_yes_if_buy_no
                    dist = (band_lo - p) if p < band_lo else (p - band_hi)
                    _add_reject(rejects, "prob_band", penalty=float(dist), ticker=ticker, p=float(p), band=[band_lo, band_hi], band_type=band_tag)
                    continue

            edge_bps_yes = (p_fair_yes - p_mkt_yes_if_buy_yes) * 10000.0
            edge_bps_no = (p_mkt_yes_if_buy_no - p_fair_yes) * 10000.0

            # For 15m "UP" markets, require direction agreement with momentum.
            if is_btc_up_15m:
                if spot_ret_bps is None or abs(spot_ret_bps) < momentum_threshold_bps:
                    stats["skips_direction"] += 1
                    if spot_ret_bps is None:
                        _add_reject(rejects, "no_momentum", penalty=momentum_threshold_bps, ticker=ticker, ret_bps=None, thr=momentum_threshold_bps)
                    else:
                        _add_reject(rejects, "momentum_too_small", penalty=(momentum_threshold_bps - abs(spot_ret_bps)), ticker=ticker, ret_bps=spot_ret_bps, thr=momentum_threshold_bps)
                    continue
                if spot_ret_bps > 0:
                    if edge_bps_yes >= min_edge_bps:
                        want_side = "yes"
                        want_lottery = bool(lot_yes)
                    else:
                        stats["skips_edge"] += 1
                        _add_reject(rejects, "edge", penalty=(min_edge_bps - edge_bps_yes), ticker=ticker, edge_bps=edge_bps_yes, min_edge_bps=min_edge_bps, side="YES")
                        continue
                else:
                    if edge_bps_no >= min_edge_bps:
                        want_side = "no"
                        want_lottery = bool(lot_no)
                    else:
                        stats["skips_edge"] += 1
                        _add_reject(rejects, "edge", penalty=(min_edge_bps - edge_bps_no), ticker=ticker, edge_bps=edge_bps_no, min_edge_bps=min_edge_bps, side="NO")
                        continue
            else:
                # Range markets: pick whichever side has enough edge (no momentum sign requirement)
                if edge_bps_yes >= min_edge_bps:
                    want_side = "yes"
                    want_lottery = bool(lot_yes)
                elif edge_bps_no >= min_edge_bps:
                    want_side = "no"
                    want_lottery = bool(lot_no)
                else:
                    stats["skips_edge"] += 1
                    best = max(edge_bps_yes, edge_bps_no)
                    _add_reject(rejects, "edge", penalty=(min_edge_bps - best), ticker=ticker, edge_yes=edge_bps_yes, edge_no=edge_bps_no, min_edge_bps=min_edge_bps)
                    continue

            side = want_side
            price = implied_yes_ask if side == "yes" else implied_no_ask

            # Rotation requirement: ensure there is at least some exit bid liquidity on our side.
            exit_bid = best_yes_bid if side == "yes" else best_no_bid
            if min_exit_bid_cents > 0 and exit_bid < min_exit_bid_cents:
                stats["skips_no_exit_bid"] += 1
                _add_reject(rejects, "exit_bid", penalty=(min_exit_bid_cents - exit_bid), ticker=ticker, exit_bid=exit_bid, min_exit_bid=min_exit_bid_cents)
                continue

            # Spread gate (avoid toxic / too wide markets)
            if max_spread_cents > 0:
                spr = spread_yes if side == "yes" else spread_no
                if spr > max_spread_cents:
                    stats["skips_spread"] += 1
                    _add_reject(rejects, "spread", penalty=(spr - max_spread_cents), ticker=ticker, spread=spr, max_spread=max_spread_cents)
                    continue

            if price > max_entry_price_cents:
                stats["skips_price"] += 1
                _log(f"skip {ticker}: entry too expensive {price}c > {max_entry_price_cents}c", log_path=log_path)
                continue

            # Time-to-close gate (avoid trading the last ~minutes)
            try:
                if close_time:
                    ct = dt.datetime.fromisoformat(str(close_time).replace('Z', '+00:00')).astimezone()
                    mins_to_close = (ct - now).total_seconds() / 60.0
                    if mins_to_close < min_minutes_to_close:
                        continue
            except Exception:
                pass

            if not entries_enabled:
                continue

            top_qty = best_no_qty if side == "yes" else best_yes_qty
            if top_qty < min_top_qty:
                stats["skips_qty"] += 1
                _log(f"skip {ticker}: top-of-book qty too low {top_qty} < {min_top_qty}", log_path=log_path)
                continue

            # Depth-within-N-cents (on the *resting* side we are crossing)
            # - Buying YES crosses NO bids (since YES ask = 100 - NO bid)
            # - Buying NO crosses YES bids
            depth_qty = 0
            try:
                if depth_within_cents > 0:
                    if side == "yes":
                        # NO bids near best_no_bid
                        cutoff_px = best_no_bid - depth_within_cents
                        depth_qty = sum(int(q) for (px, q) in no if int(px) >= cutoff_px)
                    else:
                        cutoff_px = best_yes_bid - depth_within_cents
                        depth_qty = sum(int(q) for (px, q) in yes if int(px) >= cutoff_px)
            except Exception:
                depth_qty = 0

            if min_depth_within_qty > 0 and depth_qty < min_depth_within_qty:
                stats["skips_depth"] += 1
                continue

            chosen = ticker
            chosen_side = side
            chosen_price = max(1, min(99, int(price)))
            chosen_title = title
            chosen_close_time = close_time
            chosen_lottery = bool(locals().get('want_lottery', False))
            break

        if not chosen:
            if loops % heartbeat_every == 0:
                _log(
                    f"heartbeat loops={loops} fills={fills} net_spent_today={net_spent_today_cents}c mins_left={mins_left if mins_left is not None else '—'} "
                    f"paper_props={len(props)} checked={stats['candidates_checked']} ob_calls={stats['ob_calls']} "
                    f"skips_edge={stats['skips_edge']} skips_dir={stats['skips_direction']} skips_prob={stats['skips_prob_band']} skips_sem={stats['skips_semantics']} skips_near={stats['skips_near']} "
                    f"skips_exitbid={stats['skips_no_exit_bid']} skips_spread={stats['skips_spread']} skips_depth={stats['skips_depth']} skips_qty={stats['skips_qty']} skips_price={stats['skips_price']} skips_poscap={stats['skips_poscap']} skips_cooldown={stats['skips_cooldown']}",
                    log_path=log_path,
                )

                if reject_topn > 0 and rejects:
                    # log the closest-to-passing rejects (smallest penalty first)
                    def keyfn(r):
                        try:
                            return float(r.get("penalty", 9999.0))
                        except Exception:
                            return 9999.0

                    top_rej = sorted(rejects, key=keyfn)[:reject_topn]
                    for rj in top_rej:
                        _log(f"reject: {json.dumps(rj, ensure_ascii=False)}", log_path=log_path)

            _log("no candidates passed gates; sleeping", log_path=log_path)
            time.sleep(interval)
            continue

        ticker = chosen
        side = chosen_side
        price = chosen_price
        is_lottery = chosen_lottery

        # Market-implied P(YES) for the chosen order
        p_mkt_yes = (price / 100.0) if side == "yes" else (1.0 - (price / 100.0))
        edge_bps = None
        if p_fair_yes is not None:
            edge_bps = (p_fair_yes - p_mkt_yes) * 10000.0

        _log(
            f"select ticker={ticker} side={side.upper()} px={price}c p_mkt_yes={p_mkt_yes:.3f} p_fair_yes={p_fair_yes if p_fair_yes is not None else '—'} edge_bps={edge_bps if edge_bps is not None else '—'} lottery={is_lottery} "
            f"title={chosen_title!r} close={chosen_close_time} spot={spot_px if spot_px is not None else '—'} ret_bps={spot_ret_bps if spot_ret_bps is not None else '—'} vol_bps={spot_vol_bps if spot_vol_bps is not None else '—'}",
            log_path=log_path,
        )

        # Set count consistent with buy_max_cost so exchange doesn't reject on worst-case notional.
        # For a BUY at price cents, max fillable contracts <= floor(max_cost_cents / price).
        # We'll compute count after we compute buy_max_cost.
        count = None

        # 3) place FoK order (limit)
        buy_max_cost = min(max_cost_trade, max(0, daily_max_cost - net_spent_today_cents))
        if is_lottery and lottery_max_cost_cents > 0:
            buy_max_cost = min(buy_max_cost, lottery_max_cost_cents)
        if target_spend_cents > 0:
            buy_max_cost = min(buy_max_cost, max(0, target_spend_cents - net_spent_today_cents))
        if avail_cents > 0:
            buy_max_cost = min(buy_max_cost, max(0, avail_cents - 25))  # keep small buffer
        if buy_max_cost <= 0:
            print("No remaining budget/cash; stopping")
            break
        # Determine a sensible contract count that cannot exceed the max cost.
        count = max(1, buy_max_cost // max(1, price))

        # Depth-based sizing: don't try to take more than a fraction of top-of-book.
        # (We re-fetch top_qty on selection; if unavailable just keep count.)
        try:
            # We can approximate available top qty from the last proposal loop variables by re-reading the orderbook.
            ob2 = requests.get(base + f"/api/kalshi/markets/{ticker}/orderbook", params={"depth": 5}, timeout=30).json()
            book2 = (ob2.get("orderbook") or {})
            yes2 = book2.get("yes") or []
            no2 = book2.get("no") or []
            if yes2 and no2:
                best_yes_qty2 = int(yes2[0][1])
                best_no_qty2 = int(no2[0][1])
                top_qty2 = best_no_qty2 if side == "yes" else best_yes_qty2
                if top_qty_fraction > 0:
                    cap = max(1, int(top_qty2 * top_qty_fraction))
                    count = max(1, min(count, cap))
        except Exception:
            pass

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
            last_trade_ts_by_ticker[str(ticker)] = time.time()
            trade_cost = filled_cost if filled_cost is not None else payload["buy_max_cost"]
            fills += 1
            stats["fills"] = fills
            _log(
                f"FILL #{fills}: {ticker} BUY {side.upper()} @ {price}c qty={filled_qty} cost={trade_cost}c "
                f"net_spent_today={net_spent_today_cents}c budget_left={(daily_max_cost-net_spent_today_cents) if daily_max_cost>0 else '—'}c "
                f"tif={payload.get('time_in_force')}",
                log_path=log_path,
            )
            if isinstance(data, dict):
                data = dict(data)
                data.setdefault("_meta", {})
                data["_meta"].update({
                    "p_fair_yes": p_fair_yes,
                    "p_mkt_yes": p_mkt_yes,
                    "edge_bps": edge_bps,
                    "spot_ret_bps": spot_ret_bps,
                    "spot_vol_bps": spot_vol_bps,
                })
            _record_trade(conn, ticker=ticker, side=side, action="buy", price_cents=price, qty=filled_qty, cost_cents=trade_cost, order_id=(data.get('order') or {}).get('order_id') if isinstance(data, dict) else None, raw=data)
            _send_telegram(f"Kalshi Sentinel FILL: {ticker} BUY {side.upper()} qty={filled_qty} @ {price}c cost={trade_cost}c")

        # brief structured response for debugging
        try:
            _log("response: " + json.dumps(data)[:400], log_path=log_path)
        except Exception:
            _log("response: " + str(data)[:400], log_path=log_path)

        time.sleep(interval)


if __name__ == "__main__":
    main()
