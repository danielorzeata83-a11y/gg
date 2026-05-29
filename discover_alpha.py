#!/usr/bin/env python3
"""
Polymarket Alpha Wallet Discovery
==================================

Stage 1 of a copy-trading pipeline: find wallets worth following.

The public leaderboard ranks by raw PnL, which is dominated by luck and whales.
This script pulls a broad candidate pool from the leaderboard, then RE-RANKS each
candidate using skill metrics computed from their actual closed + open positions:

  - realized PnL (booked, not paper gains)
  - win rate across distinct markets
  - number of distinct markets traded (breadth -> guards against one-hit wonders)
  - average return per resolved position
  - share of profit concentrated in the single best market (concentration risk)
  - profit factor, max drawdown, consistency score, accuracy vs odds, YES bias,
    diversification, hold time, volume, and more.

A wallet is "alpha" only if it is profitable across MANY markets, consistently.

No authentication required. All endpoints are public Data API.
Docs: https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings
"""

import requests
import time
import json
import math
import statistics
import argparse
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from datetime import datetime

DATA_API = "https://data-api.polymarket.com"

# Rate limits (per 10s window): /positions = 150, /closed-positions = 150,
# leaderboard via general Data API = 1000. We stay well under with a small sleep.
REQUEST_PAUSE = 0.15

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "alpha-discovery/1.0"})


def _get(path, params=None, retries=4):
    """GET with basic backoff. Cloudflare throttles (queues) rather than 429s,
    but we still guard against transient errors."""
    url = f"{DATA_API}{path}"
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                time.sleep(REQUEST_PAUSE)
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return None


# ----------------------------------------------------------------------------
# Stage A: pull the candidate pool from the leaderboard
# ----------------------------------------------------------------------------

def fetch_leaderboard(category="OVERALL", time_period="MONTH", order_by="PNL",
                      target=200):
    """Page through /v1/leaderboard. Max 50 per page, offset up to 1000."""
    candidates = {}
    offset = 0
    while len(candidates) < target and offset <= 1000:
        page = _get("/v1/leaderboard", {
            "category": category,
            "timePeriod": time_period,
            "orderBy": order_by,
            "limit": 50,
            "offset": offset,
        })
        if not page:
            break
        for entry in page:
            wallet = entry.get("proxyWallet")
            if wallet and wallet not in candidates:
                candidates[wallet] = {
                    "proxyWallet": wallet,
                    "userName": entry.get("userName", ""),
                    "lb_pnl": entry.get("pnl", 0.0),
                    "lb_vol": entry.get("vol", 0.0),
                }
        if len(page) < 50:
            break
        offset += 50
    return list(candidates.values())


# ----------------------------------------------------------------------------
# Stage B: profile each candidate from their real positions
# ----------------------------------------------------------------------------

@dataclass
class WalletProfile:
    proxyWallet: str
    userName: str = ""
    lb_pnl: float = 0.0
    lb_vol: float = 0.0

    # --- 💰 Performance ---
    realized_pnl: float = 0.0          # sum of realizedPnl over resolved positions
    open_unrealized: float = 0.0       # sum of cashPnl on still-open positions
    roi_realized: float = 0.0          # realized_pnl / total_cost_basis * 100
    profit_factor: float = 0.0         # gross_wins / gross_losses (>1.5 = good)
    max_drawdown: float = 0.0          # max peak-to-trough in equity curve
    resolved_markets: int = 0          # distinct resolved markets
    open_markets: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_pct_return: float = 0.0        # mean % return per resolved position
    best_market_pnl: float = 0.0
    concentration: float = 0.0         # best market / total realized (0..1)

    # --- 🎯 Edge ---
    accuracy_vs_odds: float = 0.0      # win_rate - avg_entry_price (beating implied prob)
    avg_entry_price: float = 0.0       # avg price paid (proxy for implied prob)
    consistency_score: float = 0.0     # 1 - (stddev_roi / mean_roi) across markets

    # --- 🔄 Behavior ---
    total_volume_usdc: float = 0.0     # total USDC in + out
    avg_hold_days: float = 0.0         # avg days between first buy and resolve
    trades_per_day: float = 0.0        # frequency (positions / active days)
    maker_ratio: float = 0.0           # placeholder (not available in public API)

    # --- 🛡️ Risk ---
    diversification: float = 0.0       # unique_markets / total_positions
    max_position_pct: float = 0.0      # max single market / total capital
    exit_discipline: float = 0.0       # placeholder (not available in public API)

    # --- 🌍 Context ---
    yes_bias: float = 0.0              # (yes_trades - no_trades) / total
    avg_entry_timing_days: float = 0.0 # placeholder
    wallet_age_days: float = 0.0       # days since first Polymarket trade

    # --- Composite ---
    alpha_score: float = 0.0

    def to_row(self):
        return asdict(self)


