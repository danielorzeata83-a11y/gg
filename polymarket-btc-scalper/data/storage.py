"""SQLite persistence layer."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

import config
from models import Alert, Market, Trade, WalletMetrics

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS wallets (
    address              TEXT PRIMARY KEY,
    username             TEXT DEFAULT '',
    total_trades         INTEGER DEFAULT 0,
    win_rate             REAL DEFAULT 0,
    total_volume         REAL DEFAULT 0,
    gross_wins           REAL DEFAULT 0,
    gross_losses         REAL DEFAULT 0,
    net_pnl              REAL DEFAULT 0,
    profit_factor        REAL DEFAULT 0,
    sortino_ratio        REAL DEFAULT 0,
    consistency_score    REAL DEFAULT 0,
    clv                  REAL DEFAULT 0,
    brier_score          REAL DEFAULT 1,
    reaction_time_median REAL DEFAULT 0,
    flag_martingale      INTEGER DEFAULT 0,
    flag_revenge         INTEGER DEFAULT 0,
    flag_fomo            INTEGER DEFAULT 0,
    flag_concentration   INTEGER DEFAULT 0,
    flag_sybil           INTEGER DEFAULT 0,
    alpha_score          REAL DEFAULT 0,
    last_updated         TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS trades (
    id         TEXT PRIMARY KEY,
    wallet     TEXT NOT NULL,
    market_id  TEXT NOT NULL,
    side       TEXT NOT NULL,
    price      REAL DEFAULT 0,
    size       REAL DEFAULT 0,
    timestamp  INTEGER DEFAULT 0,
    outcome    TEXT,
    pnl        REAL
);

CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    question     TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    volume       REAL DEFAULT 0,
    resolved     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL,
    type       TEXT NOT NULL,
    wallet     TEXT NOT NULL,
    market_id  TEXT NOT NULL,
    message    TEXT NOT NULL
);
"""


# ── Connection management ─────────────────────────────────────────────────────

