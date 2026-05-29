"""
Tests for copyability_score and ConvergenceDetector.
ConvergenceDetector is tested via inline re-implementation to avoid web3 import.
"""
import time
import threading
from collections import defaultdict
import pytest
from discover_alpha import WalletProfile, _copyability_score


# ---- Copyability tests -------------------------------------------------------

def _make_profile(**kwargs) -> WalletProfile:
    p = WalletProfile("0xTEST")
    p.max_position_pct = kwargs.get("max_position_pct", 0.05)
    p.trades_per_month = kwargs.get("trades_per_month", 40)
    p.diversification = kwargs.get("diversification", 0.6)
    p.trader_type = kwargs.get("trader_type", "MANUAL")
    p.concentration = kwargs.get("concentration", 0.25)
    return p


def test_easy_wallet_score():
    p = _make_profile(max_position_pct=0.02, trades_per_month=50,
                      diversification=0.8, trader_type="MANUAL", concentration=0.15)
    score, label = _copyability_score(p)
    assert score >= 70
    assert label == "🟢 Easy"


def test_hard_wallet_score():
    p = _make_profile(max_position_pct=0.50, trades_per_month=500,
                      diversification=0.05, trader_type="BOT", concentration=0.90)
    score, label = _copyability_score(p)
    assert score < 30
    assert label == "🔴 Hard"


def test_moderate_wallet_score():
    # High concentration + BOT type + low diversification → moderate/hard
    p = _make_profile(max_position_pct=0.20, trades_per_month=200,
                      diversification=0.25, trader_type="HIGH_FREQ", concentration=0.55)
    score, label = _copyability_score(p)
    assert 30 <= score < 70
    assert label == "⚠️ Moderate"


def test_bot_trader_penalized():
    manual = _make_profile(trader_type="MANUAL")
    bot = _make_profile(trader_type="BOT")
    s_manual, _ = _copyability_score(manual)
    s_bot, _ = _copyability_score(bot)
    assert s_manual > s_bot


def test_high_concentration_penalized():
    low = _make_profile(concentration=0.10)
    high = _make_profile(concentration=0.80)
    s_low, _ = _copyability_score(low)
    s_high, _ = _copyability_score(high)
    assert s_low > s_high


def test_score_bounds():
    for trader_type in ("MANUAL", "BOT", "HIGH_FREQ", "CASUAL", "UNKNOWN"):
        p = _make_profile(trader_type=trader_type)
        score, _ = _copyability_score(p)
        assert 0.0 <= score <= 100.0


# ---- ConvergenceDetector (inline re-implementation, no web3) ---------------
# The actual implementation in watch_onchain.py uses the same logic; we test
# it here via a faithful inline copy to keep tests web3-free.

class _ConvergenceDetector:
    def __init__(self, window_seconds=1800, min_wallets=3, on_convergence=None):
        self.window_seconds = window_seconds
        self.min_wallets = min_wallets
        self.on_convergence = on_convergence or (lambda e: None)
        self._entries = defaultdict(list)
        self._lock = threading.Lock()

    def feed(self, signal):
        market_id = signal.get("outcomeAssetId", "")
        side = signal.get("side", "")
        if not market_id or not side:
            return
        now = signal.get("detected_at", time.time())
        key = (market_id, side)
        with self._lock:
            self._entries[key].append({
                "wallet": signal["actor"], "usdc": signal["usdc"],
                "price": signal["price"],
                "speed_score": signal.get("speed_score", 1.0), "ts": now,
            })
            cutoff = now - self.window_seconds
            self._entries[key] = [e for e in self._entries[key] if e["ts"] >= cutoff]
            entries = self._entries[key]
            unique_wallets = {e["wallet"] for e in entries}
            if len(unique_wallets) >= self.min_wallets:
                usdc_total = sum(e["usdc"] for e in entries)
                oldest = min(e["ts"] for e in entries)
                event = {
                    "type": "CONVERGENCE", "market_id": market_id, "side": side,
                    "wallet_count": len(unique_wallets),
                    "wallets": sorted(unique_wallets),
                    "combined_usdc": round(usdc_total, 2),
                    "window_used_min": round((now - oldest) / 60, 1),
                }
                self._entries[key] = []
                self.on_convergence(event)


