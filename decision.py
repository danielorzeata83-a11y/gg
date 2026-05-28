"""
Decision layer — pure logic, no order placement.

Given a signal from watch_onchain.py, decides whether to copy the trade,
at what size and price, subject to all risk gates.
"""
import time
import logging
import requests
from dataclasses import dataclass
from typing import Optional
from config import Config
from ledger import Ledger

logger = logging.getLogger(__name__)

CLOB_API = "https://clob.polymarket.com"

_clob_session = requests.Session()
_clob_session.headers.update({"Accept": "application/json", "User-Agent": "polybot/1.0"})


def _clob_get(path: str, params: dict = None, retries: int = 4) -> Optional[dict]:
    """CLOB read with backoff. Returns None on total failure."""
    url = f"{CLOB_API}{path}"
    for attempt in range(retries):
        try:
            r = _clob_session.get(url, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(0.5 * (2 ** attempt))
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning("CLOB request failed (%s): %s", path, e)
            time.sleep(0.5 * (2 ** attempt))
    return None


def get_order_book(token_id: str) -> Optional[dict]:
    return _clob_get("/book", {"token_id": token_id})


def get_tick_size(token_id: str) -> float:
    data = _clob_get("/tick-size", {"token_id": token_id})
    if data and "minimum_tick_size" in data:
        return float(data["minimum_tick_size"])
    return 0.01


def walk_book_for_fill(book: dict, side: str, size_usdc: float) -> Optional[float]:
    """Walk the order book to compute average fill price for size_usdc of spend.

    Returns the average price we'd pay, or None if liquidity is insufficient.
    Side 'BUY': we walk the asks (ascending price). 'SELL': we walk the bids (desc).
    """
    levels_key = "asks" if side == "BUY" else "bids"
    levels = book.get(levels_key, [])
    # Each level: {"price": "0.65", "size": "150.0"}
    remaining = size_usdc
    total_cost = 0.0
    total_tokens = 0.0

    for level in levels:
        p = float(level.get("price", 0))
        s = float(level.get("size", 0))  # size in outcome tokens
        level_usdc = p * s
        if remaining <= level_usdc:
            tokens_here = remaining / p
            total_cost += remaining
            total_tokens += tokens_here
            remaining = 0.0
            break
        else:
            total_cost += level_usdc
            total_tokens += s
            remaining -= level_usdc

    if remaining > 0 or total_tokens == 0:
        return None  # insufficient liquidity
    return total_cost / total_tokens


@dataclass
class TradeDecision:
    approved: bool
    reason: str
    token_id: str = ""
    side: str = ""
    price: float = 0.0       # price we'll target
    size_usdc: float = 0.0
    alpha_price: float = 0.0
    slippage: float = 0.0
    tick_size: float = 0.01


class DecisionEngine:
    def __init__(self, cfg: Config, ledger: Ledger):
        self.cfg = cfg
        self.ledger = ledger
        # cooldown tracker: (wallet, token_id) -> last trade timestamp
        self._cooldowns: dict[tuple, float] = {}

    def evaluate(self, signal: dict) -> TradeDecision:
        """
        Given an on-chain signal, decide whether to copy the trade.
        Returns TradeDecision with approved=True only when all gates pass.
        """
        token_id = signal.get("outcomeAssetId", "")
        side = signal.get("side", "")
        alpha_price = signal.get("price", 0.0)
        actor = signal.get("actor", "")

        # ---- 0. Bankroll check (fast fail before any network call) ----------
        if self.cfg.bankroll_usdc <= 0:
            return TradeDecision(False, "BANKROLL_USDC not configured",
                                 token_id, side, alpha_price=alpha_price)

        # ---- 1. Fetch live book -------------------------------------------
        book = get_order_book(token_id)
        if not book:
            return TradeDecision(False, "could not fetch order book", token_id, side,
                                 alpha_price=alpha_price)

        tick_size = get_tick_size(token_id)

        # ---- 2. Edge check ------------------------------------------------
        size_usdc = self._compute_size()
        avg_fill = walk_book_for_fill(book, side, size_usdc)
        if avg_fill is None:
            return TradeDecision(False, "insufficient liquidity in book", token_id, side,
                                 alpha_price=alpha_price, tick_size=tick_size)

        if side == "BUY":
            slippage = avg_fill - alpha_price
        else:
            slippage = alpha_price - avg_fill  # for SELL we want higher price

        if slippage > self.cfg.max_slippage:
            return TradeDecision(False,
                                 f"slippage {slippage:.4f} > max {self.cfg.max_slippage}",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size)

        # ---- 3. Cooldown check --------------------------------------------
        ck = (actor, token_id)
        last = self._cooldowns.get(ck, 0.0)
        if time.time() - last < self.cfg.cooldown_seconds:
            return TradeDecision(False,
                                 f"cooldown active (last trade {time.time()-last:.0f}s ago)",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size)

        # ---- 4. Risk gates ------------------------------------------------
        daily_loss = self.ledger.daily_loss()
        if daily_loss >= self.cfg.max_daily_loss_usdc:
            return TradeDecision(False,
                                 f"daily loss ${daily_loss:.2f} >= limit ${self.cfg.max_daily_loss_usdc:.2f}",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size)

        exposure = self.ledger.open_exposure()
        if exposure + size_usdc > self.cfg.max_total_exposure_usdc:
            return TradeDecision(False,
                                 f"exposure ${exposure+size_usdc:.2f} would exceed max ${self.cfg.max_total_exposure_usdc:.2f}",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size)

        open_pos = self.ledger.open_position_count()
        if open_pos >= self.cfg.max_open_positions:
            return TradeDecision(False,
                                 f"open positions {open_pos} >= max {self.cfg.max_open_positions}",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size)

        # ---- 5. Approved --------------------------------------------------
        self._cooldowns[ck] = time.time()
        return TradeDecision(True, "all checks passed", token_id, side,
                             avg_fill, size_usdc, alpha_price, slippage, tick_size)

    def _compute_size(self) -> float:
        """Fixed fraction of bankroll, capped by max_position_usdc."""
        raw = self.cfg.bankroll_usdc * self.cfg.position_fraction
        return min(raw, self.cfg.max_position_usdc)
