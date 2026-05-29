#!/usr/bin/env python3
"""
Polymarket Gamma API client + market-metadata cache.
=====================================================

Gamma is Polymarket's market catalog (https://gamma-api.polymarket.com).
It is the only public source for per-market liquidity, volume, resolution
date, and category tags — none of which the Data API or CLOB expose.

This module builds a token_id -> metadata index so the rest of the pipeline
can look up market context WITHOUT a network call in the hot path. The flow:

  1. fetch_markets()  -> raw market objects
  2. fetch_events()   -> raw event objects (tags live on events, not markets)
  3. build_index()    -> {token_id: MarketMeta} resolving stringified
                         clobTokenIds and attaching event tags

The index is cached on disk (metadata changes slowly) and pre-loaded for the
watchlist, so decision.py / discover_alpha.py read from RAM at decision time.

Two Gamma quirks handled here (verified against Polymarket's own models):
  - `clobTokenIds` and `outcomePrices` arrive as STRINGIFIED JSON, not arrays.
  - There is NO `category` field. Categorisation comes from `tags`
    ({id,label,slug}) which live on the EVENT object, joined via event id.

No authentication required; Gamma is a free public API. The only constraint
is per-IP rate limiting, which the on-disk cache is designed to respect.
"""

import os
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_PAUSE = 0.2  # be gentle with the public endpoint

_session = requests.Session()
_session.headers.update({"Accept": "application/json", "User-Agent": "gamma-client/1.0"})


