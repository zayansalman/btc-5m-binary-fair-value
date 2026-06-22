# Design — Regime-aware Down-Skeptic (`down_skeptic_drift_v6`) + roster trim

**Issue:** #100 · **Branch:** `feature/100-down-skeptic-drift-v6` · **Date:** 2026-06-22

## 1. Why

The live bot bled on a one-sided book. Accounting of the last 15 live trades
(`btc_paper_positions` where `mode='live' AND state='closed'`):

- **15/15 were Up. 6 W / 9 L. Net −$9.71 on $39.89 staked (−24.3% ROI).**

Root cause is structural, not variance:

1. The active model `down_skeptic_v4` vetoes a **Down** pick unless its edge clears
   `entry_edge_min + 0.02` (a fixed toll to fight v0's ≥-tie structural Up bias).
   Up is never tolled → the model bets **Up almost exclusively**.
2. The regime flipped **bearish** over the window — Down bets won 8/9 (89%) while Up
   bled. The fixed Down toll leaned the book hardest into the losing side.
3. At ~0.53 entries the payoff is asymmetric: a win pays +$0.47/share, a loss costs
   −$0.53/share, so break-even win rate is **53%**. The realized 40% compounds fast.

`down_skeptic_v4` is the strongest performer in flat/up regimes and `cushion_drift_v5`
barely trades — so the fix is to make the **down-skeptic itself regime-aware**, not to
switch models.

## 2. The model — `down_skeptic_drift_v6`

A faithful extension of `down_skeptic_v4`: reuse v0's side selection
(`strategy.signal_from_executable_edges`), then gate by an edge toll — but the toll
**flexes with regime** instead of being a fixed one-sided Down penalty. The regime
primitive is reused verbatim from `cushion_drift_v5` (no new fitted scale):

```
drift = view.drift_per_second
sigma = view.sigma_per_second
regime = 0.0 if (drift is None or not sigma or sigma <= 0) \
         else _clamp((drift / sigma) / regime_full_scale, -1.0, +1.0)   # bullish +, bearish −

down_extra = down_edge_premium * _clamp(1.0 + regime, 0.0, 2.0)
up_extra   = down_edge_premium * _clamp(-regime,      0.0, 1.0)

if side == "Up"   and edge < params.entry_edge_min + up_extra:   return None
if side == "Down" and edge < params.entry_edge_min + down_extra: return None
```

Defaults: `down_edge_premium=0.02` (v4's value), `regime_full_scale=0.3` (v5's value).

| Regime | `up_extra` | `down_extra` | Behaviour |
|---|---|---|---|
| **0** (neutral / no drift feed) | 0 | 0.02 | **byte-for-byte identical to v4** — its exact control |
| **−1** (full bear) | 0.02 | 0 | toll flips onto Up; Down is free → suppresses the losing-Up bleed |
| **+1** (full bull) | 0 | 0.04 | strengthens the Up lean |

**Invariant (the elegance):** at `regime=0`, or whenever the drift feed is unavailable,
v6 reduces to v4 exactly — mirroring how v5 reduces to v2 at `k=0`. So v4 remains v6's
clean statistical control. v6 never *forces* a side; v0 still chooses, v6 only gates.

## 3. Roster wiring (decouple "selectable" from "logged")

Today `MODEL_IDS = list(_MODELS.keys())` drives **both** the shadow-logging set **and**
the operator selector dropdown (`controls.py:78`) **and** the switch allow-list
(`app.py:766`). "Hide from selector, keep logging" requires splitting these.

`btc_bot/shadow/runner.py`:

- `_MODELS` (shadow-logging, every tick): **add** `down_skeptic_drift_v6`; **remove**
  `late_convergence_v3`. Keeps `fair_value_v0`, `cushion_favorite_v2` (silent controls),
  `down_skeptic_v4`, `cushion_drift_v5`, `down_skeptic_drift_v6`.
- `CANDIDATE_SIGNALS` (live dispatch): **add** `down_skeptic_drift_v6`; **remove**
  `late_convergence_v3`.
- `MODEL_LABELS` / `MODEL_DESCRIPTIONS`: **add** v6 (`"Down-Skeptic · Regime Drift"` /
  `"v4 but the edge toll flexes with drift/σ momentum: bearish → Up is tolled, bullish → Down"`);
  **remove** `late_convergence_v3`.
- **New** `SELECTABLE_MODELS: list[str] = ["down_skeptic_v4", "cushion_drift_v5", "down_skeptic_drift_v6"]`
  — the curated operator-facing set. `MODEL_IDS` stays = the logged set.

`btc_5m_fv/ops/dashboard/panels/controls.py:78`: iterate `SELECTABLE_MODELS` (not
`MODEL_IDS`). Guard: if the current `active_model` is not in `SELECTABLE_MODELS`, still
render it as an option so the operator never sees a blank/orphaned selection.

`btc_5m_fv/ops/dashboard/app.py:766`: validate the incoming `active_model` against
`SELECTABLE_MODELS` (the dropdown is the only entry point).

## 4. Go-live posture

**Shadow-first.** v6 logs alongside the live model from the moment it registers. The
agent does **not** flip `btc_model.active`. `down_skeptic_v4` stays live until the
operator selects v6 from the dropdown, ideally after a validation window confirms v6
suppresses the Up bleed in a bearish regime.

## 5. Tests (TDD)

`tests/unit/test_shadow_signals.py`:

- `regime == 0` (drift/σ small) ⇒ v6 returns the **same decision as v4** on the same view (parametrize Up & Down).
- drift feed missing (`drift_per_second is None`) ⇒ identical to v4.
- **bear** regime (drift/σ strongly negative): a thin-edge **Up** pick that v4 takes is **vetoed** by v6.
- **bull** regime (drift/σ strongly positive): a thin-edge **Down** pick that v4 takes is **vetoed** by v6.
- v6 still defers side selection to `signal_from_executable_edges` (no side forcing).
- Remove the `late_convergence_v3` cases.

`tests/unit/test_shadow_runner.py`:

- `down_skeptic_drift_v6` present in `_MODELS`, `CANDIDATE_SIGNALS`, `MODEL_LABELS`, `MODEL_DESCRIPTIONS`.
- `late_convergence_v3` absent from all four.
- `SELECTABLE_MODELS == ["down_skeptic_v4", "cushion_drift_v5", "down_skeptic_drift_v6"]`; `fair_value_v0`/`cushion_favorite_v2` still in `_MODELS` (logged) but not in `SELECTABLE_MODELS`.

`tests/unit/test_dashboard.py`:

- Selector renders only the three `SELECTABLE_MODELS` options; switching to a non-selectable id is rejected by `app.py`.

## 6. Out of scope (separate issue)

DB/panel reconciliation against real Polymarket fills — the zero-fee assumption,
assumed-fill-price-as-limit, and the four divergent live-PnL numbers on screen
(+8.92 / +6.98 / −2.75 / −7.30). Tracked separately; not touched here.