def get_connection(db_path: str = config.DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_cursor(db_path: str = config.DB_PATH) -> Generator[sqlite3.Cursor, None, None]:
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = config.DB_PATH) -> None:
    """Create tables if they do not exist."""
    conn = get_connection(db_path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    logger.info("Database initialised at %s", db_path)


# ── Wallets ───────────────────────────────────────────────────────────────────

def upsert_wallet(wm: WalletMetrics, db_path: str = config.DB_PATH) -> None:
    sql = """
    INSERT INTO wallets (
        address, username, total_trades, win_rate, total_volume,
        gross_wins, gross_losses, net_pnl, profit_factor,
        sortino_ratio, consistency_score, clv, brier_score,
        reaction_time_median,
        flag_martingale, flag_revenge, flag_fomo, flag_concentration, flag_sybil,
        alpha_score, last_updated
    ) VALUES (
        :address, :username, :total_trades, :win_rate, :total_volume,
        :gross_wins, :gross_losses, :net_pnl, :profit_factor,
        :sortino_ratio, :consistency_score, :clv, :brier_score,
        :reaction_time_median,
        :flag_martingale, :flag_revenge, :flag_fomo, :flag_concentration, :flag_sybil,
        :alpha_score, :last_updated
    )
    ON CONFLICT(address) DO UPDATE SET
        username=excluded.username,
        total_trades=excluded.total_trades,
        win_rate=excluded.win_rate,
        total_volume=excluded.total_volume,
        gross_wins=excluded.gross_wins,
        gross_losses=excluded.gross_losses,
        net_pnl=excluded.net_pnl,
        profit_factor=excluded.profit_factor,
        sortino_ratio=excluded.sortino_ratio,
        consistency_score=excluded.consistency_score,
        clv=excluded.clv,
        brier_score=excluded.brier_score,
        reaction_time_median=excluded.reaction_time_median,
        flag_martingale=excluded.flag_martingale,
        flag_revenge=excluded.flag_revenge,
        flag_fomo=excluded.flag_fomo,
        flag_concentration=excluded.flag_concentration,
        flag_sybil=excluded.flag_sybil,
        alpha_score=excluded.alpha_score,
        last_updated=excluded.last_updated
    """
    with db_cursor(db_path) as cur:
        cur.execute(sql, {
            "address": wm.address,
            "username": wm.username,
            "total_trades": wm.total_trades,
            "win_rate": wm.win_rate,
            "total_volume": wm.total_volume,
            "gross_wins": wm.gross_wins,
            "gross_losses": wm.gross_losses,
            "net_pnl": wm.net_pnl,
            "profit_factor": wm.profit_factor,
            "sortino_ratio": wm.sortino_ratio,
            "consistency_score": wm.consistency_score,
            "clv": wm.clv,
            "brier_score": wm.brier_score,
            "reaction_time_median": wm.reaction_time_median,
            "flag_martingale": int(wm.flag_martingale),
            "flag_revenge": int(wm.flag_revenge),
            "flag_fomo": int(wm.flag_fomo),
            "flag_concentration": int(wm.flag_concentration),
            "flag_sybil": int(wm.flag_sybil),
            "alpha_score": wm.alpha_score,
            "last_updated": wm.last_updated,
        })


def get_all_wallets(db_path: str = config.DB_PATH) -> List[Dict[str, Any]]:
    with db_cursor(db_path) as cur:
        cur.execute("SELECT * FROM wallets ORDER BY alpha_score DESC")
        return [dict(row) for row in cur.fetchall()]


def get_wallet(address: str, db_path: str = config.DB_PATH) -> Optional[Dict[str, Any]]:
    with db_cursor(db_path) as cur:
        cur.execute("SELECT * FROM wallets WHERE address=?", (address,))
        row = cur.fetchone()
        return dict(row) if row else None


# ── Trades ────────────────────────────────────────────────────────────────────

def upsert_trade(trade: Trade, db_path: str = config.DB_PATH) -> None:
    sql = """
    INSERT OR IGNORE INTO trades (id, wallet, market_id, side, price, size, timestamp, outcome, pnl)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with db_cursor(db_path) as cur:
        cur.execute(sql, (
            trade.id, trade.wallet, trade.market_id,
            trade.side, trade.price, trade.size, trade.timestamp,
            trade.outcome, trade.pnl,
        ))


def upsert_trades_bulk(trades: List[Trade], db_path: str = config.DB_PATH) -> None:
    sql = """
    INSERT OR IGNORE INTO trades (id, wallet, market_id, side, price, size, timestamp, outcome, pnl)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = [
        (t.id, t.wallet, t.market_id, t.side, t.price, t.size, t.timestamp, t.outcome, t.pnl)
        for t in trades
    ]
    with db_cursor(db_path) as cur:
        cur.executemany(sql, rows)


def get_trades_for_wallet(wallet: str, db_path: str = config.DB_PATH) -> List[Dict[str, Any]]:
    with db_cursor(db_path) as cur:
        cur.execute("SELECT * FROM trades WHERE wallet=? ORDER BY timestamp", (wallet,))
        return [dict(row) for row in cur.fetchall()]


# ── Markets ───────────────────────────────────────────────────────────────────

def upsert_market(market: Market, db_path: str = config.DB_PATH) -> None:
    sql = """
    INSERT INTO markets (condition_id, question, end_time, volume, resolved)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(condition_id) DO UPDATE SET
        volume=excluded.volume,
        resolved=excluded.resolved
    """
    with db_cursor(db_path) as cur:
        cur.execute(sql, (
            market.condition_id, market.question,
            market.end_time, market.volume, int(market.resolved),
        ))


def get_all_markets(db_path: str = config.DB_PATH) -> List[Dict[str, Any]]:
    with db_cursor(db_path) as cur:
        cur.execute("SELECT * FROM markets ORDER BY end_time")
        return [dict(row) for row in cur.fetchall()]


# ── Alerts ────────────────────────────────────────────────────────────────────

def insert_alert(alert: Alert, db_path: str = config.DB_PATH) -> None:
    sql = """
    INSERT INTO alerts (timestamp, type, wallet, market_id, message)
    VALUES (?, ?, ?, ?, ?)
    """
    with db_cursor(db_path) as cur:
        cur.execute(sql, (
            alert.timestamp, alert.alert_type,
            alert.wallet, alert.market_id, alert.message,
        ))


def get_recent_alerts(limit: int = 50, db_path: str = config.DB_PATH) -> List[Dict[str, Any]]:
    with db_cursor(db_path) as cur:
        cur.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cur.fetchall()]


# ── Heatmap ───────────────────────────────────────────────────────────────────

def get_heatmap_data(db_path: str = config.DB_PATH) -> List[Dict[str, Any]]:
    """Return alpha-wallet trade counts grouped by day-of-week and hour.

    We join trades with wallets so we only count alpha wallet activity.
    strftime('%w') returns 0=Sunday … 6=Saturday; we remap to 0=Mon … 6=Sun.
    """
    sql = """
    SELECT
        ((CAST(strftime('%w', datetime(t.timestamp, 'unixepoch')) AS INTEGER) + 6) % 7) AS dow,
        CAST(strftime('%H', datetime(t.timestamp, 'unixepoch')) AS INTEGER) AS hour,
        COUNT(*) AS cnt
    FROM trades t
    INNER JOIN wallets w ON w.address = t.wallet
    WHERE w.alpha_score > 0
    GROUP BY dow, hour
    ORDER BY dow, hour
    """
    with db_cursor(db_path) as cur:
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]