def _get(path: str, params: dict = None, retries: int = 4) -> Optional[list]:
    """GET with exponential backoff. Returns parsed JSON or None on total failure."""
    url = f"{GAMMA_API}{path}"
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(0.5 * (2 ** attempt))
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Gamma request failed (%s): %s", path, e)
            time.sleep(0.5 * (2 ** attempt))
    return None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_json_field(value):
    """clobTokenIds / outcomePrices arrive as stringified JSON. Accept either
    a real list (already parsed) or a JSON string; return a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _to_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _extract_tags(market: dict) -> list:
    """Pull tag labels from a market's embedded events (if present).

    Gamma sometimes embeds the parent event(s) — each with a `tags` list —
    directly on the market. We collect distinct tag labels.
    """
    labels = []
    for ev in market.get("events", []) or []:
        for tag in ev.get("tags", []) or []:
            label = tag.get("label") if isinstance(tag, dict) else None
            if label and label not in labels:
                labels.append(label)
    return labels


def _event_id_of(market: dict) -> str:
    events = market.get("events", []) or []
    if events and isinstance(events[0], dict):
        return str(events[0].get("id", ""))
    return ""


# ---------------------------------------------------------------------------
# Metadata model
# ---------------------------------------------------------------------------

@dataclass
class MarketMeta:
    token_id: str
    condition_id: str = ""
    question: str = ""
    slug: str = ""
    liquidity: float = 0.0       # liquidityNum
    volume: float = 0.0          # volumeNum
    volume24hr: float = 0.0
    spread: float = 0.0
    end_date: str = ""           # ISO string
    active: bool = True
    closed: bool = False
    event_id: str = ""
    tags: list = field(default_factory=list)

    def hours_to_resolution(self, now: Optional[datetime] = None) -> Optional[float]:
        """Hours until end_date. None if unknown. Negative if already past."""
        if not self.end_date:
            return None
        dt = _parse_iso(self.end_date)
        if dt is None:
            return None
        now = now or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - now).total_seconds() / 3600


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    for fmt in (None,):  # try fromisoformat first
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).rstrip("Z"), fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_markets(active: bool = True, closed: bool = False,
                  max_pages: int = 20, per_page: int = 100) -> list:
    """Page through /markets. Returns a flat list of raw market dicts."""
    out = []
    for page in range(max_pages):
        params = {
            "limit": per_page,
            "offset": page * per_page,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        rows = _get("/markets", params)
        if not rows:
            break
        out.extend(rows)
        if len(rows) < per_page:
            break
        time.sleep(REQUEST_PAUSE)
    return out


def fetch_events(active: bool = True, closed: bool = False,
                 max_pages: int = 20, per_page: int = 100) -> list:
    """Page through /events. Tags (categories) live here, on the event."""
    out = []
    for page in range(max_pages):
        params = {
            "limit": per_page,
            "offset": page * per_page,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        rows = _get("/events", params)
        if not rows:
            break
        out.extend(rows)
        if len(rows) < per_page:
            break
        time.sleep(REQUEST_PAUSE)
    return out


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_index(markets: list, events: Optional[list] = None) -> dict:
    """
    Build {token_id: MarketMeta}. Both YES and NO token ids of a market point
    to the same metadata. clobTokenIds is resolved from its stringified form.

    If `events` is provided, its tags are joined onto markets by event id —
    this covers the case where markets don't embed their events inline.
    """
    # event_id -> [tag labels], from a standalone /events fetch
    event_tags = {}
    if events:
        for ev in events:
            eid = str(ev.get("id", ""))
            labels = []
            for tag in ev.get("tags", []) or []:
                label = tag.get("label") if isinstance(tag, dict) else None
                if label and label not in labels:
                    labels.append(label)
            if eid:
                event_tags[eid] = labels

    index = {}
    for m in markets:
        token_ids = _parse_json_field(m.get("clobTokenIds"))
        if not token_ids:
            continue
        event_id = _event_id_of(m)
        # Prefer tags embedded on the market's events; fall back to /events join
        tags = _extract_tags(m)
        if not tags and event_id in event_tags:
            tags = event_tags[event_id]

        meta_base = dict(
            condition_id=str(m.get("conditionId", "")),
            question=m.get("question", "") or "",
            slug=m.get("slug", "") or "",
            liquidity=_to_float(m.get("liquidityNum", m.get("liquidity"))),
            volume=_to_float(m.get("volumeNum", m.get("volume"))),
            volume24hr=_to_float(m.get("volume24hr")),
            spread=_to_float(m.get("spread")),
            end_date=m.get("endDate", "") or "",
            active=bool(m.get("active", True)),
            closed=bool(m.get("closed", False)),
            event_id=event_id,
            tags=tags,
        )
        for tid in token_ids:
            index[str(tid)] = MarketMeta(token_id=str(tid), **meta_base)
    return index


# ---------------------------------------------------------------------------
# Disk-cached provider
# ---------------------------------------------------------------------------

class GammaCache:
    """
    Disk-backed token_id -> MarketMeta lookup.

    Build once (network), then read from RAM in the hot path. Designed so
    decision.py can be handed a GammaCache and call .lookup(token_id) with
    zero network cost, degrading gracefully (returns None) on a cache miss.
    """

    def __init__(self, path: str = "gamma_cache.json"):
        self.path = path
        self._index: dict = {}
        self.built_at: float = 0.0

    # ---- build / refresh ----
    def refresh(self, active: bool = True, closed: bool = False) -> int:
        """Fetch from Gamma and rebuild the index. Returns token count."""
        markets = fetch_markets(active=active, closed=closed)
        events = fetch_events(active=active, closed=closed)
        self._index = build_index(markets, events)
        self.built_at = time.time()
        return len(self._index)

    # ---- persistence ----
    def save(self) -> None:
        payload = {
            "built_at": self.built_at,
            "markets": {tid: asdict(meta) for tid, meta in self._index.items()},
        }
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, self.path)

    def load(self) -> bool:
        """Load index from disk. Returns False if file missing/corrupt."""
        if not os.path.exists(self.path):
            return False
        try:
            with open(self.path) as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        self.built_at = payload.get("built_at", 0.0)
        self._index = {
            tid: MarketMeta(**meta) for tid, meta in payload.get("markets", {}).items()
        }
        return True

    # ---- access ----
    def lookup(self, token_id: str) -> Optional[MarketMeta]:
        return self._index.get(str(token_id))

    def __len__(self) -> int:
        return len(self._index)

    def age_seconds(self) -> float:
        return time.time() - self.built_at if self.built_at else float("inf")


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Build the Gamma metadata cache")
    ap.add_argument("--output", default="gamma_cache.json")
    ap.add_argument("--include-closed", action="store_true",
                    help="Also include closed markets (default: active only)")
    args = ap.parse_args()

    cache = GammaCache(args.output)
    print("Fetching Gamma markets + events...")
    n = cache.refresh(active=True, closed=args.include_closed)
    cache.save()
    print(f"Indexed {n} token ids -> {args.output}")


if __name__ == "__main__":
    main()
