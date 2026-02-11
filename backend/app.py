#!/usr/bin/env python3
"""Kalshi Sentinel API server."""

import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

from kalshi_client import KalshiClient
from db import init_db, db_health, get_db
from audit import log as audit_log
from strategy import choose_universe
from status import positions_mtm
from report import ledger_summary
from performance import kalshi_performance
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
load_dotenv(os.path.join(REPO_DIR, "config", ".env"))

PORT = int(os.getenv("PORT", "8099"))

app = Flask(__name__)
CORS(app)


@app.route("/api/health")
def health():
    ok, detail = db_health()
    return jsonify({"status": "ok" if ok else "error", "db": detail})


@app.route("/api/kalshi/exchange/status")
def exchange_status():
    kc = KalshiClient.from_env()
    data = kc.get("/exchange/status")
    return jsonify(data)


@app.route("/api/kalshi/events")
def list_events():
    kc = KalshiClient.from_env()
    params = dict(request.args)
    data = kc.get("/events", params=params)
    return jsonify(data)


@app.route("/api/kalshi/markets/<ticker>/orderbook")
def get_orderbook(ticker: str):
    kc = KalshiClient.from_env()
    depth = request.args.get("depth")
    params = {}
    if depth is not None:
        params["depth"] = depth
    try:
        data = kc.get(f"/markets/{ticker}/orderbook", params=params)
        return jsonify(data)
    except Exception as e:
        audit_log("ERROR", "kalshi", "orderbook_failed", {"ticker": ticker, "error": str(e)})
        msg = str(e)
        code = 500
        if "401" in msg:
            code = 401
        return jsonify({"error": "orderbook_failed", "details": msg[:300]}), code


@app.route("/api/kalshi/markets")
def list_markets():
    kc = KalshiClient.from_env()
    params = dict(request.args)
    data = kc.get("/markets", params=params)
    return jsonify(data)


@app.route("/api/kalshi/portfolio/balance")
def get_balance():
    kc = KalshiClient.from_env()
    try:
        data = kc.get("/portfolio/balance")
        return jsonify(data)
    except Exception as e:
        # Don't crash the whole server on auth issues; surface them.
        audit_log("ERROR", "kalshi", "balance_failed", {"error": str(e)})
        msg = str(e)
        code = 500
        if "401" in msg:
            code = 401
        return jsonify({"error": "balance_failed", "details": msg[:300]}), code


@app.route("/api/kalshi/portfolio/positions")
def get_positions():
    kc = KalshiClient.from_env()
    params = dict(request.args)
    try:
        data = kc.get("/portfolio/positions", params=params)
        return jsonify(data)
    except Exception as e:
        audit_log("ERROR", "kalshi", "positions_failed", {"error": str(e)})
        msg = str(e)
        code = 500
        if "401" in msg:
            code = 401
        return jsonify({"error": "positions_failed", "details": msg[:300]}), code


@app.route("/api/kalshi/portfolio/orders")
def get_orders():
    kc = KalshiClient.from_env()
    params = dict(request.args)
    try:
        data = kc.get("/portfolio/orders", params=params)
        return jsonify(data)
    except Exception as e:
        audit_log("ERROR", "kalshi", "orders_failed", {"error": str(e)})
        msg = str(e)
        code = 500
        if "401" in msg:
            code = 401
        return jsonify({"error": "orders_failed", "details": msg[:500]}), code


@app.route("/api/kalshi/portfolio/fills")
def get_fills():
    kc = KalshiClient.from_env()
    params = dict(request.args)
    try:
        data = kc.get("/portfolio/fills", params=params)
        return jsonify(data)
    except Exception as e:
        audit_log("ERROR", "kalshi", "fills_failed", {"error": str(e)})
        msg = str(e)
        code = 500
        if "401" in msg:
            code = 401
        return jsonify({"error": "fills_failed", "details": msg[:500]}), code


@app.route("/api/status/positions_mtm")
def status_positions_mtm():
    kc = KalshiClient.from_env()
    try:
        data = positions_mtm(kc)
        return jsonify(data)
    except Exception as e:
        audit_log("ERROR", "status", "positions_mtm_failed", {"error": str(e)})
        return jsonify({"error": "positions_mtm_failed", "details": str(e)[:500]}), 500


@app.route("/api/report/ledger")
def report_ledger():
    try:
        days = int(request.args.get("days", "7"))
        limit = int(request.args.get("limit", "200"))
        return jsonify(ledger_summary(days=days, limit=limit))
    except Exception as e:
        audit_log("ERROR", "report", "ledger_failed", {"error": str(e)})
        return jsonify({"error": "ledger_failed", "details": str(e)[:500]}), 500


