"""Flask dashboard for polymarket-btc-scalper."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

import config
from data import storage
from scanner import market_filter
from data import fetcher, parser
from utils.polymarket_links import wallet_link, market_link

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")

# Shared state for background scan results
_scan_lock = threading.Lock()
_scan_status: Dict[str, Any] = {"running": False, "last_run": None, "message": ""}


def _run_scan_background() -> None:
    """Run a full scan cycle in a background thread."""
    global _scan_status
    with _scan_lock:
        if _scan_status["running"]:
            return
        _scan_status["running"] = True
        _scan_status["message"] = "Scan started"

    try:
        # Import here to avoid circular imports at module level
        from main import run_scan_cycle
        run_scan_cycle()
        with _scan_lock:
            _scan_status["message"] = "Scan completed successfully"
    except Exception as exc:
        logger.exception("Background scan failed: %s", exc)
        with _scan_lock:
            _scan_status["message"] = f"Scan failed: {exc}"
    finally:
        import datetime
        with _scan_lock:
            _scan_status["running"] = False
            _scan_status["last_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/wallets")
def api_wallets():
    """Return all alpha wallets sorted by alpha_score descending."""
    wallets = storage.get_all_wallets()
    # Enrich with Polymarket profile links
    for w in wallets:
        w["profile_url"] = wallet_link(w.get("address", ""))
    return jsonify(wallets)


@app.route("/api/alerts")
def api_alerts():
    """Return last 50 convergence/new-alpha alerts."""
    alerts = storage.get_recent_alerts(limit=50)
    return jsonify(alerts)


@app.route("/api/markets")
def api_markets():
    """Return active BTC short-term markets with liquidity info."""
    raw_markets = fetcher.fetch_all_active_markets()
    all_markets = parser.parse_markets(raw_markets)
    btc_markets = market_filter.filter_btc_markets(all_markets)

    result = []
    for m in btc_markets:
        copy_ready = market_filter.is_liquid(m)
        result.append({
            "condition_id": m.condition_id,
            "question": m.question,
            "end_time": m.end_time,
            "volume": m.volume,
            "active": m.active,
            "resolved": m.resolved,
            "best_bid": m.best_bid,
            "best_ask": m.best_ask,
            "spread_pct": m.spread_pct,
            "copy_ready": copy_ready,
            "market_url": market_link(m.condition_id),
        })
    return jsonify(result)


@app.route("/api/heatmap")
def api_heatmap():
    """Return 7x24 alpha wallet activity heatmap data."""
    rows = storage.get_heatmap_data()
    # Build a flat grid for the frontend: list of {day, hour, count}
    grid = [[0] * 24 for _ in range(7)]
    for row in rows:
        d = int(row.get("dow", 0))
        h = int(row.get("hour", 0))
        c = int(row.get("cnt", 0))
        if 0 <= d < 7 and 0 <= h < 24:
            grid[d][h] = c
    return jsonify({"grid": grid, "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]})


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Trigger a manual scan cycle in the background."""
    with _scan_lock:
        if _scan_status["running"]:
            return jsonify({"status": "already_running", "message": "Scan already in progress"}), 409

    thread = threading.Thread(target=_run_scan_background, daemon=True)
    thread.start()
    return jsonify({"status": "started", "message": "Scan triggered"}), 202


@app.route("/api/status")
def api_status():
    with _scan_lock:
        return jsonify(dict(_scan_status))


def run_dashboard(host: str = config.DASHBOARD_HOST, port: int = config.DASHBOARD_PORT) -> None:
    """Start the Flask development server."""
    logger.info("Starting dashboard on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, threaded=True)
