# Code Map — where to make what change

> Read this first. This repo is **two coupled code trees**, not one. Most doc confusion
> comes from treating one as "the codebase" and the other as legacy. Both are LIVE.

## The two trees

| Concern | Edit here |
|---|---|
| Trading loop, signal math, paper fills, self-improvement (calibration, adaptive pause, params) | **`btc_bot/`** — `paper.py:run_paper_loop` is *the* loop; `btc_bot/strategy.py` is the live signal math |
| Execution gates, live CLOB executor, connectors, dashboard, recorder, backtest harness | **`btc_5m_fv/`** — `execution/gate.py:RiskGate`, `execution/live.py:LiveExecutor` |
| Config, DB schema, logging (foundation; imported by both, imports neither) | top-level **`config.py` / `db.py` / `logging_setup.py`** |

They are **bidirectionally coupled**: the FastAPI dashboard imports `btc_bot.*`; `btc_bot` imports back into `btc_5m_fv.{execution,connectors}`.

## "I want to change X → edit Y"

| Change | File |
|---|---|
| The **live** trading signal / edge / confidence | `btc_bot/strategy.py` (NOT `btc_5m_fv/strategy/` — that only feeds backtests) |
| Risk limits / kill-switch / daily-loss halt | `btc_5m_fv/execution/gate.py` |
| Live order placement | `btc_5m_fv/execution/live.py` |
| Add/modify a market or price feed | `btc_5m_fv/connectors/` |
| Dashboard panel / UI | `btc_5m_fv/ops/dashboard/panels/` |
| An env knob / default | `config.py` + document in `.env.example` |
| A new DB column | `db.py` migration dict (NOT the `SCHEMA` literal) |
| Start/Stop behavior | `btc_bot/controller.py` |

## Runtime flow

```
main.py ─ singleton lock + init_db ─▶ uvicorn ─▶ btc_5m_fv/ops/dashboard/app.py (FastAPI :7860)
   operator ▶ Start ─▶ btc_bot/controller.py:request_start ─(daemon thread)─▶ btc_bot/paper.py:run_paper_loop
       DISCOVER market → FEED (chainlink_settlement) → SIGNAL (btc_bot/strategy.py)
       → RISK (btc_5m_fv/execution/gate.py:RiskGate) → EXECUTE (paper sim | live.py) → STORE (db.py)
   DASHBOARD reads DB read-only via btc_5m_fv/ops/dashboard/ems.py → 8 panels/
```

`main.py` takes a singleton `fcntl` lock on `data/bot.lock`, runs `init_db`, then serves the FastAPI app via uvicorn. The Gradio `dashboard.py` branch is a fallback that **never executes** — `HAS_NEW_DASHBOARD` is always true.

## Dual-fork warnings (same logic in both trees — change the LIVE one)

- `sigma_per_second` / `fair_up_probability` / `signal_from_edge` exist in BOTH `btc_bot/strategy.py` (live) and `btc_5m_fv/strategy/*` (backtest/tests). Editing the `btc_5m_fv` copy does **not** change live behavior.
- `RiskGate` (`btc_5m_fv/execution/gate.py`, LIVE) vs `RiskService` (`btc_5m_fv/execution/risk.py`, DEAD on the live path).

## WIRED in the table ≠ live on the trading path

The generated inventory below counts **direct non-test importers**. A non-zero count means "something imports this," not "this runs when the bot trades." Several modules import-resolve but are dead on the live path. Edit them only if you mean to touch backtests/tests — never expecting a runtime trading effect:

| Module | Table says | Reality |
|---|---|---|
| `dashboard.py` | WIRED (imported by `main.py`) | Never-taken Gradio fallback. The live UI is the FastAPI app. |
| `btc_5m_fv/connectors/registry.py` | WIRED | Built, but the live loop **bypasses** the registry/ABC architecture; the live feed is `connectors/chainlink_settlement.py` only. Its importer is the dead controller. |
| `btc_5m_fv/connectors/{base,binance,chainlink,polymarket}.py` | DEAD? | The ABC connectors behind the unused registry. Not on the live path. |
| `btc_5m_fv/execution/risk.py` (`RiskService`) | WIRED | Only importer is the **dead** `btc_5m_fv/ops/controller.py`. The LIVE risk check is `execution/gate.py:RiskGate`. |
| `btc_5m_fv/execution/paper.py` (`PaperExecutionManager`) | WIRED | Live paper fills are journaled **inline in `btc_bot/paper.py`**, not via this class. |
| `btc_5m_fv/ops/controller.py` (`BotController`) | DEAD? | The live controller is `btc_bot/controller.py`. |

