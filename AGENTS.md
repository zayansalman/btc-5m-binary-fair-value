# Agent Instructions — BTC 5m Binary Pricing Model

> This file is the agent constitution for **both Codex and Claude Code** (and any
> other coding agent). It is the single source of scope and rules for this repo.

## START HERE

- **Where to make what change:** read **[docs/CODE_MAP.md](docs/CODE_MAP.md)** first.
  It is the routing doc — "I want to change X → edit Y" — and it explains the
  two-tree structure below.
- **Two coupled code trees, both LIVE:**
  - **`btc_bot/`** — the live trading loop + signal math (`paper.py:run_paper_loop`
    is *the* loop; `strategy.py` is the live signal math).
  - **`btc_5m_exec/`** — execution gates, live CLOB executor, connectors, FastAPI
    dashboard, recorder, backtest harness.
  - They are **bidirectionally coupled**: the dashboard imports `btc_bot.*`;
    `btc_bot` imports back into `btc_5m_exec.{execution,connectors}`. Top-level
    `config.py` / `db.py` / `logging_setup.py` are the shared foundation.
- **Machine-generated facts** (module inventory, wired-vs-dead status, test count)
  live in **[docs/FILE_MAP.md](docs/FILE_MAP.md)** and in `<!-- GENERATED -->`
  blocks. They are kept fresh by `tools/gen_docs.py` (CI `docs-drift` job +
  `.claude` hooks) — **never hand-edit them.**

## Active Scope

This repository is a local BTC 5-minute binary pricing-model strategy lab.

The primary active product behavior is:

1. Operator opens the local dashboard.
2. Operator presses **▶ Start**.
3. The bot paper trades BTC 5-minute Up/Down markets (default mode).
4. Operator presses **Stop** to halt new entries and close open simulated
   positions.

Live trading is also built and multi-gated (see the live rule below); it stays
off unless the operator explicitly arms every gate.

## Scope Fence (in scope / out of scope)

In scope:

- Discover current BTC 5-minute Up/Down Polymarket markets.
- **Research/shadow-only exploration is open by default** — any market, any
  timeframe, any venue instrument may be investigated, backtested, or shadow-run
  as long as it places no real orders. No fresh operator carve-out is needed to
  start a new research direction; the market/timeframe restriction below binds
  the **live trading path** only. Example: two-sided maker quoting across the
  venue's 5-minute Up/Down crypto family (btc/eth/sol/xrp/doge/bnb) —
  `btc_bot/pairarb/`, shadow only, places no orders (#182, widened 2026-08-14).
- Use a settlement-aligned BTC reference feed for signal and paper fills.
- Show the Chainlink Data Streams reference in the dashboard.
- Compute a fair Up probability and edge versus market price.
- Size trades between $1 and $5 by confidence.
- Persist every tick, position, exit, and dashboard event in SQLite.
- Provide dashboard Start, Stop, Refresh, activity feed, and summary metrics.
- Summarize the optional exported BTC Polymarket history CSV.
- Run a local trade-history conditional backtest and parameter grid optimizer.
- Present a concise systems scorecard covering scope, risk, feed discipline,
  auditability, and failure visibility.
- Maintain a public engineering roadmap focused on market-data recording,
  replay, order lifecycle, risk/PnL, telemetry, and deterministic tests.
- **Live order execution** on the Polymarket CLOB — built, multi-gated, and
  off by default. The operator (never an agent) arms and launches it.

Out of scope:

- Flipping the live gate or placing live orders on behalf of the operator.
- Any market other than BTC 5-minute Up/Down, **on the live trading path**
  (real capital). Non-BTC markets and other timeframes are in scope for
  research/shadow work — see the research/shadow-only line above.
- Remote deployment / exposing the dashboard beyond localhost by default.

## Absolute Rules

- **Live trading (real capital) is BTC 5-minute Up/Down only.** Research and
  shadow-only work on other markets/timeframes is in scope by default — see
  Scope Fence above.
- One open BTC paper position at a time.
- **Live trading is BUILT and multi-gated** (`btc_5m_exec/execution/live.py:LiveExecutor`).
  It runs only with `BTC_BOT_MODE=live` **AND** `BTC_LIVE_CONFIRM=YES_I_UNDERSTAND`
  **AND** a private key **AND** a coherent wallet. **Agents NEVER flip the gate or
  place live orders; the operator launches.** Default is paper.
- Do not read, print, log, commit, echo, or expose private keys.
- The dashboard must stay local by default at `127.0.0.1:7860`.
- Start means trade (paper unless every live gate is armed); Stop means stop.
- Current optimized paper profile keeps a 4.5 percentage-point edge floor,
  uses a 60-second late-entry cutoff, and sizes $1-$5 by confidence.
- No silent failures. Feed, market, state, or execution-loop errors must appear
  in structured logs or dashboard state.
- Keep modules small and boundaries clear.
- Keep public docs vendor-neutral and focused on trading-system quality:
  observability, risk control, feed discipline, persistence, and operator
  control.

## Code Conventions

- Python 3.11.
- Async I/O with `httpx` and `aiosqlite`.
- The live dashboard is a **FastAPI (uvicorn) app** at
  `btc_5m_exec/ops/dashboard/app.py`. The top-level Gradio `dashboard.py` is a
  **dead, never-taken fallback** (`HAS_NEW_DASHBOARD` is always true) — do not
  treat it as the live UI.
- `structlog` JSON logs.
- SQLite for local paper ledger and dashboard state.
- Prefer explicit, boring safety over cleverness.

## Running Locally

```bash
./.venv/bin/python main.py
```

This boots the **FastAPI dashboard** (uvicorn serving
`btc_5m_exec/ops/dashboard/app.py`), not Gradio.

Dashboard:

```text
http://127.0.0.1:7860
```

Optional snapshot:

```bash
./.venv/bin/python tools/demo_snapshot.py
```

## Live module status (generated)

<!-- BEGIN GENERATED:summary -->
- **Trees:** `btc_bot/` = live loop + signal math; `btc_5m_exec/` = execution/connectors/dashboard/backtest; top-level `config.py`/`db.py`/`logging_setup.py` = foundation. Both ACTIVE, bidirectionally coupled.
- **Entry:** `python main.py` → FastAPI `btc_5m_exec/ops/dashboard/app.py`; loop starts on operator ▶ Start → `btc_bot/controller.py:request_start`.
- **Tests:** 822.
- **Built-but-dead (do not edit expecting runtime effect):** `btc_5m_exec/backtest/conditional.py`, `btc_5m_exec/backtest/harness.py`, `btc_5m_exec/connectors/base.py`, `btc_5m_exec/connectors/binance.py`, `btc_5m_exec/connectors/chainlink.py`, `btc_5m_exec/connectors/polymarket.py`, `btc_5m_exec/ops/controller.py`, `btc_5m_exec/ops/dashboard/panels/_shared.py`, `btc_5m_exec/storage/replay.py`, `btc_5m_exec/strategy/signal.py`, `btc_bot/chronos_signal.py`.
<!-- END GENERATED:summary -->
