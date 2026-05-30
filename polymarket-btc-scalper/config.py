"""Central configuration for polymarket-btc-scalper."""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API endpoints ────────────────────────────────────────────────────────────
GAMMA_API_BASE   = "https://gamma-api.polymarket.com"
CLOB_API_BASE    = "https://clob.polymarket.com"
DATA_API_BASE    = "https://data-api.polymarket.com"

GAMMA_MARKETS_URL = f"{GAMMA_API_BASE}/markets"
CLOB_ORDERBOOK_URL = f"{CLOB_API_BASE}/book"
DATA_ACTIVITY_URL  = f"{DATA_API_BASE}/activity"
DATA_TRADES_URL    = f"{DATA_API_BASE}/trades"

# ── Wallet alpha criteria ────────────────────────────────────────────────────
MIN_WIN_RATE      = 0.65
MIN_TRADES        = 20
MIN_PROFIT_FACTOR = 1.5
MIN_NET_PNL       = 0.0
MIN_VOLUME        = 5_000.0

# ── Alpha score weights ──────────────────────────────────────────────────────
SCORE_WEIGHT_WIN_RATE      = 40
SCORE_WEIGHT_PROFIT_FACTOR = 20
SCORE_WEIGHT_SORTINO       = 20
SCORE_WEIGHT_CONSISTENCY   = 20

# ── Behavioral penalty multipliers (applied to score) ────────────────────────
PENALTY_MARTINGALE    = 0.30   # -30%
PENALTY_REVENGE       = 0.25   # -25%
PENALTY_FOMO          = 0.20   # -20%
PENALTY_CONCENTRATION = 0.15   # -15%
PENALTY_SYBIL         = 1.00   # -100% (exclude)

# ── Market filter thresholds ─────────────────────────────────────────────────
MIN_MARKET_VOLUME        = 1_000.0
BTC_KEYWORDS             = ["btc", "bitcoin"]
SHORT_TERM_WINDOW_MINUTES = 15
RECENTLY_RESOLVED_HOURS  = 24

# ── Convergence detector ─────────────────────────────────────────────────────
CONVERGENCE_WALLET_COUNT  = 3
CONVERGENCE_WINDOW_SECS   = 60

# ── Copy-readiness ───────────────────────────────────────────────────────────
MAX_SPREAD_PCT     = 0.05   # 5 %
ODDS_MOMENTUM_PCT  = 0.03   # 3 % in 5 min

# ── Scan settings ────────────────────────────────────────────────────────────
DEFAULT_SCAN_INTERVAL  = 300   # seconds
WALLET_TRADE_LIMIT     = 500
MARKET_TRADE_LIMIT     = 1_000

# ── Flask dashboard ──────────────────────────────────────────────────────────
DASHBOARD_PORT = 8081
DASHBOARD_HOST = "0.0.0.0"

# ── SQLite storage ───────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "btc_scalper.db")

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── HTTP retry ───────────────────────────────────────────────────────────────
HTTP_RETRIES    = 3
HTTP_BACKOFF    = 2   # seconds base for exponential back-off
HTTP_TIMEOUT    = 20  # seconds
