#!/usr/bin/env python3
"""Run paper-mode proposals for BTC/ETH markets on a schedule until a cutoff.

This does NOT place orders.

Usage:
  python scripts/run_crypto_paper_loop.py

Config via config/.env:
- PORT (default 8099)
- PAPER_HOURS_AHEAD (default 24)
- PAPER_BUDGET_DOLLARS (default 10)
- PAPER_MAX_TRADES (default 5)
- PAPER_INTERVAL_SECONDS (default 120)
- PAPER_CUTOFF_LOCAL (e.g. 2026-02-10T17:00:00-05:00)

Notifications:
- Desktop + Telegram (if configured)
"""

import os
import time
import datetime as dt

import requests
from dotenv import load_dotenv

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(REPO_DIR, "config", ".env"))

import sys
sys.path.insert(0, os.path.join(REPO_DIR, "backend"))
from notifier import notify_desktop, notify_telegram


def parse_iso(s: str):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def main():
    port = int(os.getenv("PORT", "8099"))
    base = f"http://127.0.0.1:{port}"

    hours_ahead = int(os.getenv("PAPER_HOURS_AHEAD", "24"))
    budget = float(os.getenv("PAPER_BUDGET_DOLLARS", "10"))
    max_trades = int(os.getenv("PAPER_MAX_TRADES", "5"))
    interval = int(os.getenv("PAPER_INTERVAL_SECONDS", "120"))

    cutoff_s = os.getenv("PAPER_CUTOFF_LOCAL", "")
    cutoff = parse_iso(cutoff_s) if cutoff_s else None

    notify_desktop("Kalshi Sentinel", "Paper loop started")
    notify_telegram("Kalshi Sentinel: paper loop started")

    while True:
        now = dt.datetime.now().astimezone()
        if cutoff and now >= cutoff:
            notify_desktop("Kalshi Sentinel", "Paper loop stopped (cutoff reached)")
            notify_telegram("Kalshi Sentinel: paper loop stopped (cutoff reached)")
            break

        try:
            r = requests.post(
                base + "/api/paper/run_today",
                json={"hours_ahead": hours_ahead, "budget_dollars": budget, "max_trades": max_trades},
                timeout=30,
            )
            j = r.json()
            proposed = j.get("proposed", [])

            msg = f"Kalshi Sentinel PAPER: proposed={len(proposed)} universe={j.get('universe_count')}"  # short
            notify_desktop("Kalshi Sentinel", msg)

            if proposed:
                # send details to telegram if configured
                lines = ["Kalshi Sentinel PAPER proposals:"]
                for p in proposed:
                    lines.append(f"- {p.get('ticker')} | {p.get('tags')} | score={p.get('score'):.2f} | px={p.get('limit_price_cents')}c qty={p.get('contracts')} maxloss=${p.get('estimated_max_loss_cents',0)/100:.2f}")
                notify_telegram("\n".join(lines))

        except Exception as e:
            notify_desktop("Kalshi Sentinel", f"Paper loop error: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()
