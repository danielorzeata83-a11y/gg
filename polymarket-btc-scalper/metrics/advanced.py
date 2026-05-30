"""Advanced wallet metrics: Sortino, Consistency, CLV, Brier Score, Reaction Time."""

from __future__ import annotations

import logging
import math
import statistics
from typing import Dict, List, Optional, Tuple

from models import Trade

logger = logging.getLogger(__name__)


def _roi_series(trades: List[Trade]) -> List[float]:
    """Compute per-trade ROI as pnl / (price * size), skip trades without pnl."""
    rois: List[float] = []
    for t in trades:
        if t.pnl is None:
            continue
        cost = t.price * t.size if t.price and t.size else t.size
        if cost > 0:
            rois.append(t.pnl / cost)
    return rois


def compute_sortino_ratio(trades: List[Trade]) -> float:
    """Sortino = Mean(ROI) / StdDev(negative ROIs only).

    Returns 0.0 when there are insufficient data points.
    """
    rois = _roi_series(trades)
    if not rois:
        return 0.0
    mean_roi = statistics.mean(rois)
    negative_rois = [r for r in rois if r < 0]
    if len(negative_rois) < 2:
        # No downside deviation – very good or insufficient data
        return min(mean_roi * 10, 10.0) if mean_roi > 0 else 0.0
    downside_dev = statistics.stdev(negative_rois)
    if downside_dev == 0:
        return 0.0
    return mean_roi / downside_dev


def compute_consistency_score(trades: List[Trade]) -> float:
    """Consistency = 1 - (StdDev(ROI) / Mean(ROI)), clamped to [0, 1]."""
    rois = _roi_series(trades)
    if len(rois) < 2:
        return 0.0
    mean_roi = statistics.mean(rois)
    if mean_roi <= 0:
        return 0.0
    std_roi = statistics.stdev(rois)
    raw = 1.0 - (std_roi / mean_roi)
    return max(0.0, min(1.0, raw))


def compute_clv(trades: List[Trade], final_prices: Optional[Dict[str, float]] = None) -> float:
    """CLV = entry_price - final_price_before_resolution (positive = edge).

    *final_prices* maps market_id -> final price.  When not provided we fall
    back to the last observed price for the same market in the trade list.
    """
    if final_prices is None:
        final_prices = {}

    # Build last-seen price per market from trade data as fallback
    last_price: Dict[str, float] = {}
    for t in sorted(trades, key=lambda x: x.timestamp):
        if t.price:
            last_price[t.market_id] = t.price

    clv_values: List[float] = []
    for t in trades:
        if t.side.upper() != "BUY":
            continue
        fp = final_prices.get(t.market_id) or last_price.get(t.market_id)
        if fp is None or t.price is None:
            continue
        clv_values.append(t.price - fp)

    return statistics.mean(clv_values) if clv_values else 0.0


def compute_brier_score(trades: List[Trade]) -> float:
    """Brier Score = mean((prediction - outcome)^2).

    prediction = entry price (prob of YES).
    outcome    = 1 if WIN else 0.
    Lower is better. Returns 1.0 (worst) when no resolved trades exist.
    """
    squared_errors: List[float] = []
    for t in trades:
        if t.outcome is None:
            continue
        actual = 1.0 if t.outcome == "WIN" else 0.0
        prediction = max(0.0, min(1.0, t.price)) if t.price else 0.5
        squared_errors.append((prediction - actual) ** 2)

    return statistics.mean(squared_errors) if squared_errors else 1.0


def compute_reaction_time(
    trades: List[Trade],
    market_open_times: Optional[Dict[str, int]] = None,
) -> float:
    """Median seconds between market open and wallet's first trade in that market.

    *market_open_times* maps market_id -> unix epoch of market creation.
    When not provided, returns 0.0.
    """
    if not market_open_times:
        return 0.0

    # Group trades by market, take earliest timestamp per market
    first_trade: Dict[str, int] = {}
    for t in trades:
        if t.market_id not in first_trade or t.timestamp < first_trade[t.market_id]:
            first_trade[t.market_id] = t.timestamp

    delays: List[float] = []
    for market_id, first_ts in first_trade.items():
        open_ts = market_open_times.get(market_id)
        if open_ts and first_ts > open_ts:
            delays.append(float(first_ts - open_ts))

    if not delays:
        return 0.0
    return statistics.median(delays)
