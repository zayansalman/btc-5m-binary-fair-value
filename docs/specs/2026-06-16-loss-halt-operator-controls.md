# Loss halt: operator-controlled bypass + reset; halt on live leg only

- **Issue:** #76
- **Branch:** `feature/76-loss-halt-operator-controls` (off `develop`)
- **Date:** 2026-06-16

## Problem

The daily realized-loss halt sums **paper-study P&L into the live halt** (issue #67
"parity via SUM"). On 2026-06-16 live P&L is **−$5.00** — inside the −$10.00 limit —
but the bot is halted because the combined tally is **−$27.47** (paper −$22.47 dragged
it under). The operator cannot resume live trading:

- the live `RiskGate` is built with `allow_overrides=False`, so the persisted bypass
  flag is **structurally ignored** for real money (`bypass_loss_halt` property returns
  `False` in live), and
- the dashboard bypass toggle is **disabled** in live ("safety gate enforced — cannot
  disable").

Additionally, the `BYPASS` pill shown in live is misleading: it reflects the raw stored
flag from a paper-study run, but the live gate ignores it (which is why the bot still
halted).

## Operator decisions (2026-06-16)

1. **Halt basis = live leg only.** Live halts on real-money P&L; paper halts on study P&L.
2. **Two one-click controls, both modes, no confirm dialog** (every action is journaled).
3. **Reset is stopped-only** — disabled while running; the endpoint rejects when running.
4. **Remove the live-lock invariant** — bypass/reset affect real money in live. **No hard
   floor.** Accepted explicitly; the operator owns the bot and the stake.

## Design

### 1. Halt on the per-mode leg (`execution/gate.py`)

`RiskGate` gains an explicit `is_live: bool` (defaults `False`). The halt decision in
`block_reason` compares the **mode's own leg** against the threshold instead of the sum:

```
halt_pnl = self._live_pnl if self.is_live else self._paper_pnl
if not self.bypass_loss_halt and halt_pnl <= -self.cfg.daily_loss_halt_usd:
    return f"daily loss halt: {'live' if self.is_live else 'paper'} realized {halt_pnl:+.2f} USD breaches -{threshold:.2f} USD"
```

The split counters (`_live_pnl`, `_paper_pnl`) and `record_realized_pnl(..., is_live=)`
are unchanged — both legs are still tracked and persisted; only the *comparison* changes.
`daily_realized_pnl` (combined) is retained as a reporting/back-compat surface.

- `execution/live.py:222` → `RiskGate(gate_cfg, is_live=True)`.
- `btc_bot/paper.py:324` → `build_gate_from_config()` (is_live defaults `False`).
- `build_gate_from_config(*, is_live=False)`.

### 2. Bypass applies in both modes (`execution/gate.py`)

The bypass stops being a paper-only `allow_overrides` gate and becomes a runtime operator
knob — same shape as the existing per-trade-cap override (`_runtime_max_trade_usd`), which
already applies in both modes and is re-read every tick.

- Remove the `allow_overrides` field and its `bypass_loss_halt` guard. The property
  returns `self._bypass_loss_halt`.
- `refresh_overrides` reads `btc_risk.paper_bypass_loss_halt` unconditionally (no early
  return). The live loop already calls `refresh_overrides()` each tick via the shared gate
  (`paper.py:317,406`), so live picks up the toggle with no extra wiring.
- The persisted key name is left as-is to avoid a data migration; only the function names
  generalize: `set_loss_halt_bypass` / `get_loss_halt_bypass` (old names kept as thin
  aliases if any other caller exists — grep shows only the endpoint + ems).

### 3. Reset — zero today's tally, stopped-only (`execution/gate.py` + `app.py`)

Because the dashboard process and the loop process are separate and the loop holds
counters in memory, a live reset would race the loop's `persist()`. Per the operator's
choice, Reset is **stopped-only**, which removes the race entirely:

- New endpoint `POST /api/loss_halt/reset`:
  - Reads bot state; if `running`, returns `{"status":"error","detail":"stop the bot before resetting the halt"}` (server-side guard, not just a disabled button).
  - Else sets `btc_risk.live_realized_pnl` and `btc_risk.paper_realized_pnl` to `"0.0"`
    via `set_config`, journals to `notification_feed`, returns ok.
  - On the next Start, `RiskGate.load()` reads the zeroed split keys (date is today) → a
    clean window. Bankroll-cap notional (`btc_risk.daily_buy_notional`) is **left intact**
    so the daily spend cap stays honest.

### 4. Endpoints + audit (`app.py`)

- `POST /api/loss_halt/bypass` (replaces `/api/paper/bypass_loss_halt`): sets the flag,
  journals `"Operator <enabled|disabled> loss-halt bypass (paper+live, runtime)"`.
- `POST /api/loss_halt/reset`: as above.
- Both call `db.notify(...)` so there is an audit trail (the operator chose no confirm
  dialog; journaling is not friction).

### 5. UI (`ops/dashboard/panels/guardrails.py` + `ems.py`)

- The `STATUS` value becomes a **clickable pill-button** that toggles bypass
  (`OK`/`HALTED` → click disables the halt; `BYPASS` → click re-enables). Works in both
  modes — the live-disable branch and "cannot disable" hint are removed.
- A **Reset** button is added next to it, `disabled` when `state == "running"` with a
  title explaining "stop the bot to reset"; posts to `/api/loss_halt/reset`.
- **Headroom is leg-aware**: in live it is computed from `live_pnl`, in paper from
  `paper_pnl` (panel picks the leg from `mode`). The "Headroom (combined)" label becomes
  "Headroom" and the Paper P&L row tooltip in live mode reads "study — does not affect the
  live halt".
- `ems.py` keeps passing `live_pnl`, `paper_pnl`, `mode`, `state`; the leg selection
  happens in the panel.

### 6. Migration — clear the stale bypass flag

The persisted `btc_risk.paper_bypass_loss_halt` is currently `"1"` (a paper-study
artifact). After this change a `"1"` would disable the **live** halt on the next run. The
deploy must set it to `"0"` so live starts **halt-ON** by default. Implemented as a
guarded one-shot at boot: a sentinel key `btc_risk.bypass_migrated_v76` is checked; if
unset, clear `paper_bypass_loss_halt` to `"0"` and set the sentinel, so the clear happens
exactly once and a later deliberate bypass is never wiped. Documented in the runbook.

## Testing

- **gate**: live halt fires on `live_pnl` alone; paper losses do **not** halt live;
  paper gate still halts on `paper_pnl`.
- **gate**: bypass on → `block_reason` skips the loss-halt gate in **live** (regression
  guard against the old `allow_overrides=False` lock).
- **reset**: zeroing the split keys → `load()` yields a clean window; bankroll notional
  preserved.
- **endpoints**: `/api/loss_halt/reset` rejects when running, succeeds when stopped;
  `/api/loss_halt/bypass` flips the flag and journals.
- **panel**: Reset button disabled when `state=="running"`; headroom uses the live leg in
  live mode.

## Out of scope

- Hard real-money floor (operator declined).
- Tick-applied (running-safe) reset (operator chose stopped-only).
- Changing the kill switch, bankroll cap, or slippage gate.

## Process

GitHub issue #76 → this branch off `develop` → tests + build green → push to `develop`.
**Not** merged to `main` without operator approval. `FILE_MAP`/GENERATED blocks
regenerated via `tools/gen_docs.py` (never hand-edited).
