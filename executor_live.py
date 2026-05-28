"""
Live executor — places real CLOB orders using py-clob-client-v2.

GATE: refuses to run unless every Phase 3 condition is satisfied.
This module is deliberately locked behind multiple explicit safety checks.
"""
import os
import sys
import time
import logging
import requests
from typing import Optional
from decision import TradeDecision
from ledger import Ledger, LedgerEntry
from config import Config

logger = logging.getLogger(__name__)

# ---- Gate checks -----------------------------------------------------------

def check_live_gate(cfg: Config, ledger: Ledger) -> list[str]:
    """Return list of unmet gate conditions. Empty list = OK to proceed."""
    failures = []

    if os.getenv("I_UNDERSTAND_REAL_MONEY", "").lower() != "yes":
        failures.append("env var I_UNDERSTAND_REAL_MONEY=yes not set")

    resolved = ledger.count_resolved_paper_trades()
    if resolved < cfg.min_paper_trades:
        failures.append(
            f"only {resolved} resolved paper trades; need {cfg.min_paper_trades}")

    if cfg.bankroll_usdc <= 0:
        failures.append("BANKROLL_USDC not set to a positive value")

    # Geoblock check
    try:
        r = requests.get("https://clob.polymarket.com/geo-block-check",
                         timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("blocked"):
                failures.append("your region is geoblocked by Polymarket")
    except Exception as e:
        failures.append(f"could not complete geoblock check: {e}")

    return failures


def _require_confirmation(cfg: Config) -> None:
    """Interactive: show risk limits, require re-typed phrase."""
    print("\n" + "!"*60)
    print("  YOU ARE ABOUT TO TRADE WITH REAL MONEY")
    print("!"*60)
    print(f"  Bankroll         : ${cfg.bankroll_usdc:,.2f} USDC")
    print(f"  Max position     : ${cfg.max_position_usdc:,.2f} USDC")
    print(f"  Max daily loss   : ${cfg.max_daily_loss_usdc:,.2f} USDC")
    print(f"  Max exposure     : ${cfg.max_total_exposure_usdc:,.2f} USDC")
    print(f"  Max slippage     : {cfg.max_slippage:.3f} ({cfg.max_slippage*100:.1f}c)")
    print("!"*60)
    phrase = "I ACCEPT THE RISK"
    inp = input(f'\nType exactly  "{phrase}"  to proceed: ').strip()
    if inp != phrase:
        print("Confirmation failed. Exiting.")
        sys.exit(1)


# ---- Executor --------------------------------------------------------------

class LiveExecutor:
    def __init__(self, cfg: Config, ledger: Ledger, dry_run: bool = True):
        """
        dry_run=True: log the exact order that WOULD be sent, but don't send it.
        dry_run=False + --really-send flag: actually post orders.
        """
        self.cfg = cfg
        self.ledger = ledger
        self.dry_run = dry_run
        self._kill_switch = False
        self._client = None  # initialized lazily after gate

    def gate_check(self) -> None:
        failures = check_live_gate(self.cfg, self.ledger)
        if failures:
            print("\n[LIVE GATE] Cannot proceed. The following conditions are unmet:")
            for f in failures:
                print(f"  x {f}")
            sys.exit(1)

        print("\n[LIVE GATE] All conditions met:")
        print(f"  + I_UNDERSTAND_REAL_MONEY=yes")
        print(f"  + Resolved paper trades >= {self.cfg.min_paper_trades}")
        print(f"  + Geoblock check passed")
        self.ledger.print_summary(mode="paper")
        _require_confirmation(self.cfg)

    def _init_client(self) -> None:
        try:
            from py_clob_client_v2 import ClobClient
            from py_clob_client_v2.clob_types import ApiCreds
        except ImportError:
            print("py-clob-client-v2 not installed. Run: pip install py-clob-client-v2")
            sys.exit(1)

        private_key = os.getenv("PRIVATE_KEY")
        funder = os.getenv("DEPOSIT_WALLET_ADDRESS")
        if not private_key or not funder:
            print("PRIVATE_KEY and DEPOSIT_WALLET_ADDRESS env vars required for live mode.")
            sys.exit(1)

        self._client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=private_key,
            signature_type=3,
            funder=funder,
        )
        # Derive / load API key
        api_key_env = os.getenv("CLOB_API_KEY")
        api_secret_env = os.getenv("CLOB_API_SECRET")
        api_pass_env = os.getenv("CLOB_API_PASSPHRASE")
        if api_key_env and api_secret_env and api_pass_env:
            from py_clob_client_v2.clob_types import ApiCreds
            self._client.creds = ApiCreds(
                api_key=api_key_env,
                api_secret=api_secret_env,
                api_passphrase=api_pass_env,
            )
        else:
            logger.info("Deriving API creds from private key...")
            self._client.creds = self._client.create_or_derive_api_key()

    def kill(self) -> None:
        """Hard kill-switch: stop new orders, cancel open ones."""
        self._kill_switch = True
        logger.warning("KILL SWITCH ACTIVATED — cancelling open orders")
        if self._client:
            try:
                self._client.cancel_all()
            except Exception as e:
                logger.error("cancel_all failed: %s", e)

    def execute(self, decision: TradeDecision, signal: dict) -> None:
        if self._kill_switch:
            logger.warning("Kill switch active — skipping order")
            return
        if self._client is None:
            self._init_client()

        try:
            self._place_order(decision, signal)
        except Exception as e:
            # Fail safe: any unexpected error = no trade
            logger.error("Live execute error (no order placed): %s", e)

    def _place_order(self, decision: TradeDecision, signal: dict) -> None:
        try:
            from py_clob_client_v2 import OrderArgs, PartialCreateOrderOptions
            from py_clob_client_v2.order_builder.constants import BUY, SELL
        except ImportError:
            logger.error("py-clob-client-v2 not installed")
            return

        side_const = BUY if decision.side == "BUY" else SELL

        # Round price to tick size
        tick = decision.tick_size
        price = round(round(decision.price / tick) * tick, 6)
        size_tokens = decision.size_usdc / price if price > 0 else 0

        order_args = OrderArgs(
            token_id=decision.token_id,
            price=price,
            size=round(size_tokens, 4),
            side=side_const,
        )
        options = PartialCreateOrderOptions(
            tick_size=str(tick),
            neg_risk=(signal.get("contract", "").lower() ==
                      "0xc5d563a36ae78145c45a50134d48a1215220f80a"),
        )

        if self.dry_run:
            logger.info("[DRY-RUN] WOULD POST: %s %s @ %.4f size %.4f tokens",
                        decision.side, decision.token_id[:16], price, size_tokens)
            print(f"  [DRY-RUN] WOULD POST: {decision.side} {decision.token_id[:16]}... "
                  f"@ {price:.4f}  size {size_tokens:.4f} tokens")
            return

        # Actually post
        resp = self._client.create_and_post_order(order_args, options=options)
        logger.info("[LIVE] Order posted: %s", resp)

        fill_price = price
        self._record_fill(decision, signal, fill_price, resp)

        # Wait for fill confirmation
        order_id = resp.get("orderID") if isinstance(resp, dict) else None
        if order_id:
            self._await_fill(order_id, decision)

    def _await_fill(self, order_id: str, decision: TradeDecision) -> None:
        deadline = time.time() + self.cfg.order_timeout_seconds
        while time.time() < deadline:
            try:
                status = self._client.get_order(order_id)
                if isinstance(status, dict):
                    s = status.get("status", "")
                    if s in ("MATCHED", "MINED"):
                        logger.info("Order %s filled", order_id)
                        return
                    if s in ("CANCELED", "UNMATCHED"):
                        logger.warning("Order %s not filled (status %s)", order_id, s)
                        return
            except Exception as e:
                logger.warning("Status check failed: %s", e)
            time.sleep(2)
        # Timeout — cancel
        logger.warning("Order %s timed out, cancelling", order_id)
        try:
            self._client.cancel(order_id)
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)

    def _record_fill(self, decision: TradeDecision, signal: dict,
                     fill_price: float, resp: dict) -> None:
        slippage = (fill_price - decision.alpha_price
                    if decision.side == "BUY" else decision.alpha_price - fill_price)
        entry = LedgerEntry(
            timestamp=time.time(),
            mode="live",
            entry_type="fill",
            wallet_copied=signal.get("actor", ""),
            token_id=decision.token_id,
            side=decision.side,
            alpha_price=decision.alpha_price,
            intended_price=decision.price,
            intended_size=decision.size_usdc,
            actual_fill_price=fill_price,
            actual_fill_size=decision.size_usdc,
            fee=decision.size_usdc * self.cfg.fee_assumption,
            slippage_vs_alpha=slippage,
            decision_approved=True,
            extra={"order_response": str(resp)},
        )
        self.ledger.record(entry)
