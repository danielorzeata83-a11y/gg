#!/usr/bin/env python3
"""
Polymarket On-Chain Alpha Watcher
=================================

Stage 2: the fastest possible signal source. Subscribes to OrderFilled logs
emitted by Polymarket's CTF Exchange contracts on Polygon and fires a callback
the instant a watched ("alpha") wallet trades — before the Data API has indexed it.

Why on-chain beats polling / CLOB websocket:
  - Data API has indexing lag (seconds to tens of seconds).
  - CLOB websocket shows orderbook moves but isn't cleanly tied to a wallet address.
  - Raw contract logs arrive at block time (~2s on Polygon), tied directly to maker/taker.

Transport modes (in order of latency):
  1. eth_subscribe('logs') over WebSocket — fires at block-time (~2s), RECOMMENDED.
     Falls through automatically to mode 2 if WebSocket is unavailable.
  2. eth_getLogs polling — portable fallback; adds one poll-interval of lag.

Event (from Polymarket/ctf-exchange Trading.sol):
  OrderFilled(bytes32 orderHash, address maker, address taker,
              uint256 makerAssetId, uint256 takerAssetId,
              uint256 makerAmountFilled, uint256 takerAmountFilled, uint256 fee)
  - orderHash, maker, taker are INDEXED (topics 1..3); the rest are in data.
  - makerAssetId == 0  => order is a BUY (gives USDC, receives outcome tokens)
    makerAssetId != 0  => order is a SELL (gives outcome tokens, receives USDC)

Critical correctness notes (learned from Paradigm's double-counting writeup):
  - Each trade emits MULTIPLE OrderFilled events (one per maker + one taker-focused).
    We deduplicate per (txHash, orderHash) and ignore events where the counterparty
    is the exchange contract itself, to avoid reacting twice to one economic trade.
  - USDC has 6 decimals; outcome tokens have 18 decimals.

This script ONLY listens and prints/queues signals. It does NOT place orders.
Execution (Stage 3) is deliberately separate so you can paper-trade first.

Requires: pip install web3
A Polygon RPC WebSocket endpoint (Alchemy/Infura/QuickNode/your own node).
"""

import json
import time
import argparse
import threading
from web3 import Web3

# ---- Constants -------------------------------------------------------------

CTF_EXCHANGE = Web3.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_CTF_EXCHANGE = Web3.to_checksum_address("0xC5d563A36AE78145C45a50134d48A1215220f80a")
EXCHANGE_ADDRS = {CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE}

ORDER_FILLED_SIG = "OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)"
ORDER_FILLED_TOPIC = Web3.keccak(text=ORDER_FILLED_SIG).hex()
if not ORDER_FILLED_TOPIC.startswith("0x"):
    ORDER_FILLED_TOPIC = "0x" + ORDER_FILLED_TOPIC

USDC_DECIMALS = 6
TOKEN_DECIMALS = 18


# ---- Decoding --------------------------------------------------------------

def _topic_to_address(topic_hex):
    """An indexed address topic is a 32-byte word; address is the low 20 bytes."""
    h = topic_hex[2:] if topic_hex.startswith("0x") else topic_hex
    return Web3.to_checksum_address("0x" + h[-40:])


def decode_order_filled(log):
    """Decode an OrderFilled log into a structured dict.
    topics: [topic0, orderHash, maker, taker]
    data:   makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee
    """
    topics = log["topics"]
    # web3 may give HexBytes; normalize to hex strings
    topics = [t.hex() if hasattr(t, "hex") else t for t in topics]
    for i, t in enumerate(topics):
        if not t.startswith("0x"):
            topics[i] = "0x" + t

    order_hash = topics[1]
    maker = _topic_to_address(topics[2])
    taker = _topic_to_address(topics[3])

    data = log["data"]
    data = data.hex() if hasattr(data, "hex") else data
    if data.startswith("0x"):
        data = data[2:]
    words = [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]
    maker_asset_id, taker_asset_id, maker_amt, taker_amt, fee = words[:5]

    is_buy = (maker_asset_id == 0)  # maker gives USDC -> BUY of outcome token
    if is_buy:
        usdc_raw, token_raw = maker_amt, taker_amt
        outcome_asset = taker_asset_id
    else:
        usdc_raw, token_raw = taker_amt, maker_amt
        outcome_asset = maker_asset_id

    usdc = usdc_raw / (10 ** USDC_DECIMALS)
    tokens = token_raw / (10 ** TOKEN_DECIMALS)
    price = (usdc / tokens) if tokens > 0 else 0.0

    return {
        "orderHash": order_hash,
        "maker": maker,
        "taker": taker,
        "side": "BUY" if is_buy else "SELL",
        "outcomeAssetId": str(outcome_asset),
        "usdc": round(usdc, 4),
        "tokens": round(tokens, 4),
        "price": round(price, 4),
        "txHash": (log["transactionHash"].hex()
                   if hasattr(log["transactionHash"], "hex") else log["transactionHash"]),
        "block": log["blockNumber"],
        "contract": Web3.to_checksum_address(log["address"]),
    }


# ---- Speed Metrics ---------------------------------------------------------

class SpeedMetrics:
    """Tracks per-wallet execution latency (detected_at vs block timestamp).
    Used in decision layer as execution quality gate — NOT mixed into alpha_score.
    """

    def __init__(self):
        self._latencies: dict[str, list[float]] = {}  # wallet -> [ms, ...]

    def record(self, wallet: str, detected_at: float, block_ts: float):
        """Record latency in ms for a fill: time from block mining to our detection."""
        lag_ms = max(0.0, (detected_at - block_ts) * 1000)
        bucket = self._latencies.setdefault(wallet, [])
        bucket.append(lag_ms)
        if len(bucket) > 200:
            bucket.pop(0)

    def execution_latency_score(self, avg_latency_ms: float) -> float:
        """
        Score our pipeline speed: <500ms excellent, >3000ms poor.
        Used to decide if we can still copy without excess slippage.
        """
        if avg_latency_ms < 500:
            return 1.0
        elif avg_latency_ms < 1500:
            return 0.7
        elif avg_latency_ms < 3000:
            return 0.4
        else:
            return 0.1

    def wallet_stats(self, wallet: str) -> dict:
        lats = self._latencies.get(wallet, [])
        if not lats:
            return {"avg_latency_ms": 0.0, "speed_score": 1.0, "sample_count": 0}
        avg = sum(lats) / len(lats)
        return {
            "avg_latency_ms": round(avg, 1),
            "speed_score": self.execution_latency_score(avg),
            "sample_count": len(lats),
        }

    def all_stats(self) -> dict:
        return {w: self.wallet_stats(w) for w in self._latencies}


# ---- Watcher ---------------------------------------------------------------