def _fetch_all_closed(wallet, hard_cap=300):
    """Page through /closed-positions (max 50/page) up to hard_cap rows."""
    rows = []
    offset = 0
    while len(rows) < hard_cap:
        page = _get("/closed-positions", {
            "user": wallet,
            "limit": 50,
            "offset": offset,
            "sortBy": "REALIZEDPNL",
            "sortDirection": "DESC",
        })
        if not page:
            break
        rows.extend(page)
        if len(page) < 50:
            break
        offset += 50
    return rows


def _aggregate_closed(positions):
    """Collapse closed-position legs into per-market realized stats.
    Every row from /closed-positions is already resolved by definition."""
    by_market = defaultdict(lambda: {"realized": 0.0, "cost": 0.0})
    for p in positions:
        cid = p.get("conditionId")
        if not cid:
            continue
        m = by_market[cid]
        m["realized"] += p.get("realizedPnl", 0.0) or 0.0
        # cost basis ~ avgPrice * totalBought, used to derive a % return
        avg = p.get("avgPrice", 0.0) or 0.0
        bought = p.get("totalBought", 0.0) or 0.0
        m["cost"] += avg * bought
    return by_market


def _parse_dt(s):
    """Parse ISO datetime string, return datetime or None."""
    if not s:
        return None
    try:
        s = s.rstrip("Z")
        # truncate microseconds to 6 digits
        if "." in s:
            base, frac = s.split(".", 1)
            s = base + "." + frac[:6]
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f")
    except Exception:
        try:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def _compute_extended_metrics(prof, closed_positions, open_positions):
    """Compute all extended metrics from raw position data."""

    # Profit Factor
    gross_wins = sum(p.get("realizedPnl", 0) for p in closed_positions if (p.get("realizedPnl") or 0) > 0)
    gross_losses = abs(sum(p.get("realizedPnl", 0) for p in closed_positions if (p.get("realizedPnl") or 0) < 0))
    prof.profit_factor = round(gross_wins / gross_losses, 3) if gross_losses > 0 else 0.0

    # ROI Realized
    total_cost = sum((p.get("avgPrice") or 0) * (p.get("totalBought") or 0) for p in closed_positions)
    prof.roi_realized = round((prof.realized_pnl / total_cost * 100), 2) if total_cost > 0 else 0.0

    # Total volume
    prof.total_volume_usdc = round(
        sum((p.get("totalBought") or 0) + (p.get("totalSold") or 0) for p in closed_positions), 2)

    # Avg entry price (proxy for accuracy vs odds)
    prices = [p.get("avgPrice") or 0 for p in closed_positions if p.get("avgPrice")]
    prof.avg_entry_price = round(sum(prices) / len(prices), 4) if prices else 0.0

    # Accuracy vs odds: win_rate - avg_entry_price (positive = beating implied odds)
    prof.accuracy_vs_odds = round(prof.win_rate - prof.avg_entry_price, 4)

    # Max drawdown from per-position PnL sequence
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in closed_positions:
        equity += p.get("realizedPnl") or 0
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    prof.max_drawdown = round(max_dd, 4)

    # Consistency score: stddev of per-market ROI
    market_rois = []
    for p in closed_positions:
        cost = (p.get("avgPrice") or 0) * (p.get("totalBought") or 0)
        pnl = p.get("realizedPnl") or 0
        if cost > 0:
            market_rois.append(pnl / cost)
    if len(market_rois) >= 2:
        mean_roi = sum(market_rois) / len(market_rois)
        std_roi = statistics.stdev(market_rois)
        prof.consistency_score = round(1 - (std_roi / abs(mean_roi)), 3) if mean_roi != 0 else 0.0

    # YES/NO bias
    yes_trades = sum(1 for p in closed_positions if (p.get("outcome") or "").upper() == "YES")
    no_trades = sum(1 for p in closed_positions if (p.get("outcome") or "").upper() == "NO")
    total_trades = yes_trades + no_trades
    prof.yes_bias = round((yes_trades - no_trades) / total_trades, 3) if total_trades > 0 else 0.0

    # Diversification
    unique_markets = len({p.get("conditionId") for p in closed_positions if p.get("conditionId")})
    prof.diversification = round(unique_markets / len(closed_positions), 3) if closed_positions else 0.0

    # Max position as % of total capital
    market_costs = {}
    for p in closed_positions:
        cid = p.get("conditionId", "")
        cost = (p.get("avgPrice") or 0) * (p.get("totalBought") or 0)
        market_costs[cid] = market_costs.get(cid, 0) + cost
    total_cap = sum(market_costs.values())
    prof.max_position_pct = round(max(market_costs.values()) / total_cap, 3) if total_cap > 0 and market_costs else 0.0

    # Hold time (days) — using startDate/endDate if available
    hold_times = []
    for p in closed_positions:
        start = p.get("startDate") or p.get("createdAt") or ""
        end = p.get("endDate") or p.get("updatedAt") or ""
        s_dt = _parse_dt(start)
        e_dt = _parse_dt(end)
        if s_dt and e_dt and e_dt > s_dt:
            hold_times.append((e_dt - s_dt).days)
    prof.avg_hold_days = round(sum(hold_times) / len(hold_times), 1) if hold_times else 0.0

    # Wallet age: earliest startDate across all positions
    all_dates = []
    for p in closed_positions:
        dt = _parse_dt(p.get("startDate") or p.get("createdAt") or "")
        if dt:
            all_dates.append(dt)
    if all_dates:
        earliest = min(all_dates)
        prof.wallet_age_days = round((datetime.utcnow() - earliest).days, 0)

    # Trades per day (rough: positions / active days)
    if prof.wallet_age_days and prof.wallet_age_days > 0:
        prof.trades_per_day = round(len(closed_positions) / prof.wallet_age_days, 3)


