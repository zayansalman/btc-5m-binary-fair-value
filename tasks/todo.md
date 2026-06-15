# Plan — Unified RiskGate: paper as a faithful preview of live (2026-06-15)

## Goal

Paper and live share **one gate stack**. What paper does is what live will do.
Live-only is the actual order submission. The user wants to trust paper as a
live preview before flipping `BTC_BOT_MODE=live`.

## Why this is needed

After #61 removed the daily bankroll cap default, paper and live STILL diverge.
Today (2026-06-15) the operator switched paper→live→paper because every $5 live
entry from 11:10–11:30 was BLOCKED by the (then-active) bankroll cap; the same
signal opened cleanly as paper rows at 11:57 and 12:00. Even with that cap off,
4 other gates still produce silent divergence:

| # | Gate | Paper | Live | Lives in |
|---|---|---|---|---|
| 1 | Bankroll cap | none | `BTC_LIVE_BANKROLL_CAP_USD` (opt-in, off now) | `LiveExecutor.entry_block_reason` |
| 2 | Kill-switch file | ignored | blocks + cancels resting | `LiveExecutor.kill_switch_active` |
| 3 | Daily realized-loss halt | ignored | `≤ -BTC_LIVE_DAILY_LOSS_HALT_USD` ($10 default) | `LiveExecutor` + persisted `btc_live.*` |
| 4 | Per-trade USD cap | `BTC_PAPER_MAX_TRADE_USD` | `BTC_LIVE_MAX_TRADE_USD` (different env knob) | strategy params vs gate |
| 5 | Slippage guard | none | `BTC_LIVE_MAX_ENTRY_SLIPPAGE` ($0.02) | `LiveExecutor.submit_entry` |
| 6 | Singleton check | open ledger row only | open row OR resting unfilled order | `LiveExecutor` |

## Implementation plan

### Phase 1 — Extract venue-independent gate
- [ ] New `btc_5m_fv/execution/gate.py::RiskGate`
  - Move `entry_block_reason` body + the daily counter machinery
    (`record_realized_pnl`, `_roll_daily_window`, `_load_risk_state`,
    `_persist_risk_state`) out of `LiveExecutor` and into the new class.
  - Counters keyed on a generic prefix (`btc_risk.*`) not `btc_live.*`.
  - `LiveExecutor` holds a `RiskGate` instance (composition over inheritance)
    and delegates; live also has the slippage guard, which it applies on top
    of the gate's verdict using the live book at submit time.

### Phase 2 — Wire the gate into the paper path
- [ ] `btc_bot/paper.py::_maybe_open_position` calls `gate.block_reason(...)`
      before opening any row. Blocked entries are journaled to a new
      `btc_paper_blocked` lightweight log (or reuse `btc_live_orders` with a
      `mode='paper'` column — pick whichever the user prefers).
- [ ] Slippage guard in paper: re-quote the book just before "filling" and
      apply `BTC_TRADE_MAX_ENTRY_SLIPPAGE` against the snapshot signal price.
      Same threshold as live.
- [ ] Singleton check in paper picks up the resting-order parity by virtue of
      sharing the gate.

### Phase 3 — Paper closes feed shared counters
- [ ] On every paper position close, call `gate.record_realized_pnl(pnl)` so
      the daily-loss halt advances identically across modes.
- [ ] On every paper entry, call `gate.record_buy_notional(notional)` so the
      bankroll cap (when set) advances identically.
- [ ] Counter rollover at UTC 00:00 already works — no change there.

### Phase 4 — One knob per concept
- [ ] `BTC_TRADE_MAX_USD` becomes the canonical per-trade ceiling.
- [ ] `BTC_TRADE_MAX_ENTRY_SLIPPAGE` becomes the canonical slippage cap.
- [ ] Old `BTC_PAPER_*` / `BTC_LIVE_*` knobs read as deprecated aliases for one
      release; startup logs a WARN when only the old name is set.
- [ ] Persisted `btc_live.{risk_date,daily_realized_pnl,daily_buy_notional}`
      renamed to `btc_risk.*` with a one-time read-and-migrate on boot.

### Phase 5 — Tests, dashboard, runbook
- [ ] `tests/unit/test_risk_gate.py`: 10-row table-driven fixture covering
      kill switch, daily-loss halt, bankroll cap (when set), per-trade cap,
      slippage, singleton, settle-style one-per-window. Asserts the SAME
      decision in paper and live for every row.
- [ ] Dashboard: new line "Next entry: would BLOCK — <reason>" or "OK"
      sourced from `gate.block_reason(...)` with the same params the next
      entry would use. Operator never needs SQL to see gate state.
- [ ] `docs/OPERATIONS_RUNBOOK.md`: update "going live" section — paper now
      already enforces every live gate, so a clean paper session is the live
      readiness signal.

### Phase 6 — Ship
- [ ] Branch from develop: `feature/<issue#>-unified-risk-gate`.
- [ ] `pytest -q`, `ruff check`, `mypy` all green.
- [ ] Commit + push to develop. Operator merges develop→main.

## Out of scope

