"""Strategy primitives (paper mode first).

Design goals (from the threads you shared):
- don't confuse 'bot that can trade' with 'bot that can win'
- prioritize microstructure + execution quality + risk controls
- instrument everything so we can iterate

This file intentionally starts simple; we evolve it with measured improvements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import datetime as dt


POLITICS_KWS = [
    "ELECTION",
    "TRUMP",
    "BIDEN",
    "SENATE",
    "HOUSE",
    "PRES",
    "GOV",
    "APPROVAL",
    "CONGRESS",
    "PRIMARY",
]

CRYPTO_KWS = [
    "BTC",
    "BITCOIN",
    "ETH",
    "ETHEREUM",
    "CRYPTO",
]

# For the first version we *only* trade BTC/ETH related markets.
CRYPTO_FOCUS = ["BTC", "BITCOIN", "ETH", "ETHEREUM"]


def parse_iso(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def classify_market(m: Dict[str, Any]) -> List[str]:
    text = f"{m.get('title','')} {m.get('ticker','')} {m.get('event_ticker','')} {m.get('series_ticker','')}".upper()
    tags: List[str] = []
    if any(k in text for k in POLITICS_KWS):
        tags.append("politics")
    if any(k in text for k in CRYPTO_KWS):
        # only tag as crypto if it matches BTC/ETH focus
        if any(k in text for k in CRYPTO_FOCUS):
            tags.append("crypto")
    return tags


@dataclass
class MarketScore:
    ticker: str
    title: str
    tags: List[str]
    close_time: Optional[str]
    liquidity: float
    volume_24h: float
    score: float
    reasons: List[str]


def score_market(m: Dict[str, Any], *, now_utc: dt.datetime, cutoff_utc: dt.datetime) -> Optional[MarketScore]:
    tags = classify_market(m)
    if not tags:
        return None

    close_t = parse_iso(m.get("close_time"))
    if not close_t:
        return None
    if close_t <= now_utc or close_t > cutoff_utc:
        return None

    # Basic microstructure heuristics using fields on /markets.
    # Orderbook-based refinement comes later.
    liq = float(m.get("liquidity", 0) or 0)
    vol24 = float(m.get("volume_24h", 0) or 0)

    reasons: List[str] = []
    score = 0.0

    # Prefer markets with some liquidity.
    score += min(liq / 5000.0, 5.0)
    if liq >= 1000:
        reasons.append("liquidity_ok")
    else:
        reasons.append("low_liquidity")

    # Prefer markets with recent activity.
    score += min(vol24 / 5000.0, 5.0)
    if vol24 >= 1000:
        reasons.append("volume_ok")
    else:
        reasons.append("low_volume")

    # Prefer closer to cutoff (today trading). Not too close (avoid end-of-life chaos).
    hrs = (close_t - now_utc).total_seconds() / 3600.0
    if 0.5 <= hrs <= 12:
        score += 2.0
        reasons.append("closes_soon")
    elif hrs < 0.5:
        score -= 2.0
        reasons.append("too_close")
    else:
        score += 0.5
        reasons.append("not_too_close")

    # Penalize obviously odd markets state.
    status = (m.get("status") or "").lower()
    if status and status not in ("open", "active"):
        score -= 0.5
        reasons.append(f"status_{status}")

    return MarketScore(
        ticker=m.get("ticker") or "",
        title=m.get("title") or "",
        tags=tags,
        close_time=m.get("close_time"),
        liquidity=liq,
        volume_24h=vol24,
        score=score,
        reasons=reasons,
    )


def choose_universe(markets: List[Dict[str, Any]], *, hours_ahead: int = 24) -> List[MarketScore]:
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now + dt.timedelta(hours=hours_ahead)

    scored: List[MarketScore] = []
    for m in markets:
        s = score_market(m, now_utc=now, cutoff_utc=cutoff)
        if s and s.ticker:
            scored.append(s)

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored
