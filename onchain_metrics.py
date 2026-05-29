"""
On-chain wallet verification via Polygonscan API + slippage proxy.

Polygonscan free tier: 5 calls/sec, 100k/day with a free API key.
Set POLYGONSCAN_API_KEY in .env. If absent, these metrics return zeros
and the rest of discovery continues normally (graceful degradation).
"""
import os
import time
import requests

POLYGONSCAN_API = "https://api.polygonscan.com/api"
_session = requests.Session()
_session.headers.update({"User-Agent": "polybot-onchain/1.0"})

# Known Polygon DeFi/protocol contract prefixes for pre-Polymarket activity check
# (a small illustrative set; quadratic detection not needed)
KNOWN_DEFI = {
    "0x1f98431c8ad98523631ae4a59f267346ea31f984",  # Uniswap V3 factory
    "0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff",  # QuickSwap router
    "0x8dff5e27ea6b7ac08ebfdf9eb090f32ee9a30fcf",  # Aave
}

POLYMARKET_FIRST_BLOCK_TS = 1633046400  # ~Oct 2021, Polymarket on Polygon era start


def _get(params, retries=3):
    api_key = os.getenv("POLYGONSCAN_API_KEY", "")
    if not api_key:
        return None
    params = {**params, "apikey": api_key}
    for attempt in range(retries):
        try:
            r = _session.get(POLYGONSCAN_API, params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                time.sleep(0.22)  # stay under 5/sec
                return data
            time.sleep(1.0 * (attempt + 1))
        except requests.RequestException:
            time.sleep(1.0 * (attempt + 1))
    return None


def wallet_onchain_profile(address: str) -> dict:
    """Return {age_days, tx_count, pre_polymarket_activity}. Zeros if no API key."""
    result = {"age_days": 0.0, "tx_count": 0, "pre_polymarket_activity": False}
    # First transaction (oldest) — sort asc, get 1
    data = _get({
        "module": "account", "action": "txlist", "address": address,
        "startblock": 0, "endblock": 99999999, "page": 1, "offset": 1,
        "sort": "asc",
    })
    if not data or data.get("status") != "1" or not data.get("result"):
        return result
    first_tx = data["result"][0]
    first_ts = int(first_tx.get("timeStamp", 0))
    if first_ts > 0:
        result["age_days"] = round((time.time() - first_ts) / 86400, 1)
        # if first tx well before typical Polymarket usage -> pre-existing wallet
        result["pre_polymarket_activity"] = first_ts < (POLYMARKET_FIRST_BLOCK_TS + 90*86400)

    # tx count
    cnt = _get({
        "module": "proxy", "action": "eth_getTransactionCount",
        "address": address, "tag": "latest",
    })
    if cnt and cnt.get("result"):
        try:
            result["tx_count"] = int(cnt["result"], 16)
        except (ValueError, TypeError):
            pass
    return result


def compute_sybil_risk(profiles) -> None:
    """Pool-level heuristic: flag wallets whose traded markets overlap suspiciously
    with another wallet's markets (potential coordinated/wash farms).
    Sets prof.sybil_risk in 0..1. Requires each profile to carry a `_market_set`.
    """
    n = len(profiles)
    for i, a in enumerate(profiles):
        a_markets = getattr(a, "_market_set", set())
        if not a_markets:
            continue
        max_overlap = 0.0
        for j, b in enumerate(profiles):
            if i == j:
                continue
            b_markets = getattr(b, "_market_set", set())
            if not b_markets:
                continue
            inter = len(a_markets & b_markets)
            union = len(a_markets | b_markets)
            jaccard = inter / union if union else 0
            if jaccard > max_overlap:
                max_overlap = jaccard
        # high jaccard overlap with another wallet is the risk signal
        a.sybil_risk = round(max_overlap, 3)
