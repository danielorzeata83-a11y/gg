"""Shared data-class models used throughout the project."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class Market:
    condition_id: str
    question: str
    end_time: str                # ISO-8601
    volume: float
    resolved: bool
    active: bool
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    last_price: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def spread_pct(self) -> Optional[float]:
        if self.best_bid and self.best_ask and self.best_ask > 0:
            return (self.best_ask - self.best_bid) / self.best_ask
        return None


@dataclass
class Trade:
    id: str
    wallet: str
    market_id: str
    side: str           # "BUY" | "SELL"
    price: float
    size: float
    timestamp: int      # unix epoch seconds
    outcome: Optional[str] = None   # "WIN" | "LOSS" | None (open)
    pnl: Optional[float] = None


@dataclass
class WalletMetrics:
    address: str
    username: str = ""

    # Basic
    total_trades: int = 0
    win_rate: float = 0.0
    total_volume: float = 0.0
    gross_wins: float = 0.0
    gross_losses: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0

    # Advanced
    sortino_ratio: float = 0.0
    consistency_score: float = 0.0
    clv: float = 0.0            # average CLV across trades
    brier_score: float = 1.0    # lower is better
    reaction_time_median: float = 0.0  # seconds

    # Behavioral flags (True = flag detected)
    flag_martingale: bool = False
    flag_revenge: bool = False
    flag_fomo: bool = False
    flag_concentration: bool = False
    flag_sybil: bool = False

    # Final score
    alpha_score: float = 0.0
    last_updated: str = ""


@dataclass
class Alert:
    timestamp: str
    alert_type: str     # "convergence" | "new_alpha"
    wallet: str
    market_id: str
    message: str


@dataclass
class HeatmapCell:
    day_of_week: int    # 0=Mon … 6=Sun
    hour_of_day: int    # 0-23
    count: int = 0