@app.route("/api/report/kalshi_performance")
def report_kalshi_performance():
    kc = KalshiClient.from_env()
    try:
        hours = int(request.args.get("hours", "24"))
        limit = int(request.args.get("limit", "200"))
        return jsonify(kalshi_performance(kc, hours=hours, limit=limit))
    except Exception as e:
        audit_log("ERROR", "report", "kalshi_performance_failed", {"error": str(e)})
        return jsonify({"error": "kalshi_performance_failed", "details": str(e)[:500]}), 500


@app.route("/api/kalshi/orders", methods=["POST"])
def create_order():
    """Create an order. Guarded by AUTO_TRADING_ENABLED=true in config/.env

    Safety guardrails:
    - refuses type=market (market orders can produce awful fills)
    - optional ticker allowlist via ORDER_TICKER_ALLOW_PREFIXES
    """
    if os.getenv("AUTO_TRADING_ENABLED", "false").lower() != "true":
        return jsonify({"error": "AUTO_TRADING_ENABLED is false"}), 403

    kc = KalshiClient.from_env()
    payload = request.get_json(force=True, silent=True) or {}

    # Guard: no market orders
    if (payload.get("type") or "").lower() == "market":
        return jsonify({"error": "order_rejected", "details": "type=market is disabled; use limit + IOC/FOK"}), 400

    # Guard: ticker allowlist
    allow = os.getenv("ORDER_TICKER_ALLOW_PREFIXES", "").strip()
    if allow:
        ticker = (payload.get("ticker") or "").strip()
        prefixes = [p.strip() for p in allow.split(",") if p.strip()]
        if ticker and prefixes and not any(ticker.startswith(p) for p in prefixes):
            return jsonify({"error": "order_rejected", "details": f"ticker {ticker} not allowed"}), 400

    audit_log("INFO", "orders", "create_order_called", {"payload_keys": list(payload.keys())})

    try:
        data = kc.post("/portfolio/orders", json=payload)
        return jsonify(data)
    except Exception as e:
        audit_log("ERROR", "orders", "create_order_failed", {"error": str(e)})
        msg = str(e)
        code = 500
        if "401" in msg:
            code = 401
        elif "403" in msg:
            code = 403
        elif "409" in msg:
            code = 409
        elif "429" in msg:
            code = 429
        return jsonify({"error": "create_order_failed", "details": msg[:500]}), code


def _discover_crypto_series(kc: KalshiClient, *, limit: int = 200):
    """Discover crypto-related series tickers using /series endpoint.

    Prefer category=Crypto; fallback to broad search.
    """
    series = []
    cursor = None
    for _ in range(12):
        params = {"limit": limit, "include_volume": "true", "category": "Crypto"}
        if cursor:
            params["cursor"] = cursor
        data = kc.get("/series", params=params)
        series.extend(data.get("series", []) or [])
        cursor = data.get("cursor")
        if not cursor:
            break

    # Focus on BTC/ETH first, but allow SOL as fallback so we have something tradable.
    focus_primary = ("BTC", "BITCOIN", "ETH", "ETHEREUM")
    focus_fallback = focus_primary + ("SOL", "SOLANA")

    prim = []
    fb = []
    for s in series:
        text = f"{s.get('title','')} {s.get('ticker','')} {s.get('category','')} {s.get('tags','')}".upper()
        if any(k in text for k in focus_primary):
            prim.append(s)
        elif any(k in text for k in focus_fallback):
            fb.append(s)

    return prim if prim else fb


def _fetch_markets_for_series(kc: KalshiClient, series_ticker: str):
    markets = []
    cursor = None
    for _ in range(6):
        params = {"limit": 200, "series_ticker": series_ticker, "status": "open"}
        if cursor:
            params["cursor"] = cursor
        data = kc.get("/markets", params=params)
        markets.extend(data.get("markets", []) or [])
        cursor = data.get("cursor")
        if not cursor:
            break
    return markets


