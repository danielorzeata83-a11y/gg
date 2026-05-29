#!/usr/bin/env python3
"""
Grid Wallet Scanner
====================
Finds wallets that consistently trade in the 0.3-0.7 price range.
These are range traders / market makers who profit from volatility in uncertain markets.

Strategy: pull recent fills from the Data API, filter for trades where
price is between MIN_PRICE and MAX_PRICE, rank wallets by:
  - frequency (how many grid trades they made)
  - consistency (what % of their trades are in the grid zone)
  - volume (total USDC traded in zone)
  - recency (weighted toward recent activity)

Output: grid_wallets.json — fed into bot.py alongside alpha_wallets.json
"""
import requests, time, json, argparse, math
from collections import defaultdict
from dataclasses import dataclass, asdict

DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "grid-scanner/1.0"})

MIN_PRICE = 0.30
MAX_PRICE = 0.70

def _get(base, path, params=None, retries=4):
    url = f"{base}{path}"
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                time.sleep(0.15)
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return None

@dataclass
class GridWallet:
    proxyWallet: str
    grid_trades: int        # trades in the 0.3-0.7 zone
    total_trades: int
    grid_ratio: float       # grid_trades / total_trades
    total_grid_usdc: float  # total USDC volume in zone
    avg_price: float        # average price of grid trades
    last_trade_ts: float
    grid_score: float       # composite score

    def to_row(self):
        return asdict(self)

def fetch_recent_trades(token_id: str, limit: int = 100) -> list[dict]:
    """Fetch recent trades for a token from CLOB trades endpoint."""
    data = _get(CLOB_API, "/trades", {"token_id": token_id, "limit": limit})
    return data if isinstance(data, list) else []

def fetch_active_crypto_markets(limit: int = 50) -> list[dict]:
    """Get active crypto markets from Gamma API."""
    data = _get("https://gamma-api.polymarket.com", "/markets", {
        "tag": "crypto", "active": "true", "limit": limit,
        "order": "volume24hr", "ascending": "false",
    })
    return data if isinstance(data, list) else []

def scan_market_for_grid_wallets(market: dict) -> dict[str, list]:
    """Scan a single market's recent trades for grid activity."""
    # Get token IDs from market
    tokens = market.get("tokens") or []
    wallet_trades = defaultdict(list)

    for token in tokens[:2]:  # max 2 outcomes per market
        token_id = token.get("token_id") or token.get("tokenId", "")
        if not token_id:
            continue
        trades = fetch_recent_trades(token_id, limit=200)
        for t in trades:
            price = float(t.get("price", 0) or 0)
            if MIN_PRICE <= price <= MAX_PRICE:
                maker = t.get("maker", "") or ""
                taker = t.get("taker", "") or ""
                size = float(t.get("size", 0) or 0)
                ts = float(t.get("timestamp", time.time()) or time.time())
                for wallet in [maker, taker]:
                    if wallet and len(wallet) == 42:
                        wallet_trades[wallet].append({
                            "price": price, "size": size, "ts": ts,
                            "token_id": token_id,
                        })
    return wallet_trades

def score_wallet(wallet: str, grid_trades: list, all_trade_count: int) -> GridWallet:
    total_usdc = sum(t["size"] for t in grid_trades)
    avg_p = sum(t["price"] * t["size"] for t in grid_trades) / total_usdc if total_usdc else 0
    last_ts = max(t["ts"] for t in grid_trades)
    grid_ratio = len(grid_trades) / max(all_trade_count, 1)
    # Recency bonus: trades in last 24h count double
    now = time.time()
    recency = sum(2 if (now - t["ts"]) < 86400 else 1 for t in grid_trades)
    score = (
        math.log10(len(grid_trades) + 1) * 3
        + grid_ratio * 4
        + math.log10(total_usdc + 1)
        + recency * 0.1
    )
    return GridWallet(
        proxyWallet=wallet,
        grid_trades=len(grid_trades),
        total_trades=all_trade_count,
        grid_ratio=round(grid_ratio, 4),
        total_grid_usdc=round(total_usdc, 2),
        avg_price=round(avg_p, 4),
        last_trade_ts=last_ts,
        grid_score=round(score, 3),
    )

def main():
    ap = argparse.ArgumentParser(description="Scan for grid-range traders (0.3-0.7)")
    ap.add_argument("--markets", type=int, default=20, help="How many markets to scan")
    ap.add_argument("--top", type=int, default=30, help="Top wallets to keep")
    ap.add_argument("--min-trades", type=int, default=3, help="Min grid trades to qualify")
    ap.add_argument("--out", default="grid_wallets.json")
    ap.add_argument("--min-price", type=float, default=0.30)
    ap.add_argument("--max-price", type=float, default=0.70)
    args = ap.parse_args()

    global MIN_PRICE, MAX_PRICE
    MIN_PRICE, MAX_PRICE = args.min_price, args.max_price

    print(f"[1/3] Fetching top {args.markets} active crypto markets...")
    markets = fetch_active_crypto_markets(args.markets)
    print(f"      -> {len(markets)} markets found")

    print(f"[2/3] Scanning trades in price range {MIN_PRICE:.2f}–{MAX_PRICE:.2f}...")
    combined: dict[str, list] = defaultdict(list)
    for i, m in enumerate(markets, 1):
        q = (m.get("question") or m.get("title") or "")[:50]
        print(f"      [{i:>2}/{len(markets)}] {q}")
        wallet_trades = scan_market_for_grid_wallets(m)
        for w, trades in wallet_trades.items():
            combined[w].extend(trades)

    print(f"\n[3/3] Scoring {len(combined)} wallets...")
    wallets = []
    for wallet, trades in combined.items():
        if len(trades) >= args.min_trades:
            gw = score_wallet(wallet, trades, len(trades))
            wallets.append(gw)

    wallets.sort(key=lambda w: w.grid_score, reverse=True)
    top = wallets[:args.top]

    print(f"\n{'RANK':<5}{'WALLET':<14}{'GRID TRADES':>12}{'RATIO':>8}{'USDC VOL':>12}{'AVG PRICE':>11}{'SCORE':>8}")
    print("-" * 72)
    for rank, w in enumerate(top, 1):
        print(f"{rank:<5}{w.proxyWallet[:12]:<14}{w.grid_trades:>12}{w.grid_ratio:>8.0%}"
              f"{w.total_grid_usdc:>12,.0f}{w.avg_price:>11.4f}{w.grid_score:>8.3f}")

    with open(args.out, "w") as f:
        json.dump([w.to_row() for w in top], f, indent=2)
    print(f"\nSaved {len(top)} grid wallets -> {args.out}")
    print("Usage: python bot.py --mode paper --watchlist grid_wallets.json --bankroll 1000")

if __name__ == "__main__":
    main()
