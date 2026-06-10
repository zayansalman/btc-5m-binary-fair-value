# Changelog

## v0.2.2 — Deterministic Test Fills (2026-06-10)

Fixes #24.

- **Flaky paper-execution tests** — `PaperExecutionManager._determine_fill()` rolled module-level unseeded `random.random()` per order (1% PARTIAL_FILL, 0.1% REJECTED), so FILLED-assuming unit tests failed in ~30-40% of full-suite runs. The RNG is now injectable via a new `rng: random.Random | None` constructor param (default unchanged: fresh unseeded `random.Random()`); test fixtures pin a deterministic always-fill RNG. Partial-fill/reject paths keep their dedicated tests. Verified 20/20 consecutive green runs of `tests/unit/test_paper_execution.py`.

## v0.2.1 — Runtime Blockers (2026-06-10)

Fixes #19.

- **main.py boot crash** — imported `DASHBOARD_PORT`, renamed to `DASHBOARD_SERVER_PORT` in the v0.2 dashboard migration. FastAPI entrypoint never started.
- **Binance endpoint unreachable** — `api.binance.com` was hardcoded in 4 modules and times out on some networks. New `BINANCE_API_BASE` env var (default `https://data-api.binance.vision`, Binance's public market-data mirror with identical `/api/v3` routes) threaded through `btc_bot/paper.py`, `btc_bot/backtest.py`, `btc_5m_fv/backtest/conditional.py`, and `BinanceConnector`.
- **Backtest resilience** — kline fetches in both backtest modules now retry 3× with backoff; a single transient SSL timeout no longer kills a 2,688-combination grid run.

## v0.2.0 — Full System Rebuild (2026-05-28)

A complete architectural rebuild from monolithic demo to modular trading system. Every open GitHub issue has been addressed.

### Architecture
- **New package structure** (`btc_5m_fv/`) with 7 sub-packages: `core`, `strategy`, `connectors`, `storage`, `backtest`, `execution`, `ops`
- **Interface-driven design** — all components implement ABCs from `core.interfaces`
- **`pyproject.toml`** replaces `requirements.txt` with modern Python packaging
- **GitHub Actions CI** — tests on Python 3.11/3.12, lint with ruff, type-check with mypy

### Core (closes #10, #15)
- `core/types.py` — 16 frozen dataclasses (MarketWindow, Signal, Tick, PaperOrder, PaperPosition, etc.)
- `core/interfaces.py` — 5 abstract base classes (market connector, price connector, signal generator, execution manager, risk service)
- `core/exceptions.py` — 5 custom exceptions with hierarchy

### Strategy
- Extracted from `btc_bot/strategy.py` into 3 focused modules:
  - `strategy/fair_value.py` — `sigma_per_second()`, `fair_up_probability()`
  - `strategy/sizing.py` — `confidence_from_edge()`, `notional_from_confidence()`
  - `strategy/signal.py` — `signal_from_edge()` with `SignalAction` enum

### Connectors (closes #11, #12, #16, #9)
- `connectors/polymarket.py` — market discovery with slug pattern matching
- `connectors/binance.py` — spot price, reference price, recent closes with rate limiting awareness
- `connectors/chainlink.py` — stub for Chainlink Data Streams integration (#9)
- `connectors/registry.py` — registration, health checks, history tracking
- All connectors implement `AbstractPriceConnector` / `AbstractMarketConnector`

### Storage & Backtest (closes #2, #3, #4)
- `storage/recorder.py` — `MarketDataRecorder` persists windows, ticks, CLOB snapshots to SQLite
- `storage/replay.py` — `DeterministicReplay` feeds recorded data through signal generator
- `backtest/harness.py` — `FullMarketBacktestHarness` runs strategy on ALL recorded windows
- `backtest/metrics.py` — `BacktestResult` with exit attribution, `FrictionModel` for realistic simulation
- `backtest/conditional.py` — original trade-history conditional backtest preserved

### Execution & Risk (closes #13, #14, #5)
- `execution/paper.py` — `PaperExecutionManager` with explicit order lifecycle: PENDING -> ACKNOWLEDGED -> FILLED
- `execution/risk.py` — `RiskService` with pre-trade checks, drawdown monitoring, win/loss tracking
- `ops/controller.py` — `BotController` unified tick loop

### Operations (closes #6, #7, #8)
- `ops/telemetry.py` — `FeedHealthTracker` (p50/p95/p99 latency) and `LatencyTracker`
- `ops/incidents.py` — `IncidentManager` state machine + `RunbookActions` for every incident type
- `tests/conftest.py` — deterministic fixtures for reproducible tests
- 18 test files, 321 total tests

### Dashboard Migration
- Replaced Gradio (150MB+ dependency) with **FastAPI + Jinja2**
- `ops/dashboard/app.py` — FastAPI with routes, SSE for real-time updates
- `ops/dashboard/templates/` — Jinja2 templates (5 tabs)
- `ops/dashboard/static/` — CSS and vanilla JS
- Server-Sent Events replace `gr.Timer` polling
- Visual design preserved exactly

### Tests
- 288 unit tests (network-free, deterministic)
- 11 integration tests
- 23 e2e tests
- 4 preserved smoke tests
- **321 total, all passing**

---

## v0.1.0 — Initial Demo

Original monolithic implementation:
- Gradio dashboard with custom CSS
- Paper trading loop in `btc_bot/paper.py`
- Strategy math in `btc_bot/strategy.py`
- SQLite persistence in `db.py`
- 4 smoke tests
- 7 commits, 14 Python files
