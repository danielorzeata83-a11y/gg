"""Filter Polymarket markets to BTC short-term prediction markets."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

import config
from models import Market

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_end_time(end_time_str: str) -> datetime | None:
    """Parse ISO-8601 end time string to UTC datetime."""
    if not end_time_str:
        return None
    try:
        # Handle Z suffix and +00:00
        clean = end_time_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def is_btc_market(market: Market) -> bool:
    """Return True if the market question references BTC or Bitcoin."""
    q = market.question.lower()
    return any(kw in q for kw in config.BTC_KEYWORDS)


def is_short_term_live(market: Market, window_minutes: int = config.SHORT_TERM_WINDOW_MINUTES) -> bool:
    """Return True if the market ends within *window_minutes* from now (live scalp)."""
    end_dt = _parse_end_time(market.end_time)
    if end_dt is None:
        return False
    now = _now()
    return now <= end_dt <= now + timedelta(minutes=window_minutes)


def is_recently_resolved(market: Market, hours: int = config.RECENTLY_RESOLVED_HOURS) -> bool:
    """Return True if the market resolved within the last *hours* hours."""
    if not market.resolved:
        return False
    end_dt = _parse_end_time(market.end_time)
    if end_dt is None:
        return False
    return end_dt >= _now() - timedelta(hours=hours)


def has_sufficient_volume(market: Market, min_volume: float = config.MIN_MARKET_VOLUME) -> bool:
    return market.volume >= min_volume


def is_recent_market(market: Market, days: int = 30) -> bool:
    """Return True if market was created or resolved within the last *days* days."""
    end_dt = _parse_end_time(market.end_time)
    if end_dt is None:
        return market.active
    cutoff = _now() - timedelta(days=days)
    return end_dt >= cutoff


def filter_btc_markets(markets: List[Market]) -> List[Market]:
    """Return BTC markets that are recent (last 30 days) OR active, with sufficient volume."""
    result: List[Market] = []
    for m in markets:
        if not is_btc_market(m):
            continue
        if not has_sufficient_volume(m):
            continue
        # Accept: active markets created recently OR resolved in last 30 days
        if m.active and is_recent_market(m, days=365):
            result.append(m)
        elif is_recently_resolved(m) and is_recent_market(m, days=30):
            result.append(m)
    logger.info(
        "Market filter: %d/%d markets passed BTC filter",
        len(result), len(markets),
    )
    return result


def filter_live_btc_markets(markets: List[Market]) -> List[Market]:
    """Strict filter: only markets ending within SHORT_TERM_WINDOW_MINUTES."""
    return [
        m for m in markets
        if is_btc_market(m) and is_short_term_live(m) and has_sufficient_volume(m)
    ]


def compute_spread(best_bid: float, best_ask: float) -> float:
    """Return spread as fraction of ask price."""
    if best_ask <= 0:
        return 1.0
    return (best_ask - best_bid) / best_ask


def is_liquid(market: Market, max_spread: float = config.MAX_SPREAD_PCT) -> bool:
    """Return True if the market spread is within the acceptable threshold."""
    sp = market.spread_pct
    if sp is None:
        return False
    return sp <= max_spread


def detect_odds_momentum(price_series: List[float], threshold: float = config.ODDS_MOMENTUM_PCT) -> bool:
    """Return True if the price moved more than *threshold* in the last 5 minutes.

    *price_series* should be a list of prices ordered oldest-to-newest,
    at ~1-min intervals (last 5 prices used).
    """
    if len(price_series) < 2:
        return False
    oldest = price_series[max(0, len(price_series) - 5)]
    newest = price_series[-1]
    if oldest <= 0:
        return False
    return abs(newest - oldest) / oldest >= threshold
