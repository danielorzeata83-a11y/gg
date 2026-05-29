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

    # ---- Liquidity gates ---------------------------------------------------
    # Reject markets whose bid-ask spread is wider than this (illiquid -> hidden cost).
    max_spread: float = 0.05
    # Never consume more than this fraction of the book's available depth on our side
    # (leaves a cushion so our own order doesn't move the price against us).
    depth_safety_fraction: float = 0.50
    # If depth-constrained size falls below this floor, skip the trade entirely.
    min_position_usdc: float = 5.0

    # ---- Market-metadata gates (require a Gamma cache; skipped if absent) ---
    # Reject trades on markets resolving within this many hours (thin/volatile).
    # 0 disables the gate.
    min_hours_to_resolution: float = 0.0
    # Reject markets whose Gamma liquidity is below this (USDC). 0 disables.
    min_market_liquidity: float = 0.0

    # ---- Timing ------------------------------------------------------------
    cooldown_seconds: float = 300.0     # per (wallet, market) pair
    order_timeout_seconds: float = 30.0

    # ---- Paper-trading gate ------------------------------------------------
    min_paper_trades: int = 30

    # ---- Execution speed ---------------------------------------------------
    # monitoring_mode: "ws_subscribe" (fastest) or "polling" (fallback)
    # SpeedMetrics tighten slippage automatically — no manual tuning needed.
    monitoring_mode: str = "ws_subscribe"
    # If pipeline avg latency exceeds this threshold (ms), warn in logs.
    slow_pipeline_warn_ms: float = 2000.0

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
        "MAX_SPREAD": ("max_spread", float),
        "DEPTH_SAFETY_FRACTION": ("depth_safety_fraction", float),
        "MIN_POSITION_USDC": ("min_position_usdc", float),
        "MIN_HOURS_TO_RESOLUTION": ("min_hours_to_resolution", float),
        "MIN_MARKET_LIQUIDITY": ("min_market_liquidity", float),
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
        "MONITORING_MODE": ("monitoring_mode", str),
        "SLOW_PIPELINE_WARN_MS": ("slow_pipeline_warn_ms", float),
    }
    for env_key, (attr, cast) in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            setattr(cfg, attr, cast(val))
    return cfg
