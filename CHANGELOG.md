# Changelog

## v0.3.0 — Live Execution Mode (2026-06-10)

Closes #20. Adds an opt-in live trading mode that routes the existing signal path through Polymarket's CLOB via `py-clob-client`. **Paper remains the default**; nothing changes unless the operator explicitly flips every gate.

### Live executor (`btc_5m_fv/execution/live.py`)
- `LiveExecutor` wraps the synchronous `ClobClient` behind an async API (all network calls via `asyncio.to_thread`)
- `start()` derives + sets CLOB API creds (`create_or_derive_api_creds` / `set_api_creds`) and verifies reachability before any trading
- Entries: GTC limit BUY at best ask (book best ask, gamma price fallback); price rounded to the market tick size, size rounded **down** to the CLOB 2-decimal share granularity, Polymarket minimum order size enforced from the order book (default 5 shares)
- Exits: GTC limit SELL at best bid for the **matched** entry size (`get_order` → `size_matched`), so the bot never sells shares that never filled; any resting entry remainder is cancelled before the sell
- Cancel-on-roll: unfilled entry orders are cancelled on `WINDOW_ROLL` and `BAND_REENTRY` exits (matched size is captured post-cancel so fills landing mid-cancel still get flattened)

### Hard risk limits (enforced in code BEFORE every order)
- Per-trade cap: `BTC_LIVE_MAX_TRADE_USD` (default $3)
- Max 1 open live position/order
- Daily realized-loss halt: `BTC_LIVE_DAILY_LOSS_HALT_USD` (default $10, UTC day) — **persisted in SQLite and reloaded at boot**, so Stop/Start or a restart cannot reset it within the day
- Daily bankroll cap on summed buy notionals: `BTC_LIVE_BANKROLL_CAP_USD` (default $30/UTC day, persisted); the unfilled remainder of a cancelled entry is credited back
- Entry slippage guard: `BTC_LIVE_MAX_ENTRY_SLIPPAGE` (default 0.02) blocks buys when the live ask has gapped above the signal price that produced the edge
- Kill switch: the file `data/KILL` blocks all NEW entries and cancels resting orders, checked every tick plus immediately before each entry POST (TOCTOU guard); it re-arms when the file is deleted. Exits stay allowed under kill — flattening only reduces exposure
- Realized PnL feeds the loss halt from CONFIRMED exit fills (actual matched size at the executed order's limit price), never from paper-price estimates at submission time

### Exit lifecycle & stop safety
- Exit SELLs never rest: the order is awaited up to `BTC_LIVE_EXIT_FILL_TIMEOUT_SECONDS` (default 10s) and cancelled if unfilled, so no stale GTC exit can sit in a 5-minute book into resolution; partial fills are accounted per tranche and only the remainder is retried
- A failed/blocked/unfilled live exit **keeps the ledger row OPEN** and is retried on the next tick — the ledger can never claim flat while real tokens remain on the exchange
- Cancels are verified against the DELETE response body (`canceled` list) with a terminal-status re-check; on cancel failure the order id stays tracked for retry instead of being forgotten
- Live entries write the ledger row BEFORE the order is submitted (failed submits delete it), so a DB failure after submit can never leave a real position unmanaged
- Stop: the controller sets the stop flag and **waits for the runner thread** to cancel resting orders and flatten through the executor on its own event loop — single-threaded executor ownership, no stop race, no paper-closing of live positions; unflattenable rows are reported for manual action

### Boot gating & reconciliation
- Live mode REFUSES to start unless `POLYMARKET_PRIVATE_KEY` is set AND `BTC_LIVE_CONFIRM=YES_I_UNDERSTAND` — checked on the dashboard `/api/start` path and again at loop start; it never silently falls back to paper
- Boot also REFUSES when: `POLYMARKET_FUNDER` is empty with signature type 1/2 (such orders are signed with the EOA as maker and rejected by the CLOB), the signature type is unknown, or any risk-limit env var failed to parse (no silent fallback to looser defaults)
- Boot reconciliation: `start()` cancels ALL resting CLOB orders on the account and re-adopts any open ledger position from the order journal (exchange-confirmed fill size); paper artifacts / never-filled rows are closed as `RECONCILED_*`; unreconcilable state refuses boot instead of trading on top of unknown exposure
- Wallet config: `POLYMARKET_FUNDER` (proxy wallet), `POLYMARKET_SIGNATURE_TYPE` (0 EOA / 1 email / 2 browser, default 1)
- The private key is never logged and never journaled

### Audit trail
- New SQLite table `btc_live_orders` journals every order/cancel attempt — including risk-gate BLOCKED attempts that never reach the network
- Engine ledger (`btc_paper_positions`) mirrors live fills (executor price/size), notifications use `btc_live_entry` / `btc_live_exit` events

### Dashboard & docs
- Status/brief/settings copy is mode-aware: "LIVE — orders are real" vs paper; stale "no live orders are placed by this build" claims removed
- `.env.example` documents all new vars (key/funder ship empty); `docs/OPERATIONS_RUNBOOK.md` gains a "Going live" section with launch steps, risk limits, and kill-switch drill

### Tests
- 70 new unit tests with a fully mocked `ClobClient` (boot refusal incl. funder/signature/parse-error gates, order construction/rounding/min-size, slippage guard, all risk gates incl. restart persistence, kill switch incl. re-arm and TOCTOU, exit timeout/partial-fill lifecycle, boot reconciliation, failed-exit-keeps-row-open wiring, paper-default invariance, dashboard copy)
- `py-clob-client` pinned in `requirements.txt` and `pyproject.toml`
- **395 total, all passing**

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
