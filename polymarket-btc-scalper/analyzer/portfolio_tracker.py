"""Track open positions and recent trades for alpha wallets in real-time."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import config
from data import fetcher, parser
from models import Market, Trade, WalletMetrics

logger = logging.getLogger(__name__)


class ConvergenceEvent:
    """Represents a convergence: 3+ alpha wallets entering the same market within 60s."""

    def __init__(self, market_id: str, wallets: List[str], timestamp: int):
        self.market_id = market_id
        self.wallets = wallets
        self.timestamp = timestamp

    def __repr__(self) -> str:
        return (
            f"ConvergenceEvent(market={self.market_id}, wallets={self.wallets}, "
            f"ts={self.timestamp})"
        )


class PortfolioTracker:
    """Tracks live positions of alpha wallets and detects convergence events."""

    def __init__(self, alpha_wallets: List[WalletMetrics]):
        self.alpha_addresses = {w.address for w in alpha_wallets}
        # market_id -> list of (wallet, timestamp)
        self._recent_entries: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        self._convergence_events: List[ConvergenceEvent] = []

    def record_entry(self, wallet: str, market_id: str, timestamp: int) -> Optional[ConvergenceEvent]:
        """Record a wallet entering a market; check for convergence."""
        if wallet not in self.alpha_addresses:
            return None

        now = int(time.time())
        cutoff = now - config.CONVERGENCE_WINDOW_SECS

        # Clean stale entries
        self._recent_entries[market_id] = [
            (w, ts) for w, ts in self._recent_entries[market_id] if ts >= cutoff
        ]

        # Add new entry (avoid duplicates for same wallet)
        existing_wallets = {w for w, _ in self._recent_entries[market_id]}
        if wallet not in existing_wallets:
            self._recent_entries[market_id].append((wallet, timestamp))

        unique_wallets = list({w for w, _ in self._recent_entries[market_id]})
        if len(unique_wallets) >= config.CONVERGENCE_WALLET_COUNT:
            event = ConvergenceEvent(market_id, unique_wallets, now)
            self._convergence_events.append(event)
            logger.info(
                "CONVERGENCE DETECTED: %d alpha wallets in market %s within %ds",
                len(unique_wallets), market_id, config.CONVERGENCE_WINDOW_SECS,
            )
            # Reset to avoid repeated firing for same group
            self._recent_entries[market_id] = []
            return event
        return None

    def scan_recent_trades(
        self, markets: List[Market]
    ) -> List[ConvergenceEvent]:
        """Fetch latest trades for each market and check for convergence.

        Returns list of new ConvergenceEvents detected in this scan.
        """
        new_events: List[ConvergenceEvent] = []
        now_ts = int(time.time())
        window_cutoff = now_ts - config.CONVERGENCE_WINDOW_SECS

        for market in markets:
            raw_trades = fetcher.fetch_market_trades(
                market.condition_id, limit=50
            )
            trades = parser.parse_trades(raw_trades)
            for t in trades:
                if t.timestamp >= window_cutoff and t.wallet in self.alpha_addresses:
                    event = self.record_entry(t.wallet, t.market_id, t.timestamp)
                    if event:
                        new_events.append(event)

        return new_events

    @property
    def convergence_events(self) -> List[ConvergenceEvent]:
        return list(self._convergence_events)


def compute_copy_readiness(market: Market) -> Dict[str, object]:
    """Return copy-readiness assessment for a market.

    Checks:
    - Spread < 5% (liquidity check)
    - Market is active
    - best_bid and best_ask are available
    """
    spread = market.spread_pct
    liquid = spread is not None and spread < config.MAX_SPREAD_PCT
    return {
        "market_id": market.condition_id,
        "question": market.question,
        "spread_pct": spread,
        "liquid": liquid,
        "best_bid": market.best_bid,
        "best_ask": market.best_ask,
        "copy_ready": liquid and market.active,
    }