Genuine **DEAD?** (no importers at all): `btc_5m_fv/backtest/{conditional,harness}.py`, `connectors/{base,binance,chainlink,polymarket}.py`, `ops/controller.py`, `ops/dashboard/panels/_shared.py`, `storage/replay.py`, `strategy/signal.py`, `btc_bot/chronos_signal.py`.

## Live trading is built and gated

Live exists (`execution/live.py:LiveExecutor`). It activates ONLY with `BTC_BOT_MODE=live` AND `BTC_LIVE_CONFIRM=YES_I_UNDERSTAND` AND a private key AND a coherent wallet AND a clean config parse. **Agents never flip the gate. The operator launches.**

Env knobs: `BTC_TRADE_*` are canonical; `BTC_LIVE_*` are deprecated read-aliases.

## Module status (generated)

> The inventory `Status`/`Importers` columns are **mechanical** (direct non-test importer
> count) — read the "WIRED ≠ live" callouts above for semantic truth. `pkg` (package marker)
> and `cli` (entrypoint script) statuses with zero importers are normal, not dead.

<!-- BEGIN GENERATED:summary -->
- **Trees:** `btc_bot/` = live loop + signal math; `btc_5m_fv/` = execution/connectors/dashboard/backtest; top-level `config.py`/`db.py`/`logging_setup.py` = foundation. Both ACTIVE, bidirectionally coupled.
- **Entry:** `python main.py` → FastAPI `btc_5m_fv/ops/dashboard/app.py`; loop starts on operator ▶ Start → `btc_bot/controller.py:request_start`.
- **Tests:** 624.
- **Built-but-dead (do not edit expecting runtime effect):** `btc_5m_fv/backtest/conditional.py`, `btc_5m_fv/backtest/harness.py`, `btc_5m_fv/connectors/base.py`, `btc_5m_fv/connectors/binance.py`, `btc_5m_fv/connectors/chainlink.py`, `btc_5m_fv/connectors/polymarket.py`, `btc_5m_fv/ops/controller.py`, `btc_5m_fv/ops/dashboard/panels/_shared.py`, `btc_5m_fv/storage/replay.py`, `btc_5m_fv/strategy/signal.py`, `btc_bot/chronos_signal.py`.
<!-- END GENERATED:summary -->

