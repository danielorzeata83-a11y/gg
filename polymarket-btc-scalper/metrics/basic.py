"""Basic per-wallet metrics: win rate, profit factor, net PnL, volume."""

from __future__ import annotations

import logging
from typing import List, Tuple

from models import Trade

logger = logging.getLogger(__name__)


def compute_basic_metrics(
    trades: List[Trade],
) -> Tuple[int, float, float, float, float, float, float]:
    """Return (total_trades, win_rate, total_volume, gross_wins, gross_losses, net_pnl, profit_factor).

    A trade is considered a win when pnl > 0, or outcome == "WIN".
    Volume is sum of (price * size) for each trade.
    """
    if not trades:
        return 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    wins = 0
    total_volume = 0.0
    gross_wins = 0.0
    gross_losses = 0.0
    resolved_count = 0

    for t in trades:
        notional = t.price * t.size if t.price and t.size else t.size
        total_volume += notional

        if t.pnl is not None:
            resolved_count += 1
            if t.pnl > 0:
                wins += 1
                gross_wins += t.pnl
            elif t.pnl < 0:
                gross_losses += abs(t.pnl)
        elif t.outcome == "WIN":
            wins += 1
            resolved_count += 1
            # estimate pnl from size
            gross_wins += t.size * (1.0 - t.price) if t.price else t.size
        elif t.outcome == "LOSS":
            resolved_count += 1
            gross_losses += t.size * t.price if t.price else t.size

    total_trades = len(trades)
    win_rate = wins / resolved_count if resolved_count > 0 else 0.0
    net_pnl = gross_wins - gross_losses
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else (gross_wins if gross_wins > 0 else 0.0)

    return total_trades, win_rate, total_volume, gross_wins, gross_losses, net_pnl, profit_factor


def meets_minimum_criteria(
    total_trades: int,
    win_rate: float,
    profit_factor: float,
    net_pnl: float,
    total_volume: float,
    min_trades: int,
    min_win_rate: float,
    min_profit_factor: float,
    min_net_pnl: float,
    min_volume: float,
) -> bool:
    """Return True only if all minimum alpha criteria are satisfied."""
    if total_trades < min_trades:
        return False
    if win_rate < min_win_rate:
        return False
    if profit_factor < min_profit_factor:
        return False
    if net_pnl <= min_net_pnl:
        return False
    if total_volume < min_volume:
        return False
    return True
