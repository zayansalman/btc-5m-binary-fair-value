"""SQLite storage for the BTC 5-minute binary fair-value strategy lab."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncIterator

import aiosqlite

from config import DB_PATH
from logging_setup import redact_secrets


SCHEMA = """
CREATE TABLE IF NOT EXISTS notification_feed (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL,
  details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_notification_feed_created
  ON notification_feed(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_feed_event
  ON notification_feed(event_type);

CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS btc_paper_ticks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  window_slug TEXT NOT NULL,
  market_question TEXT,
  remaining_seconds INTEGER,
  spot_price REAL,
  reference_price REAL,
  sigma_per_second REAL,
  market_up_price REAL,
  market_down_price REAL,
  fair_up_prob REAL,
  edge REAL,
  signal_side TEXT,
  confidence REAL,
  notional_usd REAL,
  reason TEXT,
  feed_source TEXT,
  up_best_bid REAL,
  up_best_ask REAL,
  up_bid_size REAL,
  up_ask_size REAL,
  down_best_bid REAL,
  down_best_ask REAL,
  down_bid_size REAL,
  down_ask_size REAL,
  quote_source TEXT,
  gamma_up_price REAL
);
CREATE INDEX IF NOT EXISTS idx_btc_paper_ticks_created
  ON btc_paper_ticks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_btc_paper_ticks_window
  ON btc_paper_ticks(window_slug);

CREATE TABLE IF NOT EXISTS btc_paper_positions (
  position_id INTEGER PRIMARY KEY AUTOINCREMENT,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  window_slug TEXT NOT NULL,
  market_question TEXT,
  side TEXT NOT NULL,
  state TEXT NOT NULL,
  entry_price REAL NOT NULL,
  exit_price REAL,
  notional_usd REAL NOT NULL,
  shares REAL NOT NULL,
  opened_spot REAL,
  closed_spot REAL,
  confidence REAL,
  edge REAL,
  entry_reason TEXT,
  exit_reason TEXT,
  realized_pnl_usd REAL,
  feed_source TEXT,
  quote_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_btc_paper_positions_state
  ON btc_paper_positions(state);
CREATE INDEX IF NOT EXISTS idx_btc_paper_positions_opened
  ON btc_paper_positions(opened_at DESC);

CREATE TABLE IF NOT EXISTS btc_live_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  window_slug TEXT,
  token_id TEXT,
  intent TEXT NOT NULL,
  side TEXT NOT NULL,
  price REAL,
  size REAL,
  notional_usd REAL,
  order_type TEXT,
  status TEXT NOT NULL,
  clob_order_id TEXT,
  error TEXT,
  details_json TEXT,
  -- 'live' rows are real CLOB attempts. 'paper' rows record what the live
  -- gate WOULD have done for the same signal, so paper is a faithful preview
  -- of live (issue #64). All rows that predate the migration are 'live'.
  mode TEXT
);
CREATE INDEX IF NOT EXISTS idx_btc_live_orders_created
  ON btc_live_orders(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_btc_live_orders_status
  ON btc_live_orders(status);
"""

BTC_LIVE_ORDERS_COLUMN_MIGRATIONS = {
    "mode": "TEXT",
}

BTC_POSITION_COLUMN_MIGRATIONS = {
    "market_question": "TEXT",
    "exit_price": "REAL",
    "shares": "REAL",
    "opened_spot": "REAL",
    "closed_spot": "REAL",
    "confidence": "REAL",
    "edge": "REAL",
    "entry_reason": "TEXT",
    "exit_reason": "TEXT",
    "realized_pnl_usd": "REAL",
    "feed_source": "TEXT",
    # 'clob' since v0.3.1; NULL rows predate executable CLOB quotes (issue #22)
    # and are excluded from all KPI aggregates (re-baseline).
    "quote_source": "TEXT",
    # 'settle' | 'scalp' since v0.3.2 (issue #28); KPIs aggregate only the
    # active style so baselines from different trade shapes never blend.
    "strategy_style": "TEXT",
    # 'live' | 'paper' — recorded at insert time from whether the live
    # executor was attached. Legacy rows are backfilled by joining
    # btc_live_orders on window_slug.
    "mode": "TEXT",
}

# Issue #22: executable top-of-book quotes journaled per tick. Rows without
# quote_source were priced off stale Gamma outcomePrices and are excluded
# from dashboard KPIs and the paper summary.
BTC_TICK_COLUMN_MIGRATIONS = {
    "up_best_bid": "REAL",
    "up_best_ask": "REAL",
    "up_bid_size": "REAL",
    "up_ask_size": "REAL",
    "down_best_bid": "REAL",
    "down_best_ask": "REAL",
    "down_bid_size": "REAL",
    "down_ask_size": "REAL",
    "quote_source": "TEXT",
    "gamma_up_price": "REAL",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@asynccontextmanager
async def connect() -> AsyncIterator[aiosqlite.Connection]:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    async with connect() as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA)
        await _migrate_columns(db, "btc_paper_positions", BTC_POSITION_COLUMN_MIGRATIONS)
        await _migrate_columns(db, "btc_paper_ticks", BTC_TICK_COLUMN_MIGRATIONS)
        await _migrate_columns(db, "btc_live_orders", BTC_LIVE_ORDERS_COLUMN_MIGRATIONS)
        await _backfill_position_mode(db)
        await _backfill_live_order_mode(db)
        await db.commit()


async def _backfill_position_mode(db: aiosqlite.Connection) -> None:
    """Fill mode on legacy rows: 'live' iff a SUBMITTED ENTRY exists for the slug.

    A position was real iff the live executor placed a corresponding entry on
    that same window — the journal proves that. All other rows are paper.
    Rows that already carry a mode are left alone.
    """
    await db.execute(
        """
        UPDATE btc_paper_positions
           SET mode = 'live'
         WHERE mode IS NULL
           AND window_slug IN (
               SELECT window_slug FROM btc_live_orders
                WHERE intent = 'ENTRY' AND status = 'SUBMITTED'
           )
        """
    )
    await db.execute(
        "UPDATE btc_paper_positions SET mode = 'paper' WHERE mode IS NULL"
    )


async def _backfill_live_order_mode(db: aiosqlite.Connection) -> None:
    """Every pre-migration row in btc_live_orders is real CLOB activity → 'live'."""
    await db.execute(
        "UPDATE btc_live_orders SET mode = 'live' WHERE mode IS NULL"
    )


async def _migrate_columns(
    db: aiosqlite.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        existing = {row["name"] for row in await cur.fetchall()}
    for column, column_type in columns.items():
        if column not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


async def get_config(key: str, default: str | None = None) -> str | None:
    async with connect() as db:
        async with db.execute("SELECT value FROM config WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
    return row["value"] if row else default


async def set_config(key: str, value: str | None) -> None:
    # The dashboard "detail" line is stored here and rendered in the UI;
    # scrub any secret that a stringified exception might have carried in.
    value = redact_secrets(value)
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO config(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              updated_at = excluded.updated_at
            """,
            (key, value, utc_now_iso()),
        )
        await db.commit()


async def journal_live_order(
    *,
    intent: str,
    side: str,
    status: str,
    window_slug: str | None = None,
    token_id: str | None = None,
    price: float | None = None,
    size: float | None = None,
    notional_usd: float | None = None,
    order_type: str | None = None,
    clob_order_id: str | None = None,
    error: str | None = None,
    details: dict[str, Any] | None = None,
    mode: str = "live",
) -> None:
    """Append one order/fill/cancel attempt to the btc_live_orders journal.

    ``mode`` is 'live' for real CLOB activity and 'paper' for paper-side
    BLOCKED rows surfaced by the shared RiskGate (issue #64).
    """
    error = redact_secrets(error)
    payload = redact_secrets(json.dumps(details or {}, sort_keys=True, default=str))
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO btc_live_orders(
              created_at, window_slug, token_id, intent, side, price, size,
              notional_usd, order_type, status, clob_order_id, error,
              details_json, mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                window_slug,
                token_id,
                intent,
                side,
                price,
                size,
                notional_usd,
                order_type,
                status,
                clob_order_id,
                error,
                payload,
                mode,
            ),
        )
        await db.commit()


async def notify(
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    # Scrub secrets at the sink: any caller-supplied string is persisted here.
    message = redact_secrets(message)
    payload = redact_secrets(json.dumps(details or {}, sort_keys=True, default=str))
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO notification_feed(created_at, event_type, message, details_json)
            VALUES (?, ?, ?, ?)
            """,
            (utc_now_iso(), event_type, message, payload),
        )
        await db.commit()
