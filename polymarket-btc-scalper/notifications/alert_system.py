"""Alert system: Telegram webhook + local DB alert persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import config
from data.storage import insert_alert
from models import Alert, WalletMetrics
from utils.helpers import post_with_retry

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram_url() -> Optional[str]:
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        return None
    return f"https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(message: str) -> bool:
    """Send a message to the configured Telegram chat. Returns True on success."""
    url = _telegram_url()
    if not url:
        logger.debug("Telegram not configured, skipping notification")
        return False
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        logger.debug("TELEGRAM_CHAT_ID not set, skipping notification")
        return False
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    result = post_with_retry(url, payload)
    if result:
        logger.info("Telegram message sent successfully")
        return True
    logger.warning("Failed to send Telegram message")
    return False


# ── Alert creation ────────────────────────────────────────────────────────────

def fire_convergence_alert(
    market_id: str,
    wallets: List[str],
    market_question: str = "",
) -> Alert:
    """Create and persist a convergence alert, and send Telegram notification."""
    wallet_str = ", ".join(wallets[:5])
    message = (
        f"CONVERGENCE: {len(wallets)} alpha wallets entered {market_question or market_id} "
        f"within 60s\nWallets: {wallet_str}"
    )
    alert = Alert(
        timestamp=_now_iso(),
        alert_type="convergence",
        wallet=wallets[0] if wallets else "",
        market_id=market_id,
        message=message,
    )
    insert_alert(alert)

    tg_msg = (
        f"<b>Convergence Alert</b>\n"
        f"Market: {market_question or market_id}\n"
        f"Alpha wallets ({len(wallets)}): {wallet_str}\n"
        f"Time: {_now_iso()}"
    )
    send_telegram(tg_msg)
    logger.info("Convergence alert fired for market %s with %d wallets", market_id, len(wallets))
    return alert


def fire_new_alpha_alert(wm: WalletMetrics) -> Alert:
    """Create and persist a new alpha wallet alert, and send Telegram notification."""
    message = (
        f"NEW ALPHA WALLET: {wm.address[:12]}... "
        f"score={wm.alpha_score:.1f} wr={wm.win_rate:.1%} "
        f"pf={wm.profit_factor:.2f} trades={wm.total_trades}"
    )
    alert = Alert(
        timestamp=_now_iso(),
        alert_type="new_alpha",
        wallet=wm.address,
        market_id="",
        message=message,
    )
    insert_alert(alert)

    tg_msg = (
        f"<b>New Alpha Wallet Found</b>\n"
        f"Address: <code>{wm.address}</code>\n"
        f"Alpha Score: {wm.alpha_score:.1f}/100\n"
        f"Win Rate: {wm.win_rate:.1%} | Profit Factor: {wm.profit_factor:.2f}\n"
        f"Trades: {wm.total_trades} | Volume: ${wm.total_volume:,.0f}"
    )
    send_telegram(tg_msg)
    logger.info("New alpha wallet alert fired for %s (score=%.1f)", wm.address, wm.alpha_score)
    return alert
