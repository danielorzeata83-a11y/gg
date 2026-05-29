"""
Lightweight Flask API server for the dashboard.
Run alongside bot.py: python api_server.py --ledger ledger.jsonl
"""
import json
import time
import os
import functools
import argparse
from pathlib import Path
from flask import (Flask, jsonify, send_from_directory, session,
                   request, redirect, url_for)
from signal_log import read_signals

app = Flask(__name__, static_folder=".")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")

LEDGER_PATH = "ledger.jsonl"
WATCHLIST_PATH = "alpha_wallets.json"
SIGNALS_PATH = "signals.jsonl"

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "")

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Polybot — Login</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    display:flex;align-items:center;justify-content:center;min-height:100vh}
  .card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px 36px;width:100%;max-width:380px}
  h1{font-size:22px;margin-bottom:28px;text-align:center;color:#58a6ff}
  h1 span{font-size:28px;display:block;margin-bottom:6px}
  label{display:block;font-size:13px;color:#8b949e;margin-bottom:6px}
  input{width:100%;background:#21262d;border:1px solid #30363d;color:#e6edf3;
    border-radius:6px;padding:10px 14px;font-size:14px;outline:none;margin-bottom:16px}
  input:focus{border-color:#58a6ff}
  .row{display:flex;align-items:center;gap:8px;margin-bottom:20px;font-size:13px;color:#8b949e}
  button{width:100%;background:#238636;color:#fff;border:none;border-radius:6px;
    padding:11px;font-size:15px;font-weight:600;cursor:pointer}
  button:hover{background:#2ea043}
  .err{color:#f85149;font-size:13px;text-align:center;margin-top:14px}
</style>
</head>
<body>
<div class="card">
  <h1><span>📊</span>Polybot Dashboard</h1>
  <form method="POST" action="/login">
    <label>Username</label>
    <input type="text" name="username" autocomplete="username" required>
    <label>Password</label>
    <input type="password" name="password" autocomplete="current-password" required>
    <div class="row"><input type="checkbox" name="remember" id="rem" style="width:auto;margin:0">
      <label for="rem" style="margin:0;cursor:pointer">Remember me (30 days)</label></div>
    <button type="submit">Sign in</button>
    {error}
  </form>
</div>
</body></html>"""


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == DASHBOARD_USER and p == DASHBOARD_PASS and DASHBOARD_PASS:
            session.permanent = bool(request.form.get("remember"))
            if session.permanent:
                app.permanent_session_lifetime = __import__("datetime").timedelta(days=30)
            session["authenticated"] = True
            return redirect(url_for("index"))
        return LOGIN_HTML.replace("{error}", '<p class="err">Invalid credentials</p>'), 401
    return LOGIN_HTML.replace("{error}", "")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# API routes
# --------------------------------------------------------------------------

@app.route("/api/status")
@login_required
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

    pnl_series = []
    running = 0.0
    for r in sorted(resolutions, key=lambda x: x.get("timestamp", 0)):
        running += r.get("resolved_pnl") or 0
        pnl_series.append({"t": r.get("timestamp", 0), "pnl": round(running, 4)})

    recent = sorted(fills, key=lambda x: x.get("timestamp", 0), reverse=True)[:20]

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
@login_required
def signals():
    rows = read_signals(SIGNALS_PATH, limit=100)
    rows.reverse()
    return jsonify({
        "signals": rows,
        "count": len(rows),
        "updated_at": time.time(),
    })


@app.route("/api/watchlist")
@login_required
def watchlist():
    path = Path(WATCHLIST_PATH)
    if not path.exists():
        return jsonify({"wallets": [], "count": 0})
    try:
        with open(path) as f:
            data = json.load(f)
        return jsonify({"wallets": data, "count": len(data)})
    except Exception:
        return jsonify({"wallets": [], "count": 0})


@app.route("/api/wallets")
@login_required
def wallets():
    path = Path(WATCHLIST_PATH)
    # fall back to full report if available
    report_path = Path(WATCHLIST_PATH.replace(".json", "_report.json"))
    source = report_path if report_path.exists() else path
    if not source.exists():
        return jsonify({"wallets": [], "count": 0})
    try:
        raw = json.loads(source.read_text())
        # report JSON wraps wallets in {"wallets": [...]}
        if isinstance(raw, dict):
            raw = raw.get("wallets", [])
        fields = ["proxyWallet", "userName", "alpha_score", "win_rate",
                  "realized_pnl", "resolved_markets", "sortino_ratio",
                  "consistency_score", "fomo_flag", "martingale_flag",
                  "revenge_flag", "sybil_risk"]
        slim = [{k: w.get(k) for k in fields} for w in raw]
        slim.sort(key=lambda x: (x.get("alpha_score") or 0), reverse=True)
        return jsonify({"wallets": slim, "count": len(slim)})
    except Exception:
        return jsonify({"wallets": [], "count": 0})


@app.route("/api/alerts")
@login_required
def alerts():
    rows = read_signals(SIGNALS_PATH, limit=500)
    flagged = [r for r in rows
               if not r.get("decision_approved")
               or abs(r.get("slippage_vs_alpha") or 0) > 0.02]
    flagged.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return jsonify({
        "alerts": flagged[:50],
        "count": len(flagged[:50]),
        "updated_at": time.time(),
    })


@app.route("/")
@login_required
def index():
    return send_from_directory(".", "dashboard.html")


@app.route("/<path:filename>")
@login_required
def static_files(filename):
    return send_from_directory(".", filename)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

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
