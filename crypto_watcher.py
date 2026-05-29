#!/usr/bin/env python3
"""
Crypto Market Watcher (15-min interval)
========================================
Polls Polymarket's crypto prediction markets every 15 minutes.
Detects significant price changes and emits trading signals.

Why 15 min: crypto markets on Polymarket move with the underlying asset.
A BTC pump often causes rapid re-pricing of outcome tokens — we want to
catch these moves before they stabilize.

Signals emitted when:
  - Price moves >= PRICE_MOVE_THRESHOLD since last check
  - New market enters the 0.3-0.7 "uncertain" zone (most tradeable)
  - Market approaches resolution with still-mispriced odds
"""
import time, requests, json, argparse, logging, threading
from dataclasses import dataclass, field
from pathlib import Path
from signal_log import write_signal

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "crypto-watcher/1.0"})

PRICE_MOVE_THRESHOLD = 0.03   # 3 cent move triggers a signal
GRID_LOW  = 0.30
GRID_HIGH = 0.70

def _get(base, path, params=None, retries=3):
    url = f"{base}{path}"
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return None

def fetch_crypto_markets(limit: int = 100) -> list[dict]:
    data = _get(GAMMA_API, "/markets", {
        "tag": "crypto", "active": "true", "limit": limit,
        "order": "volume24hr", "ascending": "false",
    })
    return data if isinstance(data, list) else []

def fetch_token_price(token_id: str, side: str = "buy") -> float | None:
    data = _get(CLOB_API, "/price", {"token_id": token_id, "side": side})
    if data and "price" in data:
        try:
            return float(data["price"])
        except (ValueError, TypeError):
            pass
    return None

@dataclass
class MarketSnapshot:
    condition_id: str
    question: str
    token_id: str       # outcome token (YES token)
    outcome: str
    price: float
    timestamp: float = field(default_factory=time.time)

class CryptoWatcher:
    def __init__(self, interval: int = 900, on_signal=None, signal_path: str = "signals.jsonl"):
        self.interval = interval
        self.on_signal = on_signal or self._default_handler
        self.signal_path = signal_path
        self._snapshots: dict[str, MarketSnapshot] = {}  # token_id -> last snapshot
        self._shutdown = threading.Event()

    def _default_handler(self, sig: dict) -> None:
        ts = time.strftime("%H:%M:%S")
        move = sig.get("price_move", 0)
        print(f"[{ts}] CRYPTO SIGNAL  {sig['side']:<4}  {sig['question'][:40]:<40}"
              f"  price={sig['price']:.3f}  move={move:+.3f}  token={sig['outcomeAssetId'][:16]}...")

    def _check_markets(self) -> None:
        markets = fetch_crypto_markets(50)
        logger.info("Crypto watcher: checking %d markets", len(markets))

        for m in markets:
            tokens = m.get("tokens") or []
            question = m.get("question") or m.get("title") or ""
            cid = m.get("conditionId") or m.get("id") or ""

            for token in tokens:
                token_id = token.get("token_id") or token.get("tokenId") or ""
                outcome = token.get("outcome") or ""
                if not token_id:
                    continue

                price = fetch_token_price(token_id)
                if price is None:
                    continue

                snap_key = token_id
                prev = self._snapshots.get(snap_key)
                now_snap = MarketSnapshot(cid, question, token_id, outcome, price)
                self._snapshots[snap_key] = now_snap

                if prev is None:
                    continue  # first observation, no signal yet

                move = price - prev.price
                in_grid = GRID_LOW <= price <= GRID_HIGH
                big_move = abs(move) >= PRICE_MOVE_THRESHOLD
                entered_grid = (not (GRID_LOW <= prev.price <= GRID_HIGH)) and in_grid

                if big_move or entered_grid:
                    side = "BUY" if move > 0 else "SELL"
                    reason = "price_move" if big_move else "entered_grid"
                    signal = {
                        "side": side,
                        "outcomeAssetId": token_id,
                        "price": round(price, 4),
                        "usdc": 0,      # no known fill size — watcher only
                        "tokens": 0,
                        "actor": "crypto_watcher",
                        "actor_role": "watcher",
                        "txHash": "",
                        "block": 0,
                        "contract": "",
                        "question": question,
                        "outcome": outcome,
                        "price_move": round(move, 4),
                        "prev_price": round(prev.price, 4),
                        "in_grid": in_grid,
                        "reason": reason,
                        "detected_at": time.time(),
                    }
                    logger.info("CRYPTO SIGNAL: %s %s %.4f (move %+.4f) [%s]",
                                side, question[:30], price, move, reason)
                    try:
                        write_signal(signal, {"approved": False, "reason": "crypto_watcher_signal"}, self.signal_path)
                    except Exception as e:
                        logger.warning("Could not write signal: %s", e)
                    self.on_signal(signal)

    def run(self) -> None:
        print(f"Crypto watcher started — checking every {self.interval//60} min")
        print(f"Price move threshold: {PRICE_MOVE_THRESHOLD*100:.0f}c  Grid zone: {GRID_LOW}–{GRID_HIGH}\n")
        while not self._shutdown.is_set():
            try:
                self._check_markets()
            except Exception as e:
                logger.error("Crypto watcher error: %s", e)
            self._shutdown.wait(self.interval)

    def stop(self) -> None:
        self._shutdown.set()

def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Watch Polymarket crypto markets for price moves")
    ap.add_argument("--interval", type=int, default=900, help="Check interval in seconds (default 900 = 15min)")
    ap.add_argument("--threshold", type=float, default=PRICE_MOVE_THRESHOLD,
                    help="Min price move to trigger signal (default 0.03)")
    ap.add_argument("--signals", default="signals.jsonl")
    args = ap.parse_args()

    global PRICE_MOVE_THRESHOLD
    PRICE_MOVE_THRESHOLD = args.threshold

    watcher = CryptoWatcher(interval=args.interval, signal_path=args.signals)
    import signal as _signal
    _signal.signal(_signal.SIGINT, lambda *_: (watcher.stop(), exit(0)))
    _signal.signal(_signal.SIGTERM, lambda *_: (watcher.stop(), exit(0)))
    watcher.run()

if __name__ == "__main__":
    main()
