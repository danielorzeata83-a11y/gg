"""Helpers that generate Polymarket UI links."""

from __future__ import annotations

POLYMARKET_BASE = "https://polymarket.com"


def market_link(condition_id: str, slug: str = "") -> str:
    """Return a link to the Polymarket market page."""
    if slug:
        return f"{POLYMARKET_BASE}/event/{slug}"
    return f"{POLYMARKET_BASE}/event/{condition_id}"


def wallet_link(address: str) -> str:
    """Return a link to the Polymarket profile page for *address*."""
    return f"{POLYMARKET_BASE}/profile/{address}"
