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


def best_bid_ask(book: dict) -> tuple:
    """Return (best_bid, best_ask) from the book, or (None, None) if missing.

    Order-independent: best ask is the lowest ask price, best bid the highest bid.
    """
    asks = book.get("asks", [])
    bids = book.get("bids", [])
    best_ask = min((float(l.get("price", 0)) for l in asks if float(l.get("size", 0)) > 0),
                   default=None)
    best_bid = max((float(l.get("price", 0)) for l in bids if float(l.get("size", 0)) > 0),
                   default=None)
    return best_bid, best_ask


def available_depth_usdc(book: dict, side: str) -> float:
    """Total USDC available on the side we'd trade into.

    BUY consumes asks, SELL consumes bids. Used to size positions so we don't
    eat more than a safe fraction of the book (which would move price against us).
    """
    levels_key = "asks" if side == "BUY" else "bids"
    levels = book.get(levels_key, [])
    return sum(float(l.get("price", 0)) * float(l.get("size", 0)) for l in levels)


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
    speed_score: float = 1.0  # 0..1 pipeline latency quality (1.0 = unknown/fast)
    spread: float = 0.0       # best_ask - best_bid at decision time


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

        SpeedMetrics gate: if our pipeline latency is poor (score < 0.4 = >3000ms avg)
        we tighten the slippage tolerance by 50% — because a slow pipeline means the
        market has likely already moved by the time our order lands.
        SpeedMetrics is intentionally separate from alpha_score (which measures wallet
        skill) — a great wallet with slow detection is still worth copying, just with
        tighter execution constraints.
        """
        token_id = signal.get("outcomeAssetId", "")
        side = signal.get("side", "")
        alpha_price = signal.get("price", 0.0)
        actor = signal.get("actor", "")
        speed_score = signal.get("speed_score", 1.0)

        # Tighten slippage tolerance when our pipeline is demonstrably slow
        effective_max_slippage = self.cfg.max_slippage
        if speed_score < 0.4:
            effective_max_slippage *= 0.5  # pipeline >3s avg: halve tolerance
        elif speed_score < 0.7:
            effective_max_slippage *= 0.75  # pipeline 1.5-3s: 25% tighter

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

        # ---- 1b. Spread gate (liquidity) ----------------------------------
        # A wide bid-ask spread signals an illiquid market: even if our slippage
        # looks ok against the alpha price, the round-trip cost is high.
        best_bid, best_ask = best_bid_ask(book)
        spread = 0.0
        if best_bid is not None and best_ask is not None:
            spread = best_ask - best_bid
            if spread > self.cfg.max_spread:
                return TradeDecision(False,
                                     f"spread {spread:.4f} > max {self.cfg.max_spread}",
                                     token_id, side, alpha_price=alpha_price,
                                     tick_size=tick_size, spread=spread)

        # ---- 2. Edge check ------------------------------------------------
        # Depth-aware sizing: cap our spend at a safe fraction of the book so our
        # own order doesn't walk the price against us. If the depth-constrained
        # size falls below the floor, the market is too thin — skip.
        desired_size = self._compute_size()
        depth = available_depth_usdc(book, side)
        size_usdc = min(desired_size, self.cfg.depth_safety_fraction * depth)
        if size_usdc < self.cfg.min_position_usdc:
            return TradeDecision(False,
                                 f"insufficient liquidity: depth ${depth:.2f} "
                                 f"caps size to ${size_usdc:.2f} < floor "
                                 f"${self.cfg.min_position_usdc:.2f}",
                                 token_id, side, alpha_price=alpha_price,
                                 tick_size=tick_size, spread=spread)

        avg_fill = walk_book_for_fill(book, side, size_usdc)
        if avg_fill is None:
            return TradeDecision(False, "insufficient liquidity in book", token_id, side,
                                 alpha_price=alpha_price, tick_size=tick_size, spread=spread)

        if side == "BUY":
            slippage = avg_fill - alpha_price
        else:
            slippage = alpha_price - avg_fill  # for SELL we want higher price

        if slippage > effective_max_slippage:
            return TradeDecision(False,
                                 f"slippage {slippage:.4f} > max {effective_max_slippage:.4f}"
                                 f" (speed_score={speed_score:.2f})",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size, speed_score, spread)

        # ---- 3. Cooldown check --------------------------------------------
        ck = (actor, token_id)
        last = self._cooldowns.get(ck, 0.0)
        if time.time() - last < self.cfg.cooldown_seconds:
            return TradeDecision(False,
                                 f"cooldown active (last trade {time.time()-last:.0f}s ago)",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size, speed_score, spread)

        # ---- 4. Risk gates ------------------------------------------------
        daily_loss = self.ledger.daily_loss()
        if daily_loss >= self.cfg.max_daily_loss_usdc:
            return TradeDecision(False,
                                 f"daily loss ${daily_loss:.2f} >= limit ${self.cfg.max_daily_loss_usdc:.2f}",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size, speed_score, spread)

        exposure = self.ledger.open_exposure()
        if exposure + size_usdc > self.cfg.max_total_exposure_usdc:
            return TradeDecision(False,
                                 f"exposure ${exposure+size_usdc:.2f} would exceed max ${self.cfg.max_total_exposure_usdc:.2f}",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size, speed_score, spread)

        open_pos = self.ledger.open_position_count()
        if open_pos >= self.cfg.max_open_positions:
            return TradeDecision(False,
                                 f"open positions {open_pos} >= max {self.cfg.max_open_positions}",
                                 token_id, side, avg_fill, size_usdc, alpha_price,
                                 slippage, tick_size, speed_score, spread)

        # ---- 5. Approved --------------------------------------------------
        self._cooldowns[ck] = time.time()
        return TradeDecision(True, "all checks passed", token_id, side,
                             avg_fill, size_usdc, alpha_price, slippage, tick_size,
                             speed_score, spread)

    def _compute_size(self) -> float:
        """Fixed fraction of bankroll, capped by max_position_usdc."""
        raw = self.cfg.bankroll_usdc * self.cfg.position_fraction
        return min(raw, self.cfg.max_position_usdc)
