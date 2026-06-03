"""Discover and profile alpha wallets from BTC market trades."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import config
from data import fetcher, parser
from metrics import advanced, basic, behavioral
from models import Market, Trade, WalletMetrics

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_wallets_from_markets(markets: List[Market]) -> Set[str]:
    """Collect all unique wallet addresses that traded in the given markets."""
    wallets: Set[str] = set()
    for market in markets:
        logger.info("Fetching trades for market %s: %s", market.condition_id, market.question[:60])
        raw_trades = fetcher.fetch_market_trades(market.condition_id)
        trades = parser.parse_trades(raw_trades)
        for t in trades:
            if t.wallet:
                wallets.add(t.wallet)
    logger.info("Discovered %d unique wallets across %d markets", len(wallets), len(markets))
    return wallets


def fetch_wallet_trades(wallet: str) -> List[Trade]:
    """Fetch and parse all trades for a single wallet."""
    raw = fetcher.fetch_wallet_activity(wallet)
    return parser.parse_trades(raw, wallet=wallet)


def build_market_open_times(trades: List[Trade]) -> Dict[str, int]:
    """Approximate market open time as the earliest trade seen per market."""
    open_times: Dict[str, int] = {}
    for t in trades:
        if t.market_id not in open_times or t.timestamp < open_times[t.market_id]:
            open_times[t.market_id] = t.timestamp
    return open_times


def profile_wallet(
    address: str,
    trades: List[Trade],
    all_wallets_trades: Optional[Dict[str, List[Trade]]] = None,
) -> Optional[WalletMetrics]:
    """Compute all metrics for a wallet and return a WalletMetrics object.

    Returns None if the wallet does not meet minimum criteria.
    """
    if not trades:
        logger.debug("No trades for wallet %s, skipping", address)
        return None

    # ── Basic metrics ─────────────────────────────────────────────────────────
    (
        total_trades,
        win_rate,
        total_volume,
        gross_wins,
        gross_losses,
        net_pnl,
        profit_factor,
    ) = basic.compute_basic_metrics(trades)

    if not basic.meets_minimum_criteria(
        total_trades, win_rate, profit_factor, net_pnl, total_volume,
        config.MIN_TRADES,
        config.MIN_WIN_RATE,
        config.MIN_PROFIT_FACTOR,
        config.MIN_NET_PNL,
        config.MIN_VOLUME,
    ):
        logger.debug(
            "Wallet %s did not meet criteria: trades=%d wr=%.2f pf=%.2f pnl=%.2f vol=%.2f",
            address, total_trades, win_rate, profit_factor, net_pnl, total_volume,
        )
        return None

    # ── Advanced metrics ──────────────────────────────────────────────────────
    sortino = advanced.compute_sortino_ratio(trades)
    consistency = advanced.compute_consistency_score(trades)
    clv = advanced.compute_clv(trades)
    brier = advanced.compute_brier_score(trades)
    market_open_times = build_market_open_times(trades)
    reaction_time = advanced.compute_reaction_time(trades, market_open_times)

    # ── Behavioral flags ──────────────────────────────────────────────────────
    flag_martingale, flag_revenge, flag_fomo, flag_concentration, flag_sybil = (
        behavioral.compute_behavioral_flags(
            trades,
            market_open_times=market_open_times,
            all_wallets_trades=all_wallets_trades or {},
            wallet_address=address,
        )
    )

    wm = WalletMetrics(
        address=address,
        total_trades=total_trades,
        win_rate=win_rate,
        total_volume=total_volume,
        gross_wins=gross_wins,
        gross_losses=gross_losses,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        sortino_ratio=sortino,
        consistency_score=consistency,
        clv=clv,
        brier_score=brier,
        reaction_time_median=reaction_time,
        flag_martingale=flag_martingale,
        flag_revenge=flag_revenge,
        flag_fomo=flag_fomo,
        flag_concentration=flag_concentration,
        flag_sybil=flag_sybil,
        last_updated=_now_iso(),
    )

    return wm


def discover_alpha_wallets(
    markets: List[Market],
    max_wallets: int = 200,
    extra_wallets: Optional[Set[str]] = None,
) -> List[WalletMetrics]:
    """Full pipeline: market trades → wallet addresses → profiles → alpha list.

    1. Collect all wallet addresses trading the given markets.
    2. Fetch each wallet's full trade history.
    3. Compute metrics and filter to alpha criteria.
    4. Return scored WalletMetrics list.
    """
    # Step 1: collect wallet addresses
    wallet_addresses = discover_wallets_from_markets(markets)
    if extra_wallets:
        wallet_addresses |= extra_wallets
        logger.info("Added %d extra wallets from recent trades", len(extra_wallets))

    # Limit to avoid excessive API calls in one run
    wallet_list = list(wallet_addresses)[:max_wallets]
    logger.info("Profiling %d wallets...", len(wallet_list))

    # Step 2: fetch all trades
    all_wallets_trades: Dict[str, List[Trade]] = {}
    for addr in wallet_list:
        trades = fetch_wallet_trades(addr)
        if trades:
            all_wallets_trades[addr] = trades

    # Step 3: profile and filter
    alpha_wallets: List[WalletMetrics] = []
    for addr, trades in all_wallets_trades.items():
        wm = profile_wallet(addr, trades, all_wallets_trades=all_wallets_trades)
        if wm is not None:
            alpha_wallets.append(wm)

    logger.info("Found %d alpha wallets out of %d profiled", len(alpha_wallets), len(wallet_list))
    return alpha_wallets
