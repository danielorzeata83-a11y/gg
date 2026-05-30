"""Orchestrator entry point for polymarket-btc-scalper."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from typing import List

import config
from data import fetcher, parser, storage
from scanner import market_filter, wallet_discovery
from analyzer import alpha_scorer
from analyzer.portfolio_tracker import PortfolioTracker
from notifications.alert_system import fire_convergence_alert, fire_new_alpha_alert
from models import WalletMetrics
from utils.helpers import setup_logging

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Core scan cycle ───────────────────────────────────────────────────────────

def run_scan_cycle() -> List[WalletMetrics]:
    """Run one complete scan: markets → wallets → score → persist → alert."""
    logger.info("=== Scan cycle started at %s ===", _now_iso())

    # 1. Fetch and filter markets
    logger.info("Step 1: Fetching active markets from Gamma API…")
    raw_active = fetcher.fetch_all_active_markets()
    raw_closed = fetcher.fetch_recently_closed_markets()
    all_raw = raw_active + raw_closed

    all_markets = parser.parse_markets(all_raw)
    btc_markets = market_filter.filter_btc_markets(all_markets)
    logger.info("Found %d BTC-related markets (active + recently closed)", len(btc_markets))

    if not btc_markets:
        logger.warning("No BTC markets found. Check API connectivity or keyword config.")
        return []

    # 2. Persist markets
    for m in btc_markets:
        storage.upsert_market(m)

    # 3. Discover alpha wallets
    logger.info("Step 2: Discovering and profiling wallets…")
    alpha_wallets = wallet_discovery.discover_alpha_wallets(btc_markets)

    if not alpha_wallets:
        logger.info("No alpha wallets found in this cycle.")
        return []

    # 4. Score wallets
    logger.info("Step 3: Scoring %d candidate wallets…", len(alpha_wallets))
    scored_wallets = alpha_scorer.score_wallets(alpha_wallets)

    # 5. Load existing wallet addresses to detect NEW ones
    existing = {w["address"] for w in storage.get_all_wallets()}

    # 6. Persist wallets and fire new-alpha alerts
    for wm in scored_wallets:
        if wm.flag_sybil:
            logger.info("Skipping Sybil wallet %s", wm.address)
            continue
        is_new = wm.address not in existing
        storage.upsert_wallet(wm)
        if is_new and wm.alpha_score >= 50:
            logger.info("New alpha wallet: %s (score=%.1f)", wm.address, wm.alpha_score)
            fire_new_alpha_alert(wm)

    # 7. Run convergence detection on live markets
    logger.info("Step 4: Running convergence detection…")
    live_markets = market_filter.filter_live_btc_markets(all_markets)
    if live_markets:
        tracker = PortfolioTracker(scored_wallets)
        convergence_events = tracker.scan_recent_trades(live_markets)
        for event in convergence_events:
            # Find a question for the market if available
            q = next(
                (m.question for m in live_markets if m.condition_id == event.market_id),
                event.market_id,
            )
            fire_convergence_alert(event.market_id, event.wallets, market_question=q)
        logger.info(
            "Convergence scan: %d live markets checked, %d events fired",
            len(live_markets), len(convergence_events),
        )
    else:
        logger.info("No live short-term BTC markets found for convergence check.")

    logger.info(
        "=== Scan cycle complete: %d alpha wallets persisted ===",
        len(scored_wallets),
    )
    return scored_wallets


# ── Watch mode ────────────────────────────────────────────────────────────────

def run_watch_mode(interval: int = config.DEFAULT_SCAN_INTERVAL) -> None:
    """Continuously run scan cycles, sleeping *interval* seconds between them."""
    logger.info("Watch mode: scanning every %d seconds (Ctrl-C to stop)", interval)
    cycle = 0
    while True:
        cycle += 1
        logger.info("--- Watch cycle #%d ---", cycle)
        try:
            run_scan_cycle()
        except KeyboardInterrupt:
            logger.info("Watch mode interrupted by user.")
            break
        except Exception as exc:
            logger.exception("Unhandled error in scan cycle: %s", exc)

        logger.info("Sleeping %d seconds until next scan…", interval)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Watch mode interrupted during sleep.")
            break


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Polymarket BTC Scalper – alpha wallet discovery and dashboard.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scan",
        action="store_true",
        help="Run a single scan cycle and exit.",
    )
    group.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously, scanning on an interval.",
    )
    group.add_argument(
        "--dashboard",
        action="store_true",
        help=f"Start the Flask dashboard (port {config.DASHBOARD_PORT}).",
    )
    p.add_argument(
        "--scan-interval",
        type=int,
        default=config.DEFAULT_SCAN_INTERVAL,
        metavar="SECONDS",
        help="Seconds between scans in --watch mode (default: %(default)s).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    # Always ensure the DB is initialised before any operation
    storage.init_db()

    if args.scan:
        wallets = run_scan_cycle()
        print(f"\nScan complete. {len(wallets)} alpha wallet(s) found and persisted.")

    elif args.watch:
        run_watch_mode(interval=args.scan_interval)

    elif args.dashboard:
        from dashboard.app import run_dashboard
        run_dashboard()


if __name__ == "__main__":
    main()