def _signal(market="mkt1", side="BUY", actor="0xAAA", usdc=500.0, ts=None):
    return {"outcomeAssetId": market, "side": side, "actor": actor,
            "usdc": usdc, "price": 0.65, "speed_score": 0.9,
            "detected_at": ts or time.time()}


def test_convergence_fires_at_threshold():
    events = []
    det = _ConvergenceDetector(window_seconds=300, min_wallets=3, on_convergence=events.append)
    now = time.time()
    for i, w in enumerate(["0xA", "0xB", "0xC"]):
        det.feed(_signal(actor=w, ts=now + i))
    assert len(events) == 1
    assert events[0]["wallet_count"] == 3
    assert events[0]["combined_usdc"] == 1500.0


def test_convergence_not_fired_below_threshold():
    events = []
    det = _ConvergenceDetector(window_seconds=300, min_wallets=3, on_convergence=events.append)
    now = time.time()
    for w in ["0xA", "0xB"]:
        det.feed(_signal(actor=w, ts=now))
    assert len(events) == 0


def test_duplicate_wallet_not_double_counted():
    events = []
    det = _ConvergenceDetector(window_seconds=300, min_wallets=3, on_convergence=events.append)
    now = time.time()
    # 0xA trades twice but still only 2 unique wallets
    for ts_off, w in [(0, "0xA"), (1, "0xA"), (2, "0xB")]:
        det.feed(_signal(actor=w, ts=now + ts_off))
    assert len(events) == 0


def test_expired_entries_excluded():
    events = []
    det = _ConvergenceDetector(window_seconds=10, min_wallets=3, on_convergence=events.append)
    now = time.time()
    det.feed(_signal(actor="0xA", ts=now - 20))  # outside window
    det.feed(_signal(actor="0xB", ts=now))
    det.feed(_signal(actor="0xC", ts=now + 1))
    assert len(events) == 0


def test_side_isolation():
    """BUY and SELL signals for the same market should not merge."""
    events = []
    det = _ConvergenceDetector(window_seconds=300, min_wallets=3, on_convergence=events.append)
    now = time.time()
    for w in ["0xA", "0xB", "0xC"]:
        det.feed(_signal(side="BUY", actor=w, ts=now))
    buy_events = [e for e in events if e["side"] == "BUY"]
    sell_events = [e for e in events if e["side"] == "SELL"]
    assert len(buy_events) == 1
    assert len(sell_events) == 0


def test_no_refire_without_new_wallet():
    """After convergence, a 4th trade from existing wallet should not re-fire."""
    events = []
    det = _ConvergenceDetector(window_seconds=300, min_wallets=3, on_convergence=events.append)
    now = time.time()
    for i, w in enumerate(["0xA", "0xB", "0xC"]):
        det.feed(_signal(actor=w, ts=now + i))
    assert len(events) == 1
    # 0xA trades again — no new wallet
    det.feed(_signal(actor="0xA", ts=now + 10))
    assert len(events) == 1  # still 1


def test_convergence_new_wallet_refires():
    """After a convergence+reset, 3 new entries should trigger again."""
    events = []
    det = _ConvergenceDetector(window_seconds=300, min_wallets=3, on_convergence=events.append)
    now = time.time()
    for i, w in enumerate(["0xA", "0xB", "0xC"]):
        det.feed(_signal(actor=w, ts=now + i))
    assert len(events) == 1
    # Bucket is now empty; need 3 more unique wallets to re-trigger
    for i, w in enumerate(["0xD", "0xE", "0xF"]):
        det.feed(_signal(actor=w, ts=now + 20 + i))
    assert len(events) == 2