- Changing gate VALUES — only the wiring changes.
- Adaptive auto-pause (#36) — already symmetric across modes, nothing to do.
- Replacing `RiskService` in `btc_5m_fv/ops/controller.py` — that controller
  is not on the live path; can be reconciled separately.

## Acceptance

1. With paper running on today's signal, paper-side BLOCKED rows match the
   reasons the same minute would emit in live (re-run the 11:10–11:30 window).
2. All 10 unit-test rows show paper == live decision.
3. Dashboard shows the live-equivalent gate verdict for the next entry, refreshed every tick.

## Won't do without explicit operator sign-off

- Filing the GitHub issue (waiting on review of this plan first).
- Touching `main`.

---

# (Historical) Adaptive layer + go live (2026-06-14)

Preflight GO: MetaMask key in, Gnosis Safe funder `0xc1Daa…` holds $37.86, sig
type 2, wallet already approved (max allowances). Hard caps in code: $5/trade,
1 position, -$10/day halt, $30/day cap, kill switch.

## "Leverage AI" — the honest split
- NOT a price predictor (loses to latency bots on 5m BTC).
- YES adaptive risk control + an AI research analyst over OUR OWN journal.

## Phase 1 — Adaptive risk controller (#36, build now, protects from trade 1)
- [x] btc_bot/adaptive.py: rolling expectancy / win-rate / Brier calibration
      over the last N closed trades of the active style.
- [x] Auto-pause when rolling ROI drops below a floor — sticky until cleared.
- [x] Config + entry-path integration + journal/dashboard + clear tool. Tests.

## Phase 2 — AI research loop (design now, build once live fills accumulate)
- [ ] docs/RESEARCH_LOOP.md: nightly agent mines journal → proposes filters →
      backtests OOS on the recorded archive → surfaces survivors for operator
      approval. AI proposes, human disposes. Never auto-applies to live.

## Phase 3 — Go live
- [x] Final preflight GO → start live bot → verify boot gate + first real entry.
      Live ran 2026-06-15 07:07–08:31 (7 fills, +$7.78 realized).

## Won't do
- RL auto-tuning on live $37 (overfits/blows up — sample far too small).
- Any live-param change without OOS validation + operator sign-off.

---

# (Historical) Live Executor Build — Issue #20 (2026-06-10)

## Plan
- [x] Fix runtime blockers (#19): main.py import, Binance endpoint, backtest retries
- [x] Paper bot running end-to-end (dashboard :7860, ticks in SQLite)
- [x] Backtest grid running on April history (background)
- [x] Implement live execution mode (py-clob-client) with hard risk limits
- [x] Adversarial review: financial-risk, API correctness, safety gates
- [x] Fix findings, tests green (395 passing)
- [x] Push to develop
- [x] Operator (Zayan) provides POLYMARKET_PRIVATE_KEY + BTC_LIVE_CONFIRM and launches live

## Review

Adversarial review (3 reviewers, 22 findings: 4 critical / 8 major / 10 minor)
— all addressed on this branch:

- **Persisted daily risk counters** — daily loss halt + daily bankroll cap now
  live in SQLite (`config` table) and are reloaded at executor start.
- **No false closes** — a blocked/failed/unfilled live exit keeps the ledger
  row OPEN and retries next tick.
- **Exit lifecycle** — exit SELLs are tracked, awaited, cancelled on timeout.
- **Stop race eliminated** — controller waits for the runner thread.
- **Boot reconciliation** — cancel_all + journal-based re-adoption.
- **Kill switch** — re-arms on file deletion, TOCTOU re-check before entry POST.
- **Boot gate hardening** — funder required for signature types 1/2.
- **Misc** — entry slippage guard, ledger-before-submit ordering for entries.

## Data-integrity build (#21/#22/#23) — 2026-06-11
- [x] CLOB executable quotes in signal path + honest fills
- [x] Chainlink settlement connector (REST + WS) + REST spot-poll fallback
- [x] Tie-rule fair value; degraded-feed entry/exit gating
- [x] WS feed lifecycle wired into run loop
- [x] KPI re-baseline: pre-clob rows excluded
- [x] Dashboard state derived from runner thread both directions (#23)
- [x] 16 new tests; full suite green

## Execution plan — soak to live gate (2026-06-11, issues #25-#27)

Soak started 2026-06-11 05:28Z on the honest baseline.

### Phase 1 — let it soak (no action)
- [x] 4-6h runtime, target 100+ closed clob-baseline trades

### Phase 2 — quality review (#25)
- [x] PnL/win/drawdown by exit reason and entry-time bucket
- [x] Churn analysis; per-window cooldown spec (#27)
- [x] Fill-realism haircut on expectancy
- [x] Gamma-vs-CLOB staleness stats

### Phase 3 — stability + ops (#26)
- [x] Tick-gap/crash audit; WS 429 recovery check
- [x] CI green on develop
- [x] Kill-switch drill in paper mode

### Phase 4 — live gate (operator decision)
- [x] Present haircut-adjusted expectancy verdict
- [x] If GO: operator sets POLYMARKET_PRIVATE_KEY etc. (done 2026-06-15)

### Backlog (non-blocking)
- [ ] Backtest harness on Chainlink data instead of Binance
- [ ] develop -> main merge (requires explicit operator approval)
- [ ] Evaluate beta polymarket-client SDK migration
