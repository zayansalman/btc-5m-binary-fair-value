# #85 — Live trades 100% blocked: runtime max-trade cap below Polymarket share minimum (2026-06-16)

**Issue:** #85. **Branch:** `feature/85-runtime-cap-min-floor` off `develop`.

## Problem
Dashboard BLOCKED panel: every entry rejected — `size 1.78 shares below Polymarket minimum 5.00 at price 0.5600`.
Operator set runtime **Max trade size = $1.00**. At favourites (price ≥ 0.50), $1.00 buys < 2 shares < Polymarket's
**5-share venue minimum** → `LiveExecutor.submit_entry` (`live.py:662`) correctly refuses every order. Bot is STOPPED, funds untouched.

## Root cause
No floor enforced on the per-trade cap (the #50 slice shipped without one):
- `POST /api/runtime-config` validates only `0 < value <= 1000` (`app.py:710`).
- HTML input hardcodes `min='0.5'` (`controls.py:36`); the "min $5.00" hint is cosmetic.
- Gate read accepts any `value > 0` (`gate.py`).
v0.4.5 (#50) CHANGELOG states the flawed assumption: *"a value below min gives a smaller fixed clip — no `min` changes needed."*
False — a clip below the min-trade size sizes every order below the venue minimum.

## Invariant
**Effective per-trade cap ≥ min-trade size (`BTC_PAPER_MIN_TRADE_USD`).** The cap is the sizing-range ceiling;
`notional_from_confidence` clamps to `[min, max]`, so a ceiling below the floor forces every order below the floor.

## Tasks (TDD)
- [ ] Failing tests: endpoint rejects sub-floor / accepts at floor; gate treats stored sub-floor override as invalid → env default.
- [ ] Pin `BTC_PAPER_MIN_TRADE_USD` in existing cap tests (they silently depended on the local `.env` floor=5).
- [ ] `gate.py`: stored override `< floor` treated as invalid → `None` in `refresh_runtime_limits` + `get_runtime_max_trade_usd` (heals the live bot's stale $1.00).
- [ ] `app.py`: reject `value < floor` with an actionable error.
- [ ] `controls.py`: HTML `min='{min_trade}'` so widget + hint agree.
- [ ] Full suite green; CHANGELOG v0.4.6; lessons.md; push to develop.

## Not in scope
#83 (backtest leak) / #84 (signal overfit) — those question live edge; this only unblocks order placement.

## Review (2026-06-17)
**Done.** Invariant enforced: effective per-trade cap ≥ `BTC_PAPER_MIN_TRADE_USD`.
- `gate.py`: `_runtime_override_or_none()` drops a non-positive / sub-floor stored override → env-default fallback; wired into `refresh_runtime_limits()` + `get_runtime_max_trade_usd()`. **Auto-heals the live bot's stale $1.00 on the next tick** — no migration, no manual DB edit.
- `app.py`: `POST /api/runtime-config` rejects sub-floor values with an actionable error.
- `controls.py`: HTML `min` now tracks the displayed floor.
- TDD: wrote failing tests first (RED → GREEN). Full suite **544 green (+3)**; ruff clean; zero new mypy errors on changed files.
- **Immediate operator action** (one of): in the dashboard set Max trade size back to ≥ $5.00 (or clear the override), then Start. After deploying this fix, the stale $1.00 self-heals on the first tick regardless.

---

# Plan — UI-settable max trade size (2026-06-16)

**Issue:** #50 (runtime config controls — the max-trade-size slice only).
**Branch:** `feature/50-runtime-max-trade-size` off `develop`.
**Scope (confirmed):** ONE runtime-settable **unified max trade size**, editable from the dashboard, read **every tick** (no restart). Position mode / multi-position is OUT — leave singleton, revisit later.

**Current behaviour:** operator `.env` has `BTC_PAPER_MIN_TRADE_USD=5`, `BTC_PAPER_MAX_TRADE_USD=5`, `BTC_LIVE_MAX_TRADE_USD=5` → fixed $5 clip. Goal: reduce/increase that $5 from the UI.

## Decisions
- New runtime knob `btc_runtime.max_trade_usd` in the `config` table, read every tick, **runtime → env** fallback (unset = current behaviour, fully backward-compatible).
- When set it governs BOTH the sizing ceiling (`_strategy_params().max_trade_usd`) and the gate per-trade cap (`effective_max_trade_usd`) → unified. `notional_from_confidence` already clamps to `[min,max]`, so values below min give a smaller fixed clip; above min re-enable confidence-scaled sizing — no `min` changes needed.
- UI follows the existing panel architecture: pure `render()` panel in `panels/`, data via `ems.py`, POST endpoint + `refreshAll`, theme CSS vocab. No bespoke UI.
- Singleton enforcement, `EntryRequest`, `GateConfig` position fields, `LiveExecutor` scalar state: **untouched**.

## Tasks
### 1. Gate (`btc_5m_fv/execution/gate.py`)
- [ ] `_runtime_max_trade_usd` state + `refresh_runtime_limits()` (both modes) reading `btc_runtime.max_trade_usd`.
- [ ] Properties `runtime_max_trade_usd` (raw override) + `effective_max_trade_usd` (override else `cfg.max_trade_usd`).
- [ ] `block_reason`: per-trade cap uses `effective_max_trade_usd`.
- [ ] Module helpers `set_runtime_max_trade_usd` / `get_runtime_max_trade_usd` + key const + validation.

### 2. Loop (`btc_bot/paper.py`)
- [ ] `paper_tick_once`: `await _risk_gate.refresh_runtime_limits()` each tick (both modes).
- [ ] `_strategy_params()`: `max_trade_usd = gate.runtime_max_trade_usd if set else BTC_PAPER_MAX_TRADE_USD`.

### 3. Dashboard (respect existing panel architecture)
- [ ] `POST /api/runtime-config` (`{key:'max_trade_usd', value}`): validate (0 < v ≤ 1000), persist via gate setter, audit via `notify`.
- [ ] `panels/controls.py` CONTROLS card: current max (operator vs env), number input + Apply. Built for mode/positions to be added later.
- [ ] `ems.py`: load current value + render the card in the grid. `strategy.py`: show effective max.
- [ ] `dashboard.js`: `setMaxTradeSize()` (confirm → POST → toast → refreshAll). `style.css`: `.ctl-row`/`.ctl-input` matching theme.

### 4. Tests + ship
- [x] gate: effective override set/cleared; `refresh_runtime_limits` fallback; per-trade cap uses effective; set/get round-trip + validation.
- [x] dashboard: endpoint persists + validates; controls panel renders.
- [x] `pytest tests/` green (503, +15 new); my changed files add zero new `ruff`/`mypy` errors (pre-existing develop debt untouched).
- [x] README/CHANGELOG note. Commit + push to **develop** (never main).

## Review (2026-06-16)
**Done.** Operator can now set the unified max trade size from the dashboard CONTROLS card; the loop reads `btc_runtime.max_trade_usd` every tick (paper + live), no restart. Unset = prior behaviour.

- **Gate** (`gate.py`): `refresh_runtime_limits()` + `runtime_max_trade_usd`/`effective_max_trade_usd`; `block_reason` uses the effective cap; `set/get_runtime_max_trade_usd` helpers.
- **Loop** (`paper.py`): per-tick refresh; `_strategy_params()` honours the override for the sizing ceiling. One knob = sizing ceiling + gate cap (unified).
- **Dashboard**: new `panels/controls.py` CONTROLS card, `POST /api/runtime-config` (validated, audited), `setMaxTradeSize()` JS, `.ctl-*` theme CSS, STRATEGY sizing line override-aware — all following the existing panel architecture.
- **Verified**: 503 tests green (+15). Browser round-trip confirmed: set $3 → card "operator", STRATEGY "$3/clip", out-of-range rejected; restored to env default $5 after.
- **Out of scope (deferred):** singleton/multiple mode + max positions + LiveExecutor multi-position refactor — left intact for later.

---

# (Superseded) Plan — singleton/multiple position mode + max live positions
Deferred at operator request (2026-06-16): keep singleton for now; revisit multi-position + the LiveExecutor scalar→map refactor later. Full design preserved in session history / memory.

---

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
