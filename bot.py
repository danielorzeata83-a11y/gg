#!/usr/bin/env python3
"""
Polymarket copy-trading bot — main entrypoint.

Usage:
  python bot.py --mode paper --watchlist alpha_wallets.json --bankroll 1000
  python bot.py --mode live   --watchlist alpha_wallets.json --bankroll 1000 --really-send
"""
import os
import sys
import json
import time
import signal
import logging
import argparse
import threading
from typing import Optional
from dotenv import load_dotenv

from config import Config, load_config
from ledger import Ledger, LedgerEntry
from decision import DecisionEngine
from executor_paper import PaperExecutor
from executor_live import LiveExecutor
from watch_onchain import AlphaWatcher
from signal_log import write_signal

load_dotenv()


def setup_logging(log_path: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_watchlist(path: str) -> list[str]:
    with open(path) as f:
        data = json.load(f)
    if data and isinstance(data[0], dict):
        return [d["proxyWallet"] for d in data if d.get("proxyWallet")]
    return data


def record_decision(ledger: Ledger, decision, signal: dict, mode: str) -> None:
    entry = LedgerEntry(
        timestamp=time.time(),
        mode=mode,
        entry_type="decision",
        wallet_copied=signal.get("actor", ""),
        token_id=decision.token_id,
        side=decision.side,
        alpha_price=decision.alpha_price,
        intended_price=decision.price,
        intended_size=decision.size_usdc,
        decision_approved=decision.approved,
        rejection_reason=decision.reason if not decision.approved else "",
    )
    ledger.record(entry)


class Bot:
    def __init__(self, cfg: Config, mode: str, rpc_url: str,
                 really_send: bool = False):
        self.cfg = cfg
        self.mode = mode
        self.ledger = Ledger(cfg.ledger_path)
        self.engine = DecisionEngine(cfg, self.ledger)
        self._shutdown = threading.Event()
        self.logger = logging.getLogger("bot")

        if mode == "paper":
            self.executor = PaperExecutor(cfg, self.ledger)
        else:
            self.executor = LiveExecutor(cfg, self.ledger, dry_run=not really_send)
            self.executor.gate_check()

        wallets = load_watchlist(cfg.watchlist_path)
        if not wallets:
            print("No wallets in watchlist. Run discover_alpha.py first.")
            sys.exit(1)

        self.watcher = AlphaWatcher(rpc_url, wallets, on_signal=self._on_signal)
        self.logger.info("Bot initialized: mode=%s wallets=%d", mode, len(wallets))

    def _on_signal(self, signal: dict) -> None:
        self.logger.info("SIGNAL: %s %s $%.2f @ %.4f  wallet=%s",
                         signal["side"], signal["outcomeAssetId"][:16],
                         signal["usdc"], signal["price"], signal["actor"][:10])
        decision = self.engine.evaluate(signal)
        record_decision(self.ledger, decision, signal, self.mode)

        status = "APPROVED" if decision.approved else f"REJECTED ({decision.reason})"
        self.logger.info("DECISION: %s", status)

        write_signal(signal, {
            "approved": decision.approved,
            "reason": decision.reason,
            "price": decision.price,
            "size_usdc": decision.size_usdc,
            "slippage": decision.slippage,
            "alpha_price": decision.alpha_price,
        })

        if decision.approved:
            self.executor.execute(decision, signal)

    def run(self) -> None:
        def _status_loop():
            while not self._shutdown.is_set():
                self.ledger.print_summary(mode=self.mode)
                self._shutdown.wait(300)  # print summary every 5 min

        t = threading.Thread(target=_status_loop, daemon=True)
        t.start()

        def _sigterm(signum, frame):
            self.logger.info("Shutdown signal received")
            self._shutdown.set()
            if self.mode == "live" and hasattr(self.executor, "kill"):
                self.executor.kill()
            sys.exit(0)

        signal.signal(signal.SIGINT, _sigterm)
        signal.signal(signal.SIGTERM, _sigterm)

        self.watcher.run(poll_interval=2.0)

    def run_once(self, signals: list[dict]) -> None:
        """Replay mode: process a list of historical signals without live watcher."""
        for sig in signals:
            self._on_signal(sig)
        self.ledger.print_summary(mode=self.mode)


def main() -> None:
    ap = argparse.ArgumentParser(description="Polymarket copy-trading bot")
    ap.add_argument("--mode", default="paper", choices=["paper", "live"])
    ap.add_argument("--rpc", default=os.getenv("POLYGON_RPC_URL", ""),
                    help="Polygon RPC URL (wss:// or https://)")
    ap.add_argument("--watchlist", default="alpha_wallets.json")
    ap.add_argument("--bankroll", type=float, default=0.0,
                    help="Your USDC bankroll (required)")
    ap.add_argument("--really-send", action="store_true",
                    help="Actually post live orders (live mode only)")
    ap.add_argument("--once", metavar="SIGNALS_JSON",
                    help="Replay mode: path to JSON array of signals")
    args = ap.parse_args()

    cfg = load_config()
    if args.bankroll > 0:
        cfg.bankroll_usdc = args.bankroll
    if args.watchlist:
        cfg.watchlist_path = args.watchlist

    setup_logging(cfg.log_path)

    if args.once:
        with open(args.once) as f:
            signals = json.load(f)
        bot = Bot.__new__(Bot)
        bot.cfg = cfg
        bot.mode = args.mode
        bot.ledger = Ledger(cfg.ledger_path)
        bot.engine = DecisionEngine(cfg, bot.ledger)
        bot.executor = PaperExecutor(cfg, bot.ledger)
        bot.logger = logging.getLogger("bot")
        bot._shutdown = __import__("threading").Event()
        bot.run_once(signals)
        return

    if not args.rpc:
        print("--rpc or POLYGON_RPC_URL required")
        sys.exit(1)

    bot = Bot(cfg, args.mode, args.rpc, really_send=args.really_send)
    bot.run()


if __name__ == "__main__":
    main()
