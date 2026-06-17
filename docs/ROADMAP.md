# Engineering Roadmap

This roadmap keeps the project useful as a local BTC paper-trading tool while
moving it toward the engineering shape expected of serious trading systems.

## Current Strengths

- Narrow market scope: BTC 5-minute Up/Down only.
- Explicit operator controls: Start, Stop, Refresh, activity feed.
- Local paper ledger: ticks, simulated positions, exits, config state, and
  notifications are persisted in SQLite.
- Basic risk rules: bounded $1-$5 paper sizing, one open position, late-window
  skip, target/stop/time exits.
- Failure visibility: loop/feed/market errors are surfaced in logs and
  dashboard state.

> Several earlier roadmap items have since shipped and were removed from this
> list: the market-data recorder (`btc_5m_fv/storage/recorder.py`), the
> full-market replay + backtest harness (`btc_5m_fv/storage/replay.py`,
> `btc_5m_fv/backtest/harness.py` — built, though not yet wired into the live
> tooling; see `docs/BACKTESTING.md`), feed/latency telemetry
> (`btc_5m_fv/ops/telemetry.py`), incident states (`btc_5m_fv/ops/incidents.py`
> + `docs/OPERATIONS_RUNBOOK.md`), the dedicated-wallet live executor
> (`btc_5m_fv/execution/live.py`), and CI with deterministic fixtures
> (`.github/workflows/ci.yml`). What remains below is genuine future work.

## Priority Buildout

1. **Order Lifecycle Simulator**

   Model paper orders as separate acknowledgement, fill, partial-fill, cancel,
   exit, and reconciliation events. This keeps the paper system structurally
   close to the live executor without adding live risk.

2. **Risk And PnL Console**

   Add realized/unrealized PnL, exposure, inventory, drawdown, win/loss by
   market window, and stop-reason attribution. Keep risk metrics visible in
   both dashboard and CLI snapshots.

3. **Research-To-Production Boundary**

   Separate signal research from execution state. A new signal should be
   testable in replay before it is allowed in the live paper loop. The
   human-gated params propose/apply flow (`btc_bot/params_propose.py`,
   `btc_bot/params_apply.py`) is a first step; wiring the full-market harness
   into that loop is the remaining work.

## Later, Explicitly Reviewed

- CLOB quote integration with freshness checks.
- Chainlink Data Streams as primary reference input.
- Position and balance reconciliation against venue state.
- Remote monitoring only after private-key handling is isolated.