<!-- BEGIN GENERATED:inventory -->
| Module | Status | Importers | Role |
|---|---|---|---|
| `btc_5m_fv/__init__.py` | pkg | 0 | BTC 5m Binary Fair Value trading system. |
| `btc_5m_fv/backtest/__init__.py` | pkg | 0 | Backtesting harness and metrics. |
| `btc_5m_fv/backtest/conditional.py` | DEAD? | 0 | Conditional backtest — evaluates strategy on historical user trades. |
| `btc_5m_fv/backtest/harness.py` | DEAD? | 0 | Full-market backtest harness. |
| `btc_5m_fv/backtest/metrics.py` | WIRED | 1 | Backtest metrics, friction models, and reporting data classes. |
| `btc_5m_fv/connectors/__init__.py` | pkg | 0 | Exchange and data connectors. |
| `btc_5m_fv/connectors/base.py` | DEAD? | 0 | Re-export abstract base classes and exceptions for connector authors. |
| `btc_5m_fv/connectors/binance.py` | DEAD? | 0 | Binance connector — BTC spot price and recent close history. |
| `btc_5m_fv/connectors/chainlink.py` | DEAD? | 0 | Chainlink Data Streams connector stub. |
| `btc_5m_fv/connectors/chainlink_settlement.py` | WIRED | 1 | Settlement-aligned Chainlink BTC/USD feed via Polymarket endpoints (issue #21). |
| `btc_5m_fv/connectors/polymarket.py` | DEAD? | 0 | Polymarket connector — discovers the current BTC 5-minute binary market window. |
| `btc_5m_fv/connectors/registry.py` | WIRED | 1 | Connector registry — manages the lifecycle and discovery of all connectors. |
| `btc_5m_fv/core/__init__.py` | pkg | 0 | Core domain types, interfaces, and exceptions. |
| `btc_5m_fv/core/exceptions.py` | WIRED | 4 | Custom exception hierarchy for the BTC 5m Binary Fair Value trading system. |
| `btc_5m_fv/core/interfaces.py` | WIRED | 10 | Abstract base classes for all pluggable system components. |
| `btc_5m_fv/core/types.py` | WIRED | 10 | All domain types and enums for the BTC 5m Binary Fair Value trading system. |
| `btc_5m_fv/execution/__init__.py` | pkg | 0 | Paper and live execution managers. |
| `btc_5m_fv/execution/gate.py` | WIRED | 4 | Venue-independent pre-trade risk gate (issue #64). |
| `btc_5m_fv/execution/live.py` | WIRED | 6 | Live execution on the Polymarket CLOB via py-clob-client. |
| `btc_5m_fv/execution/paper.py` | WIRED | 1 | Paper execution manager — explicit order lifecycle with SQLite persistence. |
| `btc_5m_fv/execution/risk.py` | WIRED | 1 | Venue-independent risk service — pre-trade and post-trade risk controls. |
| `btc_5m_fv/ops/__init__.py` | pkg | 0 | Operator controls and telemetry. |
| `btc_5m_fv/ops/controller.py` | DEAD? | 0 | Unified bot controller — tick loop using execution manager + risk service. |
| `btc_5m_fv/ops/dashboard/__init__.py` | pkg | 0 | FastAPI dashboard for BTC 5m Binary Fair Value trading system. |
| `btc_5m_fv/ops/dashboard/app.py` | WIRED | 2 | FastAPI dashboard for BTC 5m Binary Fair Value trading system. |
| `btc_5m_fv/ops/dashboard/ems.py` | WIRED | 1 | EMS view orchestrator (#37). |
| `btc_5m_fv/ops/dashboard/panels/__init__.py` | pkg | 1 | Dashboard panels. |
| `btc_5m_fv/ops/dashboard/panels/_data.py` | WIRED | 1 | Read-only SQLite loaders for dashboard panels. |
| `btc_5m_fv/ops/dashboard/panels/_shared.py` | DEAD? | 0 | Shared rendering primitives for dashboard panels. |
| `btc_5m_fv/ops/dashboard/panels/blotter.py` | WIRED | 1 | Trade blotter: open positions on top, last 12 closed below, mode chip per row. |
| `btc_5m_fv/ops/dashboard/panels/controls.py` | WIRED | 1 | Operator runtime controls (#50, #89): live-editable risk knobs. |
| `btc_5m_fv/ops/dashboard/panels/decision_engine.py` | WIRED | 1 | Decision engine panel: inputs → computation → gates → final banner + tail. |
| `btc_5m_fv/ops/dashboard/panels/guardrails.py` | WIRED | 1 | Risk guardrails: daily spend, loss-halt, bot state, recent BLOCKED entries. |
| `btc_5m_fv/ops/dashboard/panels/market.py` | WIRED | 1 | Live market panel: probability gauge, UP/DOWN book, basis. |
| `btc_5m_fv/ops/dashboard/panels/performance.py` | WIRED | 1 | Performance / alpha panel: combined equity curve + LIVE/PAPER mini-cards. |
| `btc_5m_fv/ops/dashboard/panels/ribbon.py` | WIRED | 1 | Top status ribbon: mode/state pills, daily PnL split, feed liveness chips. |
| `btc_5m_fv/ops/dashboard/panels/strategy.py` | WIRED | 1 | Strategy panel: active params, proposed-vs-applied delta, calibration. |
| `btc_5m_fv/ops/dashboard/panels/tca.py` | WIRED | 1 | TCA panel: quoted spread, half-spread, edge capture, Brier calibration. |
| `btc_5m_fv/ops/incidents.py` | WIRED | 1 | Incident state machine and operator runbooks for the BTC 5m FV system. |
| `btc_5m_fv/ops/telemetry.py` | WIRED | 1 | Feed health telemetry and latency tracking for the BTC 5m FV system. |
| `btc_5m_fv/storage/__init__.py` | pkg | 0 | Persistence layer — database, recording, and replay. |
| `btc_5m_fv/storage/recorder.py` | WIRED | 2 | Market data recorder — persists raw market snapshots to SQLite for deterministic replay. |
| `btc_5m_fv/storage/replay.py` | DEAD? | 0 | Deterministic replay — feed recorded market data through a signal generator. |
| `btc_5m_fv/strategy/__init__.py` | pkg | 0 | Signal generation module — fair value, sizing, and signal composition. |
| `btc_5m_fv/strategy/fair_value.py` | WIRED | 2 | Fair-value probability and volatility estimation. |
| `btc_5m_fv/strategy/signal.py` | DEAD? | 0 | Signal composition — bridge raw edge into a fully typed :class:`Signal`. |
| `btc_5m_fv/strategy/sizing.py` | WIRED | 1 | Position sizing derived from signal confidence and strategy parameters. |
| `btc_bot/__init__.py` | pkg | 6 | BTC 5-minute paper-trading package. |
| `btc_bot/adaptive.py` | WIRED | 2 | Adaptive risk controller (#36): edge-decay auto-pause + calibration. |
| `btc_bot/backtest.py` | WIRED | 5 | Backtest and optimize the BTC 5-minute binary strategy on local history. |
| `btc_bot/calibration.py` | WIRED | 3 | Probability calibration for the side-relative model output (#37). |
| `btc_bot/calibration_fit.py` | cli | 0 | Fit the side-relative probability calibrator from the closed-trade journal. |
| `btc_bot/chronos_signal.py` | DEAD? | 0 | Layer 3 — Chronos time-series ensemble (stub). |
| `btc_bot/controller.py` | WIRED | 2 | Start/stop controller for the BTC 5-minute trader (paper default, live opt-in). |
| `btc_bot/history.py` | WIRED | 3 | Load the user's exported Polymarket history for BTC sizing context. |
| `btc_bot/paper.py` | WIRED | 5 | BTC 5-minute trading engine (paper by default, live opt-in). |
| `btc_bot/params.py` | WIRED | 5 | Active strategy parameters (Layer 2 — operator-gated auto-tune). |
| `btc_bot/params_apply.py` | cli | 0 | Layer 2 — operator-gated promotion of proposed -> active strategy params. |
| `btc_bot/params_propose.py` | cli | 0 | Layer 2 — propose tuned strategy parameters from the existing backtest grid. |
| `btc_bot/shadow/__init__.py` | pkg | 6 | Shadow forward-tester: candidate strategies logged and settled net of fees. |
| `btc_bot/shadow/fees.py` | WIRED | 2 | Polymarket taker-fee math for the shadow forward-tester. |
| `btc_bot/shadow/ledger.py` | WIRED | 2 | Persistence for the shadow forward-tester's would-be trades. |
| `btc_bot/shadow/runner.py` | WIRED | 5 | Shadow forward-tester runner. |
| `btc_bot/shadow/signals.py` | WIRED | 1 | Candidate strategies for the shadow forward-tester. |
| `btc_bot/shadow/types.py` | WIRED | 3 | Shared data contracts for the shadow forward-tester. |
| `btc_bot/strategy.py` | WIRED | 5 | Shared BTC 5-minute binary strategy math. |
| `config.py` | WIRED | 22 | Configuration for the local BTC 5-minute binary fair-value strategy lab. |
| `dashboard.py` | WIRED | 1 | Local Gradio dashboard for BTC 5-minute paper trading. |
| `db.py` | WIRED | 13 | SQLite storage for the BTC 5-minute binary fair-value strategy lab. |
| `logging_setup.py` | WIRED | 9 | Structured JSON logging with structlog. Module + trade_id context. |
| `main.py` | cli | 0 | Entrypoint for the BTC 5-minute paper trading system. |
| `tools/backtest_btc_strategy.py` | cli | 0 | Run the BTC strategy backtest and parameter optimizer. |
| `tools/chainlink_lead_lag.py` | cli | 0 | Chainlink-vs-Binance BTC lead-lag analysis (issue #57). |
| `tools/clear_auto_pause.py` | cli | 0 | Clear the adaptive auto-pause and resume entries (#36). |
| `tools/demo_snapshot.py` | cli | 0 | Print a BTC paper trading snapshot. |
| `tools/fetch_polymarket_trades.py` | cli | 0 | Pull this account's Polymarket trade history via the CLOB API → CSV. |
| `tools/gen_docs.py` | cli | 0 | Generate the machine-derived sections of the agent docs. |
| `tools/live_detect_wallet.py` | cli | 0 | Detect the Polymarket funder wallet + signature type from a signer key (#34). |
| `tools/live_preflight.py` | cli | 0 | Live-launch preflight: verify the .env wallet config end to end (issue #32). |
| `tools/live_setup.py` | cli | 0 | One-time live-trading onboarding (issues #32, #33). |
| `tools/offline_replay.py` | cli | 0 | Offline replay of the BTC 5-m fair-value strategy on HF Polymarket data. |
| `tools/shadow_performance.py` | cli | 0 | Per-model performance comparison for the shadow forward-tester. |
<!-- END GENERATED:inventory -->

See `docs/FILE_MAP.md` for the full generated index.
