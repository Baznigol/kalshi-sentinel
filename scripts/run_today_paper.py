#!/usr/bin/env python3
"""Paper-mode daily runner.

Selects candidate markets closing soon for Politics/Crypto based on simple heuristics
and prints a proposed trade plan.

NOTE: This does NOT place orders.
"""

import os
import datetime as dt
from dotenv import load_dotenv

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from kalshi_client import KalshiClient

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
load_dotenv(os.path.join(REPO_DIR, 'config', '.env'))

KEYWORDS = {
    'crypto': ['BTC', 'BITCOIN', 'ETH', 'ETHEREUM', 'CRYPTO', 'SOL', 'DOGE'],
    'politics': ['ELECTION', 'TRUMP', 'BIDEN', 'SENATE', 'HOUSE', 'PRES', 'GOV', 'APPROVAL', 'CONGRESS'],
}


def parse_iso(s):
    if not s:
        return None
    # Kalshi returns Z timestamps
    try:
        return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


def main():
    kc = KalshiClient.from_env()

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now + dt.timedelta(hours=12)
    budget = 10.0

    # Fetch a decent chunk of markets
    markets = []
    cursor = None
    for _ in range(5):
        params = {'limit': 200}
        if cursor:
            params['cursor'] = cursor
        data = kc.get('/markets', params=params)
        markets.extend(data.get('markets', []))
        cursor = data.get('cursor')
        if not cursor:
            break

    def classify(m):
        text = f"{m.get('title','')} {m.get('ticker','')} {m.get('event_ticker','')}".upper()
        tags = []
        for k, kws in KEYWORDS.items():
            if any(x in text for x in kws):
                tags.append(k)
        return tags

    cands = []
    for m in markets:
        close_t = parse_iso(m.get('close_time'))
        if not close_t:
            continue
        if close_t < now or close_t > cutoff:
            continue
        tags = classify(m)
        if not tags:
            continue
        # heuristic: prefer markets with some liquidity
        liq = float(m.get('liquidity', 0) or 0)
        vol = float(m.get('volume_24h', 0) or 0)
        yes_bid = m.get('yes_bid')
        no_bid = m.get('no_bid')
        # basic sanity
        if yes_bid is None or no_bid is None:
            continue
        cands.append((liq, vol, close_t, tags, m))

    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)

    print('KALSHI SENTINEL — PAPER MODE (NO ORDERS)')
    print(f'UTC now: {now.isoformat()}')
    print(f'Cutoff (UTC): {cutoff.isoformat()}')
    print(f'Risk budget: ${budget:.2f}')
    print('')

    if not cands:
        print('No candidates found in the sample window. Increase pages or broaden cutoff.')
        return

    top = cands[:10]
    for i, (liq, vol, close_t, tags, m) in enumerate(top, 1):
        print(f"#{i} [{','.join(tags)}] {m.get('ticker')} | closes {close_t.isoformat()}")
        print(f"    {m.get('title')}")
        print(f"    liq=${liq:.0f} vol24h=${vol:.0f} yes_bid={m.get('yes_bid')} yes_ask={m.get('yes_ask')} no_bid={m.get('no_bid')} no_ask={m.get('no_ask')}")

    # Proposed action (placeholder)
    best = top[0][4]
    print('\nPROPOSED TRADE (placeholder, requires model):')
    print(f"- Market: {best.get('ticker')} — {best.get('title')}")
    print(f"- Reason: highest liquidity/volume among near-term crypto/politics markets in sample")
    print("- Next step: add probability model + orderbook-based execution, then place LIMIT order")


if __name__ == '__main__':
    main()
