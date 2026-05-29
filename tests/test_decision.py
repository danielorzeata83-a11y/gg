"""Unit tests for the decision layer."""
import time
import pytest
from unittest.mock import patch, MagicMock
from config import Config
from ledger import Ledger, LedgerEntry
from decision import DecisionEngine, TradeDecision


def make_signal(price=0.60, side="BUY", token="999", actor="0xABC"):
    return {
        "side": side,
        "outcomeAssetId": token,
        "price": price,
        "usdc": 50.0,
        "tokens": 83.3,
        "actor": actor,
        "actor_role": "maker",
        "txHash": "0xdeadbeef",
        "contract": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    }


def make_book(ask_price=0.61, ask_size=1000.0):
    return {
        "asks": [{"price": str(ask_price), "size": str(ask_size)}],
        "bids": [{"price": str(ask_price - 0.01), "size": str(ask_size)}],
    }


@pytest.fixture
def engine(tmp_path):
    cfg = Config(bankroll_usdc=1000.0, max_slippage=0.02)
    ledger = Ledger(str(tmp_path / "ledger.jsonl"))
    return DecisionEngine(cfg, ledger), cfg, ledger


def mock_book_and_tick(book, tick=0.01):
    def _mock_book(token_id):
        return book
    def _mock_tick(token_id):
        return tick
    return _mock_book, _mock_tick


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_approved(mock_tick, mock_book, engine):
    eng, cfg, ledger = engine
    mock_book.return_value = make_book(ask_price=0.61)
    mock_tick.return_value = 0.01
    sig = make_signal(price=0.60)
    d = eng.evaluate(sig)
    assert d.approved, d.reason
    assert d.slippage <= cfg.max_slippage


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_edge_rejected_slippage(mock_tick, mock_book, engine):
    eng, cfg, _ = engine
    # alpha at 0.60, book at 0.65 -> slippage = 0.05 > max 0.02
    mock_book.return_value = make_book(ask_price=0.65)
    mock_tick.return_value = 0.01
    sig = make_signal(price=0.60)
    d = eng.evaluate(sig)
    assert not d.approved
    assert "slippage" in d.reason


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_liquidity_rejected(mock_tick, mock_book, engine):
    eng, cfg, _ = engine
    # Only $0.01 of liquidity available (need ~$20 for 2% of 1000)
    mock_book.return_value = make_book(ask_price=0.61, ask_size=0.01)
    mock_tick.return_value = 0.01
    sig = make_signal(price=0.60)
    d = eng.evaluate(sig)
    assert not d.approved
    assert "liquidity" in d.reason


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_cooldown_rejected(mock_tick, mock_book, engine):
    eng, cfg, _ = engine
    mock_book.return_value = make_book(ask_price=0.61)
    mock_tick.return_value = 0.01
    sig = make_signal(price=0.60)
    # First trade goes through, second should be blocked by cooldown
    d1 = eng.evaluate(sig)
    assert d1.approved
    d2 = eng.evaluate(sig)
    assert not d2.approved
    assert "cooldown" in d2.reason


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_daily_loss_gate(mock_tick, mock_book, tmp_path, engine):
    eng, cfg, ledger = engine
    cfg.max_daily_loss_usdc = 10.0
    mock_book.return_value = make_book(ask_price=0.61)
    mock_tick.return_value = 0.01
    # Record a big resolved loss today
    entry = LedgerEntry(
        timestamp=time.time(),
        mode="paper", entry_type="resolution",
        wallet_copied="0xABC", token_id="999",
        side="BUY", alpha_price=0.6,
        intended_price=0.61, intended_size=20.0,
        actual_fill_price=0.61, actual_fill_size=20.0,
        resolved_pnl=-15.0,
    )
    ledger.record(entry)
    sig = make_signal(price=0.60)
    d = eng.evaluate(sig)
    assert not d.approved
    assert "daily loss" in d.reason


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_wide_spread_rejected(mock_tick, mock_book, engine):
    eng, cfg, _ = engine
    cfg.max_spread = 0.05
    # ask 0.61, bid 0.50 -> spread 0.11 > 0.05
    book = {
        "asks": [{"price": "0.61", "size": "1000"}],
        "bids": [{"price": "0.50", "size": "1000"}],
    }
    mock_book.return_value = book
    mock_tick.return_value = 0.01
    d = eng.evaluate(make_signal(price=0.60))
    assert not d.approved
    assert "spread" in d.reason
    assert d.spread == pytest.approx(0.11)


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_depth_aware_sizing_caps_position(mock_tick, mock_book, engine):
    eng, cfg, _ = engine
    cfg.depth_safety_fraction = 0.5
    cfg.min_position_usdc = 5.0
    # Book has ~$12.2 depth (20 tokens * 0.61). Desired 2% of 1000 = $20.
    # depth_safety 0.5 * 12.2 = $6.1 -> position capped to $6.1 (>= floor 5).
    book = {
        "asks": [{"price": "0.61", "size": "20"}],
        "bids": [{"price": "0.60", "size": "20"}],
    }
    mock_book.return_value = book
    mock_tick.return_value = 0.01
    d = eng.evaluate(make_signal(price=0.60))
    assert d.approved, d.reason
    assert d.size_usdc == pytest.approx(0.5 * 20 * 0.61)  # 6.1
    assert d.size_usdc < 20.0  # capped below desired


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_thin_book_below_floor_rejected(mock_tick, mock_book, engine):
    eng, cfg, _ = engine
    cfg.min_position_usdc = 5.0
    # Only 5 tokens * 0.61 = $3.05 depth; 0.5 fraction -> $1.5 < floor 5 -> reject
    book = {
        "asks": [{"price": "0.61", "size": "5"}],
        "bids": [{"price": "0.60", "size": "5"}],
    }
    mock_book.return_value = book
    mock_tick.return_value = 0.01
    d = eng.evaluate(make_signal(price=0.60))
    assert not d.approved
    assert "liquidity" in d.reason


@patch("decision.get_order_book")
@patch("decision.get_tick_size")
def test_no_bankroll_rejected(mock_tick, mock_book, engine):
    eng, cfg, _ = engine
    cfg.bankroll_usdc = 0.0
    mock_book.return_value = make_book(ask_price=0.61)
    mock_tick.return_value = 0.01
    sig = make_signal(price=0.60)
    d = eng.evaluate(sig)
    assert not d.approved
    assert "BANKROLL" in d.reason
