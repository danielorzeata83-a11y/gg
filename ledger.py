"""
Append-only trade ledger (JSON-lines).

Every decision and every fill — paper or live — gets written here.
The summary() method computes the metrics needed to pass the Phase 3 gate.
"""
import json
import time
import os
from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path


@dataclass
class LedgerEntry:
    timestamp: float
    mode: str                    # "paper" | "live"
    entry_type: str              # "decision" | "fill" | "resolution"
    wallet_copied: str
    token_id: str
    side: str                    # "BUY" | "SELL"
    alpha_price: float
    intended_price: float
    intended_size: float
    actual_fill_price: float = 0.0
    actual_fill_size: float = 0.0
    fee: float = 0.0
    slippage_vs_alpha: float = 0.0
    decision_approved: bool = False
    rejection_reason: str = ""
    resolved_pnl: Optional[float] = None
    market_question: str = ""
    extra: dict = None


class Ledger:
    def __init__(self, path: str = "ledger.jsonl"):
        self.path = path

    def record(self, entry: LedgerEntry) -> None:
        row = asdict(entry)
        row["extra"] = row.get("extra") or {}
        with open(self.path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def all_entries(self) -> list[dict]:
        if not Path(self.path).exists():
            return []
        entries = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    def fills(self, mode: str = None) -> list[dict]:
        return [e for e in self.all_entries()
                if e["entry_type"] == "fill"
                and (mode is None or e["mode"] == mode)]

    def resolved_fills(self, mode: str = None) -> list[dict]:
        return [e for e in self.fills(mode) if e.get("resolved_pnl") is not None]

    def summary(self, mode: str = None) -> dict:
        all_fills = self.fills(mode)
        resolved = self.resolved_fills(mode)
        decisions = [e for e in self.all_entries()
                     if e["entry_type"] == "decision"
                     and (mode is None or e["mode"] == mode)]

        approved = [d for d in decisions if d.get("decision_approved")]
        rejected = [d for d in decisions if not d.get("decision_approved")]

        wins = [r for r in resolved if (r.get("resolved_pnl") or 0) > 0]
        losses = [r for r in resolved if (r.get("resolved_pnl") or 0) < 0]
        total_pnl = sum(r.get("resolved_pnl") or 0 for r in resolved)

        slippages = [f.get("slippage_vs_alpha", 0) for f in all_fills]
        avg_slip = (sum(slippages) / len(slippages)) if slippages else 0.0

        return {
            "mode": mode or "all",
            "total_decisions": len(decisions),
            "approved": len(approved),
            "rejected": len(rejected),
            "hit_rate": (len(approved) / len(decisions)) if decisions else 0.0,
            "total_fills": len(all_fills),
            "resolved_fills": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(resolved)) if resolved else 0.0,
            "total_realized_pnl": round(total_pnl, 4),
            "avg_slippage_vs_alpha": round(avg_slip, 4),
        }

    def print_summary(self, mode: str = None) -> None:
        s = self.summary(mode)
        print(f"\n{'='*55}")
        print(f"  LEDGER SUMMARY  [{s['mode'].upper()} mode]")
        print(f"{'='*55}")
        print(f"  Decisions : {s['total_decisions']}  "
              f"(approved {s['approved']}, rejected {s['rejected']}, "
              f"hit rate {s['hit_rate']:.0%})")
        print(f"  Fills     : {s['total_fills']} total, {s['resolved_fills']} resolved")
        print(f"  Win rate  : {s['win_rate']:.0%}  "
              f"({s['wins']} wins / {s['losses']} losses)")
        print(f"  Realized PnL  : ${s['total_realized_pnl']:,.2f}")
        print(f"  Avg slippage vs alpha : {s['avg_slippage_vs_alpha']:.4f}")
        print(f"{'='*55}\n")

    def count_resolved_paper_trades(self) -> int:
        """Count paper fills AND paper resolution entries that have a resolved_pnl."""
        count = len(self.resolved_fills(mode="paper"))
        # Also count standalone resolution entries (not linked to a fill entry)
        extra = [e for e in self.all_entries()
                 if e.get("entry_type") == "resolution"
                 and e.get("mode") == "paper"
                 and e.get("resolved_pnl") is not None]
        # Avoid double-counting: if there are fill entries, resolutions are additive
        if count > 0:
            return count
        return len(extra)

    def daily_loss(self) -> float:
        """Sum of losses (negative PnL) from resolved fills/resolutions today."""
        today_start = time.mktime(time.strptime(
            time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        losses = 0.0
        for e in self.all_entries():
            if e.get("entry_type") not in ("fill", "resolution"):
                continue
            if e.get("resolved_pnl") is None:
                continue
            if e.get("timestamp", 0) >= today_start:
                pnl = e.get("resolved_pnl") or 0.0
                if pnl < 0:
                    losses += abs(pnl)
        return losses

    def open_exposure(self) -> float:
        """Approximate open exposure from unresolved fills."""
        exposure = 0.0
        for e in self.fills():
            if e.get("resolved_pnl") is None:
                exposure += e.get("actual_fill_size", 0) * e.get("actual_fill_price", 0)
        return exposure

    def open_position_count(self) -> int:
        return sum(1 for e in self.fills()
                   if e.get("resolved_pnl") is None)