def profile_wallet(cand):
    prof = WalletProfile(
        proxyWallet=cand["proxyWallet"],
        userName=cand.get("userName", ""),
        lb_pnl=cand.get("lb_pnl", 0.0),
        lb_vol=cand.get("lb_vol", 0.0),
    )

    # Closed (resolved) positions -> the real skill signal
    closed = _fetch_all_closed(cand["proxyWallet"])

    # Open positions -> paper PnL, informational only
    openp = _get("/positions", {
        "user": cand["proxyWallet"],
        "limit": 500,
        "sizeThreshold": 1,
    }) or []

    by_market = _aggregate_closed(closed)
    pct_returns = []
    market_pnls = []
    for cid, m in by_market.items():
        prof.resolved_markets += 1
        prof.realized_pnl += m["realized"]
        market_pnls.append(m["realized"])
        if m["realized"] > 0:
            prof.wins += 1
        elif m["realized"] < 0:
            prof.losses += 1
        if m["cost"] > 0:
            pct_returns.append(m["realized"] / m["cost"])

    prof.open_markets = len({p.get("conditionId") for p in openp if p.get("conditionId")})
    prof.open_unrealized = sum((p.get("cashPnl", 0.0) or 0.0) for p in openp)

    decided = prof.wins + prof.losses
    prof.win_rate = round((prof.wins / decided), 4) if decided else 0.0
    prof.avg_pct_return = round((sum(pct_returns) / len(pct_returns)), 4) if pct_returns else 0.0
    if market_pnls:
        prof.best_market_pnl = max(market_pnls)
        total_pos = sum(p for p in market_pnls if p > 0)
        prof.concentration = round((prof.best_market_pnl / total_pos), 4) if total_pos > 0 else 1.0

    # Compute extended metrics from raw position rows
    _compute_extended_metrics(prof, closed, openp)

    prof.alpha_score = compute_alpha_score(prof)
    return prof


def compute_alpha_score(p: WalletProfile) -> float:
    """
    Composite skill score. Designed to REWARD consistency and breadth, and
    PUNISH one-hit-wonders and pure whales.

    Components:
      + realized PnL (log-scaled)
      + win rate above 50%
      + breadth (distinct resolved markets)
      + profit factor (log-scaled)
      + consistency score
      + accuracy vs odds
      - concentration penalty
      - max drawdown penalty
    """
    if p.resolved_markets < MIN_MARKETS:
        return 0.0
    if p.realized_pnl <= 0:
        return 0.0

    pnl_term         = math.log10(p.realized_pnl + 10)           # 1..7
    winrate_term     = (p.win_rate - 0.5) * 4                    # -2..+2
    breadth_term     = math.log10(p.resolved_markets)            # 0.7..2.7
    conc_penalty     = p.concentration * 2.0                     # 0..2
    pf_term          = min(math.log10(p.profit_factor + 0.01) * 2, 2) if p.profit_factor > 0 else -1
    consistency_term = p.consistency_score * 1.5                 # -1.5..1.5
    dd_penalty       = p.max_drawdown * 2.0                      # 0..2
    accuracy_term    = p.accuracy_vs_odds * 3                    # -3..+3

    return round(
        pnl_term + winrate_term + breadth_term + pf_term
        + consistency_term + accuracy_term
        - conc_penalty - dd_penalty,
        3
    )


