"""Behavioral analysis: detect trading anti-patterns and flag wallets."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Tuple

from models import Trade

logger = logging.getLogger(__name__)


def _group_by_market(trades: List[Trade]) -> Dict[str, List[Trade]]:
    groups: Dict[str, List[Trade]] = defaultdict(list)
    for t in trades:
        groups[t.market_id].append(t)
    for v in groups.values():
        v.sort(key=lambda x: x.timestamp)
    return groups


def detect_martingale(trades: List[Trade]) -> bool:
    """Detect Martingale/DCA pattern: increasing position size after a loss."""
    groups = _group_by_market(trades)
    for market_trades in groups.values():
        last_loss = False
        for i, t in enumerate(market_trades):
            if i == 0:
                last_loss = t.outcome == "LOSS" or (t.pnl is not None and t.pnl < 0)
                continue
            prev = market_trades[i - 1]
            if last_loss and t.size > prev.size * 1.5:
                return True
            last_loss = t.outcome == "LOSS" or (t.pnl is not None and t.pnl < 0)
    return False


def detect_revenge_trading(trades: List[Trade]) -> bool:
    """Detect revenge trading: rapid new trade entry within 30s after a loss."""
    REVENGE_WINDOW = 30  # seconds
    sorted_trades = sorted(trades, key=lambda x: x.timestamp)
    for i, t in enumerate(sorted_trades[:-1]):
        is_loss = t.outcome == "LOSS" or (t.pnl is not None and t.pnl < 0)
        if not is_loss:
            continue
        next_t = sorted_trades[i + 1]
        if 0 < (next_t.timestamp - t.timestamp) <= REVENGE_WINDOW:
            return True
    return False


def detect_fomo(trades: List[Trade], market_open_times: Dict[str, int] = None) -> bool:
    """Detect FOMO: entering a market very late (>80% of market lifetime elapsed)
    when price has already moved significantly (price > 0.75 or price < 0.25).
    """
    if not market_open_times:
        return False

    # We need market end times; approximate using latest trade timestamp per market
    market_last_ts: Dict[str, int] = {}
    for t in trades:
        if t.market_id not in market_last_ts or t.timestamp > market_last_ts[t.market_id]:
            market_last_ts[t.market_id] = t.timestamp

    groups = _group_by_market(trades)
    for market_id, mtrades in groups.items():
        open_ts = market_open_times.get(market_id)
        if not open_ts:
            continue
        end_ts = market_last_ts.get(market_id, 0)
        market_duration = end_ts - open_ts
        if market_duration <= 0:
            continue
        first_entry = mtrades[0].timestamp
        elapsed_pct = (first_entry - open_ts) / market_duration
        if elapsed_pct > 0.8:
            price = mtrades[0].price or 0.5
            if price > 0.75 or price < 0.25:
                return True
    return False


def detect_concentration(trades: List[Trade]) -> bool:
    """Detect concentration risk: single position > 30% of total capital deployed."""
    total_capital = sum(t.price * t.size if t.price and t.size else t.size for t in trades)
    if total_capital <= 0:
        return False

    groups = _group_by_market(trades)
    for mtrades in groups.values():
        position_size = sum(
            t.price * t.size if t.price and t.size else t.size for t in mtrades
        )
        if position_size / total_capital > 0.30:
            return True
    return False


def detect_sybil(
    wallet_address: str,
    all_wallets_trades: Dict[str, List[Trade]],
    time_window: int = 5,
) -> bool:
    """Detect Sybil clustering: wallet entries are suspiciously correlated with
    another wallet (same markets, same side, within *time_window* seconds).

    Returns True if the wallet appears to be a Sybil clone of another.
    Requires the full trade dict for all wallets being analysed.
    """
    our_trades = all_wallets_trades.get(wallet_address, [])
    if not our_trades:
        return False

    our_set: Dict[str, List[Tuple[str, int, float]]] = defaultdict(list)
    for t in our_trades:
        our_set[t.market_id].append((t.side, t.timestamp, t.size))

    for other_addr, other_trades in all_wallets_trades.items():
        if other_addr == wallet_address:
            continue
        match_count = 0
        total_overlap = 0
        for t in other_trades:
            our_market_trades = our_set.get(t.market_id, [])
            for side, ts, size in our_market_trades:
                if side == t.side and abs(ts - t.timestamp) <= time_window:
                    match_count += 1
                    break
            total_overlap += 1
        if total_overlap > 0 and match_count / total_overlap > 0.80 and match_count >= 10:
            logger.warning(
                "Sybil cluster detected: %s mirrors %s (%d/%d trades match)",
                wallet_address, other_addr, match_count, total_overlap,
            )
            return True
    return False


def compute_behavioral_flags(
    trades: List[Trade],
    market_open_times: Dict[str, int] = None,
    all_wallets_trades: Dict[str, List[Trade]] = None,
    wallet_address: str = "",
) -> Tuple[bool, bool, bool, bool, bool]:
    """Return (martingale, revenge, fomo, concentration, sybil) boolean flags."""
    if market_open_times is None:
        market_open_times = {}
    if all_wallets_trades is None:
        all_wallets_trades = {}

    martingale = detect_martingale(trades)
    revenge = detect_revenge_trading(trades)
    fomo = detect_fomo(trades, market_open_times)
    concentration = detect_concentration(trades)
    sybil = detect_sybil(wallet_address, all_wallets_trades) if wallet_address else False

    return martingale, revenge, fomo, concentration, sybil
