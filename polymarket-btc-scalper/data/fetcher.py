"""Raw API fetchers – all network calls live here."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import config
from utils.helpers import get_with_retry

logger = logging.getLogger(__name__)


def fetch_active_markets(limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
    """Fetch markets from Gamma API (active + not closed)."""
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "offset": offset,
    }
    data = get_with_retry(config.GAMMA_MARKETS_URL, params=params)
    if data is None:
        return []
    # Gamma may return a list directly or wrap it
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("markets", data.get("results", []))
    return []


def fetch_all_active_markets() -> List[Dict[str, Any]]:
    """Paginate through all active markets."""
    all_markets: List[Dict[str, Any]] = []
    offset = 0
    limit = 500
    while True:
        batch = fetch_active_markets(limit=limit, offset=offset)
        if not batch:
            break
        all_markets.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    logger.info("Fetched %d active markets from Gamma API", len(all_markets))
    return all_markets


def fetch_recently_closed_markets(limit: int = 200) -> List[Dict[str, Any]]:
    """Fetch recently resolved markets from Gamma API."""
    params = {
        "active": "false",
        "closed": "true",
        "limit": limit,
    }
    data = get_with_retry(config.GAMMA_MARKETS_URL, params=params)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("markets", data.get("results", []))
    return []


def fetch_market_trades(condition_id: str, limit: int = config.MARKET_TRADE_LIMIT) -> List[Dict[str, Any]]:
    """Fetch trades for a specific market from the data API."""
    params = {"market": condition_id, "limit": limit}
    data = get_with_retry(config.DATA_TRADES_URL, params=params)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("trades", data.get("results", []))
    return []


def fetch_wallet_activity(wallet: str, limit: int = config.WALLET_TRADE_LIMIT) -> List[Dict[str, Any]]:
    """Fetch all activity for a wallet address from the data API."""
    params = {"user": wallet, "limit": limit}
    data = get_with_retry(config.DATA_ACTIVITY_URL, params=params)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("activity", data.get("results", []))
    return []


def fetch_orderbook(token_id: str) -> Optional[Dict[str, Any]]:
    """Fetch CLOB order-book for a token (market outcome token)."""
    params = {"token_id": token_id}
    return get_with_retry(config.CLOB_ORDERBOOK_URL, params=params)
