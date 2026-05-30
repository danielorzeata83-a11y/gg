"""Parsers that convert raw API dicts into typed model objects."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from models import Market, Trade

logger = logging.getLogger(__name__)


# ── Market parsing ────────────────────────────────────────────────────────────

def parse_market(raw: Dict[str, Any]) -> Optional[Market]:
    """Parse a raw Gamma API market dict into a Market model."""
    try:
        condition_id = (
            raw.get("conditionId")
            or raw.get("condition_id")
            or raw.get("id", "")
        )
        question = raw.get("question", raw.get("title", ""))
        end_time = (
            raw.get("endDate")
            or raw.get("end_date_iso")
            or raw.get("end_time", "")
        )
        volume_raw = raw.get("volume", raw.get("volumeNum", 0))
        try:
            volume = float(volume_raw) if volume_raw else 0.0
        except (TypeError, ValueError):
            volume = 0.0

        active = bool(raw.get("active", True))
        resolved = bool(raw.get("closed", raw.get("resolved", False)))

        return Market(
            condition_id=str(condition_id),
            question=str(question),
            end_time=str(end_time),
            volume=volume,
            resolved=resolved,
            active=active,
            raw=raw,
        )
    except Exception as exc:
        logger.debug("Failed to parse market: %s – %s", raw, exc)
        return None


def parse_markets(raw_list: List[Dict[str, Any]]) -> List[Market]:
    results = []
    for raw in raw_list:
        m = parse_market(raw)
        if m:
            results.append(m)
    return results


# ── Trade parsing ─────────────────────────────────────────────────────────────

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def parse_trade(raw: Dict[str, Any], wallet: str = "") -> Optional[Trade]:
    """Parse a raw trade / activity dict into a Trade model.

    The data API returns slightly different shapes depending on whether you
    call /activity or /trades, so we handle both.
    """
    try:
        trade_id = str(
            raw.get("id")
            or raw.get("tradeId")
            or raw.get("trade_id", "")
        )
        trader = str(
            raw.get("maker")
            or raw.get("taker")
            or raw.get("user")
            or raw.get("wallet")
            or wallet
        )
        market_id = str(
            raw.get("conditionId")
            or raw.get("condition_id")
            or raw.get("market")
            or raw.get("marketId", "")
        )
        side = str(raw.get("side", raw.get("type", "BUY"))).upper()
        price = _safe_float(raw.get("price", raw.get("outcomePrice", 0)))
        size  = _safe_float(raw.get("size", raw.get("amount", raw.get("usdcSize", 0))))

        # Timestamp: accept epoch int or ISO string
        ts_raw = raw.get("timestamp", raw.get("createdAt", raw.get("created_at", 0)))
        if isinstance(ts_raw, str) and "T" in ts_raw:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                timestamp = int(dt.timestamp())
            except ValueError:
                timestamp = 0
        else:
            timestamp = _safe_int(ts_raw)

        outcome_raw = raw.get("outcome", raw.get("result"))
        outcome: Optional[str] = None
        if isinstance(outcome_raw, str) and outcome_raw.upper() in ("WIN", "LOSS"):
            outcome = outcome_raw.upper()

        pnl_raw = raw.get("pnl", raw.get("profit"))
        pnl: Optional[float] = None
        if pnl_raw is not None:
            pnl = _safe_float(pnl_raw)

        if not trade_id:
            trade_id = f"{trader}_{market_id}_{timestamp}"

        return Trade(
            id=trade_id,
            wallet=trader,
            market_id=market_id,
            side=side,
            price=price,
            size=size,
            timestamp=timestamp,
            outcome=outcome,
            pnl=pnl,
        )
    except Exception as exc:
        logger.debug("Failed to parse trade: %s – %s", raw, exc)
        return None


def parse_trades(raw_list: List[Dict[str, Any]], wallet: str = "") -> List[Trade]:
    results = []
    for raw in raw_list:
        t = parse_trade(raw, wallet=wallet)
        if t:
            results.append(t)
    return results
