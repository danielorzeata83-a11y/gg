"""
Paper executor — simulates fills against the real live order book.
No real orders. No real money. Records everything in the ledger.
"""
import time
import logging
import requests
from dataclasses import dataclass
from typing import Optional
from decision import TradeDecision, walk_book_for_fill, get_order_book, CLOB_API, _clob_get
from ledger import Ledger, LedgerEntry
from config import Config

logger = logging.getLogger(__name__)


class PaperExecutor:
    def __init__(self, cfg: Config, ledger: Ledger):
        self.cfg = cfg
        self.ledger = ledger
        # open simulated positions: fill entry dict keyed by (wallet, token_id)
        self._open: dict[tuple, dict] = {}

    def execute(self, decision: TradeDecision, signal: dict) -> None:
        """Simulate a fill. Book-walk gives realistic avg price."""
        token_id = decision.token_id
        side = decision.side
        size_usdc = decision.size_usdc

        book = get_order_book(token_id)
        if not book:
            logger.warning("Paper executor: could not fetch book for %s", token_id)
            return

        avg_fill = walk_book_for_fill(book, side, size_usdc)
        if avg_fill is None:
            logger.warning("Paper executor: book too thin for %s %.2f usdc", token_id, size_usdc)
            return

        fee = size_usdc * self.cfg.fee_assumption
        tokens_received = size_usdc / avg_fill if avg_fill > 0 else 0.0
        slippage = (avg_fill - decision.alpha_price
                    if side == "BUY" else decision.alpha_price - avg_fill)

        entry = LedgerEntry(
            timestamp=time.time(),
            mode="paper",
            entry_type="fill",
            wallet_copied=signal.get("actor", ""),
            token_id=token_id,
            side=side,
            alpha_price=decision.alpha_price,
            intended_price=decision.price,
            intended_size=size_usdc,
            actual_fill_price=avg_fill,
            actual_fill_size=size_usdc,
            fee=fee,
            slippage_vs_alpha=slippage,
            decision_approved=True,
            market_question=signal.get("market_question", ""),
        )
        self.ledger.record(entry)

        pos_key = (signal.get("actor", ""), token_id)
        self._open[pos_key] = {
            "entry": entry,
            "fill_price": avg_fill,
            "size_usdc": size_usdc,
            "tokens": tokens_received,
            "side": side,
        }

        logger.info("[PAPER] FILL %s %s %.4f @ %.4f (alpha %.4f, slip %.4f)",
                    side, token_id[:16], size_usdc, avg_fill,
                    decision.alpha_price, slippage)
        print(f"  [PAPER] FILL {side} ${size_usdc:.2f} @ {avg_fill:.4f} "
              f"(alpha {decision.alpha_price:.4f}, slippage {slippage:+.4f})")

    def resolve_position(self, pos_key: tuple, resolved_price: float) -> None:
        """Mark a simulated position as resolved at the given final price."""
        pos = self._open.pop(pos_key, None)
        if not pos:
            return
        entry = pos["entry"]
        tokens = pos["tokens"]
        side = pos["side"]

        if side == "BUY":
            pnl = tokens * resolved_price - pos["size_usdc"] - entry.fee
        else:
            # SELL: we received usdc, track against token value
            pnl = pos["size_usdc"] - tokens * resolved_price - entry.fee

        # Append a resolution record to the ledger
        res = LedgerEntry(
            timestamp=time.time(),
            mode="paper",
            entry_type="resolution",
            wallet_copied=entry.wallet_copied,
            token_id=entry.token_id,
            side=side,
            alpha_price=entry.alpha_price,
            intended_price=entry.intended_price,
            intended_size=entry.intended_size,
            actual_fill_price=entry.actual_fill_price,
            actual_fill_size=entry.actual_fill_size,
            fee=entry.fee,
            slippage_vs_alpha=entry.slippage_vs_alpha,
            decision_approved=True,
            resolved_pnl=round(pnl, 4),
        )
        self.ledger.record(res)
        logger.info("[PAPER] RESOLVED %s pnl=%.4f", entry.token_id[:16], pnl)
