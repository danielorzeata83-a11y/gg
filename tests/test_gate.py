"""Tests for the Phase 3 -> 4 live gate."""
import time
import pytest
from unittest.mock import patch
from config import Config
from ledger import Ledger, LedgerEntry
from executor_live import check_live_gate


@pytest.fixture
def ledger_with_trades(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.jsonl"))
    for i in range(35):
        e = LedgerEntry(
            timestamp=time.time(),
            mode="paper", entry_type="resolution",
            wallet_copied="0xabc", token_id=str(i),
            side="BUY", alpha_price=0.5,
            intended_price=0.51, intended_size=10.0,
            actual_fill_price=0.51, actual_fill_size=10.0,
            resolved_pnl=1.0 if i % 2 == 0 else -0.5,
        )
        ledger.record(e)
    return ledger


def test_gate_fails_without_env(tmp_path):
    cfg = Config(bankroll_usdc=500.0)
    ledger = Ledger(str(tmp_path / "ledger.jsonl"))
    with patch.dict("os.environ", {}, clear=True):
        with patch("executor_live.requests.get") as mock_geo:
            mock_geo.return_value.status_code = 200
            mock_geo.return_value.json.return_value = {"blocked": False}
            failures = check_live_gate(cfg, ledger)
    assert any("I_UNDERSTAND_REAL_MONEY" in f for f in failures)


def test_gate_fails_without_paper_trades(tmp_path):
    cfg = Config(bankroll_usdc=500.0, min_paper_trades=30)
    ledger = Ledger(str(tmp_path / "empty.jsonl"))
    with patch.dict("os.environ", {"I_UNDERSTAND_REAL_MONEY": "yes"}):
        with patch("executor_live.requests.get") as mock_geo:
            mock_geo.return_value.status_code = 200
            mock_geo.return_value.json.return_value = {"blocked": False}
            failures = check_live_gate(cfg, ledger)
    assert any("paper trades" in f for f in failures)


def test_gate_passes_all_conditions(tmp_path, ledger_with_trades):
    cfg = Config(bankroll_usdc=500.0, min_paper_trades=30)
    with patch.dict("os.environ", {"I_UNDERSTAND_REAL_MONEY": "yes"}):
        with patch("executor_live.requests.get") as mock_geo:
            mock_geo.return_value.status_code = 200
            mock_geo.return_value.json.return_value = {"blocked": False}
            failures = check_live_gate(cfg, ledger_with_trades)
    assert failures == [], failures


def test_gate_fails_geoblocked(tmp_path, ledger_with_trades):
    cfg = Config(bankroll_usdc=500.0, min_paper_trades=30)
    with patch.dict("os.environ", {"I_UNDERSTAND_REAL_MONEY": "yes"}):
        with patch("executor_live.requests.get") as mock_geo:
            mock_geo.return_value.status_code = 200
            mock_geo.return_value.json.return_value = {"blocked": True}
            failures = check_live_gate(cfg, ledger_with_trades)
    assert any("geoblocked" in f for f in failures)