# Tunable thresholds
MIN_MARKETS = 10          # require at least this many resolved markets to qualify


def main():
    ap = argparse.ArgumentParser(description="Discover Polymarket alpha wallets")
    ap.add_argument("--category", default="OVERALL",
                    choices=["OVERALL", "POLITICS", "SPORTS", "CRYPTO", "CULTURE",
                             "MENTIONS", "WEATHER", "ECONOMICS", "TECH", "FINANCE"])
    ap.add_argument("--period", default="MONTH", choices=["DAY", "WEEK", "MONTH", "ALL"])
    ap.add_argument("--order", default="PNL", choices=["PNL", "VOL"])
    ap.add_argument("--pool", type=int, default=100, help="candidate pool size")
    ap.add_argument("--top", type=int, default=20, help="how many to keep")
    ap.add_argument("--min-markets", type=int, default=MIN_MARKETS)
    ap.add_argument("--out", default="alpha_wallets.json")
    args = ap.parse_args()

    globals()["MIN_MARKETS"] = args.min_markets

    print(f"[1/3] Pulling leaderboard pool ({args.category}/{args.period}/{args.order})...")
    candidates = fetch_leaderboard(args.category, args.period, args.order, args.pool)
    print(f"      -> {len(candidates)} candidates")

    print(f"[2/3] Profiling each from real positions (this respects rate limits)...")
    profiles = []
    for i, c in enumerate(candidates, 1):
        prof = profile_wallet(c)
        profiles.append(prof)
        tag = f"score={prof.alpha_score:>6}" if prof.alpha_score else "excluded"
        print(f"      [{i:>3}/{len(candidates)}] {c['proxyWallet'][:10]}... "
              f"{c.get('userName','')[:18]:<18} markets={prof.resolved_markets:>3} "
              f"winrate={prof.win_rate:.0%} realized=${prof.realized_pnl:>12,.0f} {tag}")

    qualified = [p for p in profiles if p.alpha_score > 0]
    qualified.sort(key=lambda p: p.alpha_score, reverse=True)
    top = qualified[:args.top]

    print(f"\n[3/3] {len(qualified)} wallets qualified; keeping top {len(top)}\n")
    print(f"{'RANK':<5}{'WALLET':<14}{'NAME':<18}{'SCORE':>7}{'WINRATE':>9}"
          f"{'MKTS':>6}{'REALIZED PNL':>16}{'CONC':>7}{'PF':>6}{'DD':>7}")
    print("-" * 95)
    for rank, p in enumerate(top, 1):
        print(f"{rank:<5}{p.proxyWallet[:12]:<14}{(p.userName or '-')[:16]:<18}"
              f"{p.alpha_score:>7}{p.win_rate:>8.0%}{p.resolved_markets:>6}"
              f"${p.realized_pnl:>14,.0f}{p.concentration:>7.2f}"
              f"{p.profit_factor:>6.2f}{p.max_drawdown:>7.2%}")

    with open(args.out, "w") as f:
        json.dump([p.to_row() for p in top], f, indent=2)
    print(f"\nSaved watchlist -> {args.out}")

    # Also write full candidate list for the HTML report (includes excluded wallets)
    report_path = args.out.replace(".json", "_report.json")
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "params": {
            "category": args.category,
            "period": args.period,
            "order_by": args.order,
            "pool_size": len(candidates),
            "min_markets": globals()["MIN_MARKETS"],
        },
        "wallets": [p.to_row() for p in sorted(profiles,
                    key=lambda x: x.alpha_score, reverse=True)],
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Saved full report  -> {report_path}")
    print(f"Open alpha_report.html in your browser to explore results.")
    print("Next stage: feed proxyWallet addresses into the position watcher.")


if __name__ == "__main__":
    main()