@app.route("/api/paper/run_today", methods=["POST"])
def paper_run_today():
    """Paper-run market selection and store proposed trades.

    Body:
      {"hours_ahead": 24, "budget_dollars": 10, "max_trades": 3}

    For now this only proposes trades (no placement).
    """
    body = request.get_json(force=True, silent=True) or {}
    hours_ahead = int(body.get("hours_ahead", 24))
    budget = float(body.get("budget_dollars", 10))
    max_trades = int(body.get("max_trades", 3))
    ticker_prefixes = body.get("ticker_prefixes") or []
    if isinstance(ticker_prefixes, str):
        ticker_prefixes = [ticker_prefixes]
    ticker_prefixes = [p for p in ticker_prefixes if isinstance(p, str) and p.strip()]

    kc = KalshiClient.from_env()

    # Discover crypto series tickers and pull open markets for them.
    series = _discover_crypto_series(kc)
    markets = []

    # If caller provides a concrete series prefix like KXBTC15M, fetch that series directly.
    direct_series = [p for p in ticker_prefixes if p and p.isupper() and p.startswith("KX") and ("-" not in p)]
    if direct_series:
        for st in direct_series:
            try:
                markets.extend(_fetch_markets_for_series(kc, st))
            except Exception as e:
                audit_log("WARN", "discover", "series_fetch_failed", {"series_ticker": st, "error": str(e)})

    # If markets still empty, scan discovered crypto series.
    if not markets:
        # If caller is forcing a narrow prefix like KXBTC15M, we must scan more series; it may not appear in the first 15.
        series_cap = 15
        if ticker_prefixes and any(p.startswith("KXBTC15M") for p in ticker_prefixes):
            series_cap = 80

        for s in series[:series_cap]:
            st = s.get("ticker")
            if not st:
                continue
            try:
                markets.extend(_fetch_markets_for_series(kc, st))
            except Exception as e:
                audit_log("WARN", "discover", "series_fetch_failed", {"series_ticker": st, "error": str(e)})

    # Optional ticker prefix filter (useful for forcing BTC15M-only, etc.)
    if ticker_prefixes:
        markets = [m for m in markets if any(str(m.get('ticker','')).startswith(p) for p in ticker_prefixes)]

    # Fallback: if series discovery didn't yield anything (common with narrow prefixes), scan open markets pages.
    if ticker_prefixes and not markets:
        cursor = None
        for _ in range(8):
            params = {"limit": 200, "status": "open"}
            if cursor:
                params["cursor"] = cursor
            try:
                data = kc.get("/markets", params=params)
            except Exception as e:
                audit_log("WARN", "discover", "markets_scan_failed", {"error": str(e)})
                break
            page = data.get("markets", []) or []
            page = [m for m in page if any(str(m.get('ticker','')).startswith(p) for p in ticker_prefixes)]
            markets.extend(page)
            cursor = data.get("cursor")
            if not cursor or len(markets) >= 300:
                break

    universe = choose_universe(markets, hours_ahead=hours_ahead)
    universe = [u for u in universe if 'crypto' in (u.tags or [])]

    top = universe[: max(50, max_trades * 10)]

    # naive proposal: pick top N and propose 1 trade each sized equally.
    # We'll replace this with model-based directional trades + orderbook gating.
    per_trade = budget / max(1, max_trades)

    db = get_db()
    proposed = []
    try:
        for s in top[:max_trades]:
            # placeholder direction: no real signal yet
            side = "YES"
            action = "BUY"
            # placeholder limit: use last_price_dollars if available else mid-ish 50
            m = next((x for x in markets if x.get("ticker") == s.ticker), None) or {}
            last = m.get("last_price")
            limit_cents = int(last) if isinstance(last, int) and 1 <= last <= 99 else 50
            contracts = max(1, int((per_trade * 100) / max(limit_cents, 1)))
            max_loss_cents = contracts * limit_cents

            rationale = f"paper_proposal score={s.score:.2f} tags={','.join(s.tags)} reasons={','.join(s.reasons)}"
            db.execute(
                """
                INSERT INTO paper_trades(ticker, side, action, limit_price_cents, contracts, estimated_max_loss_cents, status, rationale, market_json)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    s.ticker,
                    side,
                    action,
                    limit_cents,
                    contracts,
                    max_loss_cents,
                    "PROPOSED",
                    rationale,
                    json.dumps(m),
                ),
            )
            proposed.append(
                {
                    "ticker": s.ticker,
                    "title": s.title,
                    "subtitle": m.get("subtitle"),
                    "tags": s.tags,
                    "close_time": s.close_time,
                    "score": s.score,
                    "limit_price_cents": limit_cents,
                    "contracts": contracts,
                    "estimated_max_loss_cents": max_loss_cents,
                    "rationale": rationale,
                }
            )

        db.commit()
    finally:
        db.close()

    audit_log("INFO", "paper", "paper_run_today", {"hours_ahead": hours_ahead, "budget": budget, "max_trades": max_trades, "proposed": len(proposed)})

    # Return small universe preview for debugging (safe: public market metadata)
    universe_preview = [
        {
            "ticker": u.ticker,
            "title": u.title,
            "close_time": u.close_time,
            "score": u.score,
            "tags": u.tags,
            "reasons": u.reasons,
            "liquidity": u.liquidity,
            "volume_24h": u.volume_24h,
        }
        for u in universe[: min(20, len(universe))]
    ]

    return jsonify({
        "proposed": proposed,
        "universe_count": len(universe),
        "markets_count": len(markets),
        "universe_preview": universe_preview,
    })


@app.route("/api/paper/trades")
def paper_trades():
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, ts, ticker, side, action, limit_price_cents, contracts, estimated_max_loss_cents, status, rationale FROM paper_trades ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


@app.route("/api/audit")
def audit():
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, ts, level, component, message, data_json FROM audit_log ORDER BY id DESC LIMIT 300"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("data_json"):
                try:
                    d["data"] = json.loads(d["data_json"])
                except Exception:
                    d["data"] = None
            out.append(d)
        return jsonify(out)
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print(f"Kalshi Sentinel backend: http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
