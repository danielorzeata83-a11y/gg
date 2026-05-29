"""
Lightweight Flask API server for the dashboard.
Run alongside bot.py: python api_server.py --ledger ledger.jsonl
"""
import json
import time
import argparse
from pathlib import Path
from flask import Flask, jsonify, send_from_directory
from signal_log import read_signals

app = Flask(__name__, static_folder=".")

LEDGER_PATH = "ledger.jsonl"
WATCHLIST_PATH = "alpha_wallets.json"
SIGNALS_PATH = "signals.jsonl"


def read_ledger():
    path = Path(LEDGER_PATH)
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


@app.route("/api/status")
def status():
    entries = read_ledger()
    fills = [e for e in entries if e["entry_type"] == "fill"]
    resolutions = [e for e in entries if e["entry_type"] == "resolution"]
    decisions = [e for e in entries if e["entry_type"] == "decision"]

    approved = [d for d in decisions if d.get("decision_approved")]
    rejected = [d for d in decisions if not d.get("decision_approved")]

    wins = [r for r in resolutions if (r.get("resolved_pnl") or 0) > 0]
    losses = [r for r in resolutions if (r.get("resolved_pnl") or 0) < 0]
    total_pnl = sum(r.get("resolved_pnl") or 0 for r in resolutions)

    slippages = [f.get("slippage_vs_alpha", 0) for f in fills]
    avg_slip = (sum(slippages) / len(slippages)) if slippages else 0.0

    # PnL over time
    pnl_series = []
    running = 0.0
    for r in sorted(resolutions, key=lambda x: x.get("timestamp", 0)):
        running += r.get("resolved_pnl") or 0
        pnl_series.append({"t": r.get("timestamp", 0), "pnl": round(running, 4)})

    # Recent fills
    recent = sorted(fills, key=lambda x: x.get("timestamp", 0), reverse=True)[:20]

    # Open positions (fills without a matching resolution)
    resolved_keys = {(r["token_id"], r["wallet_copied"]) for r in resolutions}
    open_positions = [f for f in fills
                      if (f["token_id"], f.get("wallet_copied", "")) not in resolved_keys]

    return jsonify({
        "summary": {
            "total_decisions": len(decisions),
            "approved": len(approved),
            "rejected": len(rejected),
            "hit_rate": (len(approved) / len(decisions)) if decisions else 0.0,
            "total_fills": len(fills),
            "resolved": len(resolutions),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(resolutions)) if resolutions else 0.0,
            "total_pnl": round(total_pnl, 2),
            "avg_slippage": round(avg_slip, 4),
            "open_positions": len(open_positions),
        },
        "pnl_series": pnl_series,
        "recent_fills": recent,
        "open_positions": open_positions,
        "updated_at": time.time(),
    })


@app.route("/api/signals")
def signals():
    """Live signal feed — what alpha wallets traded + bot decision."""
    rows = read_signals(SIGNALS_PATH, limit=100)
    rows.reverse()  # newest first
    return jsonify({
        "signals": rows,
        "count": len(rows),
        "updated_at": time.time(),
    })


@app.route("/api/watchlist")
def watchlist():
    """Return the current alpha watchlist."""
    path = Path(WATCHLIST_PATH)
    if not path.exists():
        return jsonify({"wallets": [], "count": 0})
    try:
        with open(path) as f:
            data = json.load(f)
        return jsonify({"wallets": data, "count": len(data)})
    except Exception:
        return jsonify({"wallets": [], "count": 0})


@app.route("/")
def index():
    return send_from_directory(".", "dashboard.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)


def main():
    global LEDGER_PATH, WATCHLIST_PATH, SIGNALS_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="ledger.jsonl")
    ap.add_argument("--watchlist", default="alpha_wallets.json")
    ap.add_argument("--signals", default="signals.jsonl")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    LEDGER_PATH = args.ledger
    WATCHLIST_PATH = args.watchlist
    SIGNALS_PATH = args.signals
    print(f"Dashboard at http://localhost:{args.port}")
    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
