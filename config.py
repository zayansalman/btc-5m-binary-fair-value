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


def _env_optional_float(name: str) -> float | None:
    """Risk-limit-style env var: blank / unset / ≤0 → None (gate disabled)."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    try:
        v = float(value)
    except ValueError:
        CONFIG_PARSE_ERRORS.append(f"{name}={value!r} is not a valid number")
        return None
    return v if v > 0 else None


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
# Trade shape. 'settle' (default): max one entry per window, hold to
# resolution — the shape the April backtest validated (+31% ROI); the spread
# is paid once at entry. 'scalp': legacy intra-window TARGET/STOP/BAND exits —
# soaked -$7.87 in 70 minutes under honest fills (median hold 8s, paying the
# spread every round trip); kept only for experiments.
BTC_EXIT_STYLE = _env_choice("BTC_EXIT_STYLE", "settle", {"settle", "scalp"})
# Shadow forward-tester: log candidate strategies' would-be trades in parallel
# with the live strategy (no real orders placed). "off" disables it entirely.
BTC_SHADOW_ENABLED = _env_choice("BTC_SHADOW_ENABLED", "on", {"on", "off"})

# Adaptive risk controller (issue #36): pause NEW entries when the strategy's
# rolling expectancy decays, before losses pile up. Complements the hard halt.
BTC_AUTO_PAUSE_ENABLED = _env_choice(
    "BTC_AUTO_PAUSE_ENABLED", "true", {"true", "false"}
) == "true"
BTC_AUTO_PAUSE_WINDOW = _env_int("BTC_AUTO_PAUSE_WINDOW", 20)
BTC_AUTO_PAUSE_MIN_TRADES = _env_int("BTC_AUTO_PAUSE_MIN_TRADES", 10)
BTC_AUTO_PAUSE_MIN_ROI = _env_float("BTC_AUTO_PAUSE_MIN_ROI", -0.15)

# --- Live trading (Polymarket CLOB) ---------------------------------------
POLYMARKET_CLOB_API = os.getenv("POLYMARKET_CLOB_API", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = _env_int("POLYMARKET_CHAIN_ID", 137)  # Polygon mainnet
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER = os.getenv("POLYMARKET_FUNDER", "")
# 0 = EOA, 1 = email/magic proxy wallet, 2 = browser wallet proxy.
POLYMARKET_SIGNATURE_TYPE = _env_int("POLYMARKET_SIGNATURE_TYPE", 1)
# Hard risk limits enforced by the unified RiskGate (issue #64) before every
# paper or live entry. Same gate, same values, both modes — paper is a
# faithful preview of live. The per-trade and daily-loss-halt limits are
# always on; the daily bankroll cap is OPT-IN and disabled when
# BTC_TRADE_BANKROLL_CAP_USD is blank / unset / ≤0. The persisted daily
# counters in SQLite keep tracking spend regardless, so the dashboard can
# still display daily throughput when the cap is off.
#
# The legacy BTC_LIVE_* names are still read as deprecated aliases for one
# release: setting only the old name logs a deprecation warning on boot.
CONFIG_DEPRECATIONS: list[str] = []


def _trade_knob(new_name: str, old_name: str, default: float) -> float:
    new_raw = os.getenv(new_name)
    old_raw = os.getenv(old_name)
    if new_raw is not None:
        return float(new_raw)
    if old_raw is not None:
        # Deferred warning — config.py is import-time; logging isn't ready yet.
        CONFIG_DEPRECATIONS.append(
            f"{old_name} is deprecated; use {new_name} (value carried over)"
        )
        return float(old_raw)
    return default


def _trade_optional(new_name: str, old_name: str) -> float | None:
    new_raw = os.getenv(new_name)
    old_raw = os.getenv(old_name)
    raw = new_raw if new_raw is not None else old_raw
    if old_raw is not None and new_raw is None:
        CONFIG_DEPRECATIONS.append(
            f"{old_name} is deprecated; use {new_name} (value carried over)"
        )
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


BTC_TRADE_MAX_USD = _trade_knob("BTC_TRADE_MAX_USD", "BTC_LIVE_MAX_TRADE_USD", 3.0)
BTC_TRADE_DAILY_LOSS_HALT_USD = _trade_knob(
    "BTC_TRADE_DAILY_LOSS_HALT_USD", "BTC_LIVE_DAILY_LOSS_HALT_USD", 10.0
)
BTC_TRADE_BANKROLL_CAP_USD: float | None = _trade_optional(
    "BTC_TRADE_BANKROLL_CAP_USD", "BTC_LIVE_BANKROLL_CAP_USD"
)
BTC_TRADE_MAX_ENTRY_SLIPPAGE = _trade_knob(
    "BTC_TRADE_MAX_ENTRY_SLIPPAGE", "BTC_LIVE_MAX_ENTRY_SLIPPAGE", 0.02
)

# Legacy aliases — kept as module attributes for one release so external
# tooling that reads ``config.BTC_LIVE_*`` continues to work.
BTC_LIVE_MAX_TRADE_USD = BTC_TRADE_MAX_USD
BTC_LIVE_DAILY_LOSS_HALT_USD = BTC_TRADE_DAILY_LOSS_HALT_USD
BTC_LIVE_BANKROLL_CAP_USD = BTC_TRADE_BANKROLL_CAP_USD
BTC_LIVE_MAX_ENTRY_SLIPPAGE = BTC_TRADE_MAX_ENTRY_SLIPPAGE
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
# Stale-model guard + favorites filter (issue #29, from the 26h settle soak,
# n=225): claimed edges above ~7% and entries below ~50c were where adverse
# selection lived; the joint surviving slice ran +22.8% ROI (n=48, in-sample).
BTC_PAPER_ENTRY_EDGE_MAX = _env_float("BTC_PAPER_ENTRY_EDGE_MAX", 0.07)
BTC_PAPER_MIN_ENTRY_PRICE = _env_float("BTC_PAPER_MIN_ENTRY_PRICE", 0.50)
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
