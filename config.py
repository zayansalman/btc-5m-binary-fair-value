"""Configuration for the local BTC 5-minute binary fair-value strategy lab."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# Env vars that failed to parse. Paper mode tolerates the fallback defaults,
# but live mode REFUSES to boot while this is non-empty (see
# btc_5m_fv.execution.live.assert_live_boot_allowed): a typo in a risk limit
# must never silently degrade to looser defaults with real funds.
CONFIG_PARSE_ERRORS: list[str] = []


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        CONFIG_PARSE_ERRORS.append(f"{name}={value!r} is not a valid number")
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        CONFIG_PARSE_ERRORS.append(f"{name}={value!r} is not a valid integer")
        return default


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    return value if value in allowed else default


REPO_ROOT = Path(__file__).parent.resolve()

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(
    os.getenv("DB_PATH", str(DATA_DIR / "btc_5m_binary_fair_value.db"))
).expanduser().resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DASHBOARD_SERVER_NAME = os.getenv("DASHBOARD_SERVER_NAME", "127.0.0.1")
DASHBOARD_SERVER_PORT = int(os.getenv("DASHBOARD_SERVER_PORT", "7860"))

# Market-data mirror of the spot API: api.binance.com is unreachable from some
# networks, while data-api.binance.vision serves the same /api/v3 endpoints.
BINANCE_API_BASE = os.getenv("BINANCE_API_BASE", "https://data-api.binance.vision")
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"
BTC_CHAINLINK_STREAM_URL = "https://data.chain.link/streams/btc-usd-cexprice-streams"
BTC_MARKET_TIMEFRAME_MINUTES = 5

# --- Settlement-aligned Chainlink feed (issue #21) --------------------------
# Polymarket resolves BTC 5m markets on its Chainlink BTC/USD stream, NOT on
# Binance (measured basis: Chainlink ~ $50.7 BELOW Binance, std $3.8). The
# reference open, live spot, and sigma all come from these two endpoints;
# Binance remains only as a volatility-shape fallback and for backtest tooling.
POLYMARKET_CRYPTO_PRICE_API = os.getenv(
    "POLYMARKET_CRYPTO_PRICE_API", "https://polymarket.com/api/crypto/crypto-price"
)
POLYMARKET_LIVE_DATA_WS = os.getenv(
    "POLYMARKET_LIVE_DATA_WS", "wss://ws-live-data.polymarket.com"
)
# Seconds after which the latest Chainlink WS print is considered stale; a
# stale/absent settlement feed blocks NEW entries (exits still run).
BTC_CHAINLINK_STALE_SECONDS = _env_float("BTC_CHAINLINK_STALE_SECONDS", 15.0)
# Observed Chainlink print granularity (~2 decimal places at $61k). Used to
# estimate the discrete tie mass P(close == open), which resolves Up.
BTC_PRINT_GRANULARITY_USD = _env_float("BTC_PRINT_GRANULARITY_USD", 0.01)

# Execution target. Paper is the default; live places REAL orders on the
# Polymarket CLOB and only boots when POLYMARKET_PRIVATE_KEY is set AND
# BTC_LIVE_CONFIRM=YES_I_UNDERSTAND. The private key is never logged.
BTC_BOT_MODE = _env_choice("BTC_BOT_MODE", "paper", {"paper", "live"})

# --- Live trading (Polymarket CLOB) ---------------------------------------
POLYMARKET_CLOB_API = os.getenv("POLYMARKET_CLOB_API", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = _env_int("POLYMARKET_CHAIN_ID", 137)  # Polygon mainnet
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER = os.getenv("POLYMARKET_FUNDER", "")
# 0 = EOA, 1 = email/magic proxy wallet, 2 = browser wallet proxy.
POLYMARKET_SIGNATURE_TYPE = _env_int("POLYMARKET_SIGNATURE_TYPE", 1)
# Hard risk limits enforced in code before every live order. The daily loss
# halt and daily bankroll cap are persisted in SQLite so restarts cannot
# reset them within a UTC day.
BTC_LIVE_MAX_TRADE_USD = _env_float("BTC_LIVE_MAX_TRADE_USD", 3.0)
BTC_LIVE_DAILY_LOSS_HALT_USD = _env_float("BTC_LIVE_DAILY_LOSS_HALT_USD", 10.0)
BTC_LIVE_BANKROLL_CAP_USD = _env_float("BTC_LIVE_BANKROLL_CAP_USD", 30.0)
# Block an entry when the live best ask sits more than this far above the
# signal price that generated the edge (thin 5m books can gap badly).
BTC_LIVE_MAX_ENTRY_SLIPPAGE = _env_float("BTC_LIVE_MAX_ENTRY_SLIPPAGE", 0.02)
# How long an exit SELL may rest before it is cancelled and retried at the
# new best bid. Exits never rest beyond this bound.
BTC_LIVE_EXIT_FILL_TIMEOUT_SECONDS = _env_float("BTC_LIVE_EXIT_FILL_TIMEOUT_SECONDS", 10.0)
# Must be the literal string YES_I_UNDERSTAND for live mode to boot.
BTC_LIVE_CONFIRM = os.getenv("BTC_LIVE_CONFIRM", "")
# Touch this file to halt all live trading and cancel open orders.
KILL_SWITCH_PATH = Path(
    os.getenv("KILL_SWITCH_PATH", str(DATA_DIR / "KILL"))
).expanduser().resolve()
BTC_PAPER_MIN_TRADE_USD = _env_float("BTC_PAPER_MIN_TRADE_USD", 1.0)
BTC_PAPER_MAX_TRADE_USD = _env_float("BTC_PAPER_MAX_TRADE_USD", 5.0)
BTC_PAPER_TICK_SECONDS = _env_float("BTC_PAPER_TICK_SECONDS", 5.0)
BTC_PAPER_ENTRY_EDGE_MIN = _env_float("BTC_PAPER_ENTRY_EDGE_MIN", 0.045)
BTC_PAPER_MIN_CONFIDENCE = _env_float("BTC_PAPER_MIN_CONFIDENCE", 0.50)
BTC_PAPER_ENTRY_MIN_REMAINING_SECONDS = int(
    os.getenv("BTC_PAPER_ENTRY_MIN_REMAINING_SECONDS", "60")
)
BTC_PAPER_TARGET_RETURN = _env_float("BTC_PAPER_TARGET_RETURN", 0.10)
BTC_PAPER_STOP_RETURN = _env_float("BTC_PAPER_STOP_RETURN", -0.08)
BTC_PAPER_TIME_EXIT_SECONDS = int(os.getenv("BTC_PAPER_TIME_EXIT_SECONDS", "45"))

BTC_HISTORY_CSV_PATH = Path(
    os.getenv(
        "BTC_HISTORY_CSV_PATH",
        str(DATA_DIR / "polymarket_history.csv"),
    )
).expanduser()