class AlphaWatcher:
    def __init__(self, rpc_url, watched_wallets, on_signal=None):
        self.rpc_url = rpc_url
        self.w3 = Web3(Web3.LegacyWebSocketProvider(rpc_url)
                       if rpc_url.startswith("ws")
                       else Web3.HTTPProvider(rpc_url))
        # normalize watchlist to a set of checksummed addresses
        self.watched = {Web3.to_checksum_address(w) for w in watched_wallets}
        self.on_signal = on_signal or self._default_handler
        self._seen = set()  # (txHash, orderHash) dedupe keys
        self.speed = SpeedMetrics()

    def _default_handler(self, sig):
        ts = time.strftime("%H:%M:%S")
        spd = sig.get("speed_score", 1.0)
        spd_str = f"⚡{spd:.2f}" if spd >= 0.7 else f"🐢{spd:.2f}"
        print(f"[{ts}] ALPHA {sig['actor_role']} {sig['actor'][:10]}... "
              f"{sig['side']:<4} ${sig['usdc']:>10,.2f} @ {sig['price']:.3f} "
              f"{spd_str} lag={sig.get('detection_lag_ms',0):.0f}ms "
              f"tx={sig['txHash'][:12]}...")

    def _handle_log(self, log):
        detected_at = time.time()
        try:
            ev = decode_order_filled(log)
        except Exception as e:
            print(f"  decode error: {e}")
            return

        # Dedupe: one economic trade emits several OrderFilled logs.
        key = (ev["txHash"], ev["orderHash"])
        if key in self._seen:
            return

        # Which side is a watched wallet? Ignore the exchange-as-counterparty leg.
        hit_role = None
        actor = None
        if ev["maker"] in self.watched and ev["maker"] not in EXCHANGE_ADDRS:
            hit_role, actor = "maker", ev["maker"]
        elif ev["taker"] in self.watched and ev["taker"] not in EXCHANGE_ADDRS:
            hit_role, actor = "taker", ev["taker"]
        if not actor:
            return

        self._seen.add(key)
        # keep dedupe set bounded
        if len(self._seen) > 50000:
            self._seen = set(list(self._seen)[-10000:])

        # Record latency: compare detection time vs block timestamp
        try:
            blk = self.w3.eth.get_block(ev["block"])
            block_ts = blk.get("timestamp", detected_at)
        except Exception:
            block_ts = detected_at
        self.speed.record(actor, detected_at, block_ts)
        stats = self.speed.wallet_stats(actor)

        signal = {
            **ev,
            "actor": actor,
            "actor_role": hit_role,
            "detected_at": detected_at,
            "detection_lag_ms": round((detected_at - block_ts) * 1000, 1),
            "speed_score": stats["speed_score"],
        }
        self.on_signal(signal)

    # ---- WebSocket subscription (fast path) --------------------------------

    def _run_ws_subscription(self):
        """
        eth_subscribe('logs') via WebSocket — fires at block finalization (~2s on Polygon).
        ~2-5s faster than polling because we don't add the poll interval on top.
        Returns False if subscription fails so caller can fall back to polling.
        """
        if not self.rpc_url.startswith("ws"):
            return False

        try:
            sub_filter = {
                "address": [CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE],
                "topics": [ORDER_FILLED_TOPIC],
            }
            subscription_id = self.w3.eth.subscribe("logs", sub_filter)
            print(f"  WebSocket subscription active (id={subscription_id})")

            for event in self.w3.eth.get_filter_changes(subscription_id):
                self._handle_log(event)
        except Exception as e:
            print(f"  WebSocket subscription error: {e} — falling back to polling")
            return False
        return True

    # ---- HTTP polling (fallback) -------------------------------------------

    def _run_polling(self, poll_interval=2.0, from_block="latest"):
        """eth_getLogs polling — portable, works on any provider."""
        last_block = (self.w3.eth.block_number if from_block == "latest"
                      else int(from_block))
        flt = {
            "address": list(EXCHANGE_ADDRS),
            "topics": [ORDER_FILLED_TOPIC],
        }
        print(f"  Using polling fallback (interval={poll_interval}s)")
        while True:
            try:
                head = self.w3.eth.block_number
                if head >= last_block:
                    logs = self.w3.eth.get_logs({
                        **flt, "fromBlock": last_block, "toBlock": head})
                    for log in logs:
                        self._handle_log(log)
                    last_block = head + 1
            except Exception as e:
                print(f"  poll error (retrying): {e}")
                time.sleep(poll_interval * 2)
            time.sleep(poll_interval)

    # ---- Entry point -------------------------------------------------------

    def run(self, poll_interval=2.0, from_block="latest"):
        try:
            chain_id = self.w3.eth.chain_id
        except Exception as e:
            raise ConnectionError(f"Could not connect to Polygon RPC: {e}")
        if chain_id != 137:
            print(f"  WARNING: connected to chain {chain_id}, expected Polygon (137)")

        print(f"Watching {len(self.watched)} wallets across "
              f"{len(EXCHANGE_ADDRS)} exchange contracts.")
        print(f"OrderFilled topic: {ORDER_FILLED_TOPIC}")

        # Try WebSocket subscription first; fall back to polling if unavailable.
        if self.rpc_url.startswith("ws"):
            print("Attempting WebSocket log subscription (faster)...")
            success = self._run_ws_subscription()
            if success:
                return
            # subscription returned False — fall through to polling

        print("Listening for fills via polling... (Ctrl-C to stop)\n")
        self._run_polling(poll_interval, from_block)


def load_watchlist(path):
    with open(path) as f:
        data = json.load(f)
    # accepts output of discover_alpha.py (list of {proxyWallet,...})
    if data and isinstance(data[0], dict):
        return [d["proxyWallet"] for d in data if d.get("proxyWallet")]
    return data  # plain list of addresses


def main():
    ap = argparse.ArgumentParser(description="Watch alpha wallets on-chain (Polygon)")
    ap.add_argument("--rpc", required=True,
                    help="Polygon RPC URL (wss://... preferred for lowest latency)")
    ap.add_argument("--watchlist", default="alpha_wallets.json",
                    help="JSON from discover_alpha.py, or a JSON list of addresses")
    ap.add_argument("--poll", type=float, default=2.0,
                    help="Seconds between log polls (used only if WebSocket unavailable)")
    args = ap.parse_args()

    wallets = load_watchlist(args.watchlist)
    if not wallets:
        print("No wallets in watchlist. Run discover_alpha.py first.")
        return

    watcher = AlphaWatcher(args.rpc, wallets)
    watcher.run(poll_interval=args.poll)


if __name__ == "__main__":
    main()
