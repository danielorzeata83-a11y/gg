"""
Bot configuration — all tunables in one place.
Conservative defaults. BANKROLL_USDC has no default and must be set.
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    # ---- Bankroll & sizing -------------------------------------------------
    bankroll_usdc: float = 0.0          # REQUIRED — no default, set via env/arg
    position_fraction: float = 0.02     # 2% of bankroll per trade
    max_position_usdc: float = 50.0     # hard cap per position

    # ---- Risk gates --------------------------------------------------------
    max_slippage: float = 0.02          # reject if our price > alpha price + this
    max_open_positions: int = 5
    max_total_exposure_usdc: float = 200.0
    max_daily_loss_usdc: float = 50.0

    # ---- Timing ------------------------------------------------------------
    cooldown_seconds: float = 300.0     # per (wallet, market) pair
    order_timeout_seconds: float = 30.0

    # ---- Paper-trading gate ------------------------------------------------
    min_paper_trades: int = 30

    # ---- Fees --------------------------------------------------------------
    # Polymarket charges 2% on winning positions (taker fee).
    # We model a conservative 2% round-trip assumption for paper simulation.
    fee_assumption: float = 0.02

    # ---- Paths -------------------------------------------------------------
    watchlist_path: str = "alpha_wallets.json"
    ledger_path: str = "ledger.jsonl"
    log_path: str = "bot.log"

    # ---- On-chain verification (optional) ----------------------------------
    # Polygonscan API key for wallet-age / tx-count / sybil verification in
    # discover_alpha.py. Optional — discovery degrades gracefully without it.
    polygonscan_api_key: str = ""


def load_config() -> Config:
    """Load config, overriding defaults from environment variables."""
    cfg = Config()
    env_map = {
        "BANKROLL_USDC": ("bankroll_usdc", float),
        "POSITION_FRACTION": ("position_fraction", float),
        "MAX_POSITION_USDC": ("max_position_usdc", float),
        "MAX_SLIPPAGE": ("max_slippage", float),
        "MAX_OPEN_POSITIONS": ("max_open_positions", int),
        "MAX_TOTAL_EXPOSURE_USDC": ("max_total_exposure_usdc", float),
        "MAX_DAILY_LOSS_USDC": ("max_daily_loss_usdc", float),
        "COOLDOWN_SECONDS": ("cooldown_seconds", float),
        "ORDER_TIMEOUT_SECONDS": ("order_timeout_seconds", float),
        "MIN_PAPER_TRADES": ("min_paper_trades", int),
        "FEE_ASSUMPTION": ("fee_assumption", float),
        "WATCHLIST_PATH": ("watchlist_path", str),
        "LEDGER_PATH": ("ledger_path", str),
        "LOG_PATH": ("log_path", str),
        "POLYGONSCAN_API_KEY": ("polygonscan_api_key", str),
    }
    for env_key, (attr, cast) in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            setattr(cfg, attr, cast(val))
    return cfg
