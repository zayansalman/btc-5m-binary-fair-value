# Changelog

## v0.3.7 — Existing-Wallet (MetaMask) Onboarding + Auto-Detect (2026-06-12)

Fixes #34. For users who already hold funds in a connected-wallet Polymarket account, added a no-fund-movement path: trade the existing balance in place using the wallet's signer key.

- `tools/live_detect_wallet.py`: given the signer key in `.env`, derives every wallet it could control (EOA / POLY_PROXY / Gnosis Safe via the SDK's `derive_proxy_wallet_address` / `derive_safe_wallet_address`), reads each candidate's on-chain pUSD balance on Polygon (multi-RPC fallback: publicnode / 1rpc / drpc), and writes the funded one's address + matching signature type into `.env`. Deterministic — the funded address's derivation *is* its signature type (EOA=0, POLY_PROXY=1, GNOSIS_SAFE=2); no guessing.
- Runbook documents Path A (existing connected wallet, auto-detected) vs Path B (fresh isolated deposit wallet), with the key-blast-radius tradeoff stated plainly.
- pUSD collateral token address (`0xC011a7E1…`, Polygon) sourced from the SDK's PRODUCTION environment config.

## v0.3.6 — Verified Self-Serve Deposit-Wallet Onboarding (2026-06-12)

Fixes #33. Hardened and verified `tools/live_setup.py` end-to-end against the live Polymarket API:

- **Key never leaks**: the generated private key is written straight into `.env` (perms `0600`, `.env.bak` saved) and is never printed to stdout — so it cannot land in terminal scrollback or an assistant transcript. Only the public signer/deposit addresses are shown. `_merge_env` updates keys in place, preserves comments/other keys, and is unit-tested to NEVER auto-write `BTC_LIVE_CONFIRM` (the operator's conscious go-live step).
- **Correct deposit-wallet flow** (verified live): mint a Builder API Key from the signer key (L1→L2→builder, self-serve, used only for the gasless deploy then discarded) → `SecureClient.create(api_key=...)` deploys the deterministic type-3 deposit wallet gaslessly. The earlier `setup_trading_approvals()` call was the EOA method and hit a relayer allowlist rejection; removed. Collateral allowance is set by `update_balance_allowance` on first funded connect (executor + preflight already call it) — no separate approval step.
- `polymarket-client` declared as the optional `setup` extra (one-time onboarding only; runtime trading uses `py-clob-client-v2`).

## v0.3.5 — Real Live-Setup Flow (2026-06-12)

Fixes #32. The runbook's "export private key from Polymarket settings" step does not exist in the product (operator-verified; absent from current docs). Setup rewritten to the documented reality — you bring a wallet you control:

- `tools/live_setup.py` — one-time onboarding via official py-sdk (optional install): generates a fresh key if needed, deploys the deterministic deposit wallet (signature type 3), runs idempotent gasless trading approvals, prints the `.env` block and funding instructions. Existing UI funds move by **withdrawing to the funder address** — no export anywhere.
- `tools/live_preflight.py` — read-only GO/NO-GO: boot gate, credential derivation, CLOB reachability, funder balance/allowance as the CLOB sees it.
- `LiveExecutor.start()` now refreshes the CLOB balance/allowance cache (`update_balance_allowance`, best-effort) — the documented pre-first-order step.
- Runbook "Going Live" rewritten (wallet decision table, scripted path, preflight gate); `.env.example` signature-type guidance corrected (type 3 recommended).

## v0.3.4 — Migrate to py-clob-client-v2 (2026-06-12)

Fixes #31 (launch blocker). Upstream archived `py-clob-client` (v1) with "no longer functional — should not be used"; live mode would have failed at first auth. Migrated `LiveExecutor` to official `py-clob-client-v2` (1.0.1):

- `create_or_derive_api_creds()` → `create_or_derive_api_key()`; `cancel(order_id)` → `cancel_order(OrderPayload(orderID=...))`; import paths updated. Constructor surface (incl. `signature_type`/`funder` for proxy wallets), `set_api_creds`, `get_order`, `cancel_all`, order/cancel response bodies, and `OrderBookSummary` fields (`min_order_size`, `tick_size`, worst→best level ordering) are unchanged — verified against installed v2 source.
- v1 removed from the venv and dependency declarations; suite green with zero v1 references; v2 smoke-tested against the live CLOB API (`get_ok`, `get_server_time`).

## v0.3.3 — Anti-Adverse-Selection Entry Filters (2026-06-12)

Fixes #29. The 26h settle soak (n=225, -14.4% ROI overall) revealed structured losses: PnL by claimed edge decreases monotonically (4.5–7%: +7.0% ROI; >15%: -36% to -57%), and entries below 50¢ lose badly while favorites win 63–72%. Large apparent edge = the model lagging a fast market (adverse selection), not opportunity.

- `BTC_PAPER_ENTRY_EDGE_MAX` (default **0.07**): entries whose claimed edge exceeds the cap are rejected — "skip: edge above cap (stale-model guard)"
- `BTC_PAPER_MIN_ENTRY_PRICE` default raised 0.05 → **0.50**: favorites only
- In-sample, the joint surviving slice (edge 4.5–7%, entry ≥ 50¢) ran **+22.8% ROI, 73% win, n=48**, positive in both sample halves. This is post-hoc structure: the filtered strategy must hold in a fresh out-of-sample soak before the live gate opens.

## v0.3.2 — Settle-Style Strategy Profile (2026-06-11)

Fixes #28, closes #27. First honest-baseline soak (135 trades / 70 min) showed the legacy scalp shape is structurally negative under real fills: -$7.87, median hold 8s, up to 17 entries per window, 65 STOPs (-$55) vs 48 TARGETs (+$44) — it pays the spread every few ticks and stops out on noise. The +31% April backtest used the opposite shape.

- New `BTC_EXIT_STYLE`: **`settle` (default)** — max one entry per window (kills churn by construction), no TARGET/STOP/BAND/TIME exits; positions ride to resolution and close at the Chainlink-settled 1.00/0.00. `scalp` keeps the legacy behavior for experiments.
- Live mode: `LiveExecutor.record_settlement(won, window_slug)` registers the resolution outcome (PnL into the persisted daily-loss halt, journal `SETTLEMENT` row, slot freed) without placing an exit order; any resting entry remainder is cancelled. Winning tokens await operator redemption — runbook section added.
- Positions journal `strategy_style`; KPIs aggregate only the active style (third baseline reset; prior rows remain as audit trail).
- 7 new tests (style gating, one-entry-per-window, settlement win/loss accounting, no-exit-order bypass).

## v0.3.1 — Data Integrity: CLOB Quotes + Settlement Feed (2026-06-11)

Fixes #21, #22, #23. The signal path now prices, fills, and settles against the same data the market actually uses.

### Executable quotes (#22)
- Signal edge is computed against the CLOB best ask per outcome token (`signal_from_executable_edges`); Gamma `outcomePrices` are journaled (`gamma_up_price`) only to quantify their staleness, never used for pricing
- Honest paper fills: BUY at best ask, SELL at best bid, capped by top-of-book size; empty/crossed books skip with a journaled reason
- Window-rolled paper positions settle at the actual Chainlink resolution (1.0/0.0 via the settlement endpoint) instead of pricing the old position off the new window's book
- Tick journal gains top-of-book columns (both sides) + `quote_source`; performance KPIs exclude pre-fix rows (re-baseline — old rows stay as audit trail)

### Settlement-aligned Chainlink feed (#21)
- New `ChainlinkSettlementConnector` (REST): window reference open with provisional-revision stabilization, fast settlement via `open(N+1) == close(N)`, cache-busting, full Chrome-fingerprint WAF headers
- New `ChainlinkWsFeed` (WS): live 1s prints from `ws-live-data.polymarket.com` (byte-exact compact subscribe filters, literal-PING keepalive, reconnect with backoff, 429-aware), feeding spot + sigma
- REST spot poll fallback: `openPrice` of a window starting seconds ago IS the near-live settlement print — the engine survives WS outages/rate limits without mixing sources
- Binance demoted to volatility-shape fallback and backtest tooling — its LEVELS never touch the model (measured Chainlink−Binance basis ≈ −$50.7, std $3.8)
- Tie rule: fair value now includes the discrete tie mass P(close == open), which resolves Up — `fair_up > 0.5` when price pins the reference
- A degraded settlement feed blocks new entries and suppresses fair-value-based exits (BAND_REENTRY); time/target/stop exits still run

### Dashboard state (#23)
- Controller state derives from the actual runner thread in both directions: never STOPPED while ticking, never RUNNING while dead

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
