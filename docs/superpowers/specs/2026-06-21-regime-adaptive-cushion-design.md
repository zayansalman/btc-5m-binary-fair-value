# Regime-aware adaptive cushion — design

**Issue:** #94 (Phase 1, this spec) · #95 (Phase 2 futures, follow-up)
**Date:** 2026-06-21
**Status:** approved → implementation

## Problem / hypothesis

`cushion_favorite_v2` (shadow forward-tester candidate) earns its lifetime P&L
almost entirely on the **Up** side; its **Down** picks are a coin flip. The
diagnostic is decisive — Down winners vs losers had statistically identical
entry price (0.551 vs 0.555) and model edge (0.0585 vs 0.0586): **the signal
carries no discriminating information on Down.**

The Up-bias is two parts:
1. **Structural** — Polymarket resolves ties (`spot >= reference`) to Up, so Up
   wins ~52% of windows (already priced by `down_skeptic_v4`).
2. **Regime** — BTC drifted up over the sample (Jun 18–21). In an up-trend any
   "long" wins regardless of signal quality, so cushion's positive P&L may be
   largely "BTC went up for three days."

**Killing the Down leg would hardcode the regime bias.** The fix is to make the
Up/Down decision *regime-aware and two-sided*: feed short-horizon directional
drift into an adaptive cushion threshold so Down is selective in up-trends and
permissive in down-trends, symmetrically.

## Mechanism — adaptive asymmetric cushion

```
regime  ∈ [−1, +1]   (bullish positive)
Up   bar_bps = clamp(base_bps − k·regime, floor_bps, base_bps + k)
Down bar_bps = clamp(base_bps + k·regime, floor_bps, base_bps + k)
take the v0-chosen side only if its favourable spot-vs-reference cushion ≥ bar
```

- `base_bps = 1.5` (v2's constant), `k` = sensitivity in bps, `floor_bps` keeps a
  strong regime from dropping a bar to zero (preserves the anti-pin-noise purpose).
- **Regime never forces a side.** v0 still selects the side from executable
  edges; regime only re-weights how much cushion each side must show. A large
  enough cushion still lets a Down through in an up-trend.
- **Clean nesting:** `k = 0` (or `regime = 0`, or drift unavailable) ≡ `v2`
  exactly. `v2` is the literal control; any divergence is attributable to regime.

## Regime feature — standardized momentum (Phase 1, spot only)

Signals are **pure functions of `SnapshotView`**, so the regime input is computed
upstream and added to the view. `PaperSnapshot` carries `sigma_per_second` (a
scalar derived from the feed's recent 1s closes) but not the raw closes, so drift
is computed the same way and stored as a scalar:

- New `drift_per_second(closes)` = **mean** of 1s log-returns — the directional
  twin of `sigma_per_second` (**stdev** of the same returns). `0.0` when < 2
  returns. Lives beside `sigma_per_second` in `btc_bot/strategy.py`.
- `regime = clamp((drift_per_second / sigma_per_second) / regime_full_scale, −1, +1)`
  — a **drift-to-vol ratio** (standardized momentum). Self-calibrating across
  volatility regimes; no hand-picked bps constant to overfit. `sigma_per_second`
  already has a floor, so no division-by-zero.

`regime_full_scale` maps the ratio to full strength. For N≈90 1s samples, a true
zero-drift mean is ~N(0, 1/√N)·σ ≈ 0.1σ, so a ~3-sigma directional move gives
drift/σ ≈ 0.3 → `regime_full_scale = 0.3` (a clearly directional 90s move = full
regime). This is the primary tunable.

### Default parameters (first-principles, not optimized)

| Param | Default | Rationale |
|---|---|---|
| `cushion_min_bps` (base) | 1.5 | v2's value → clean nesting |
| `k_bps` | 1.0 | full regime shifts a bar ±1.0bps → bar ∈ [0.5, 2.5] |
| `regime_full_scale` | 0.3 | ~3σ 90s move = full strength |
| `floor_bps` | 0.5 | a bar can never fall below the pin-noise floor |

## Components

- **`btc_bot/strategy.py`** — add `drift_per_second(closes) -> float`.
- **`btc_bot/paper.py`** — compute drift where sigma is computed (same closes);
  add `drift_per_second: float = 0.0` to `PaperSnapshot`; populate it in the
  `PaperSnapshot` and the live-dispatch `SnapshotView` construction sites.
- **`btc_bot/shadow/types.py`** — add `drift_per_second: float | None = None` to
  `SnapshotView` (default keeps existing constructors valid).
- **`btc_bot/shadow/runner.py`** — populate `drift_per_second` in `build_view`;
  register `cushion_drift_v5` in `_MODELS`, `MODEL_LABELS`, `MODEL_DESCRIPTIONS`,
  `CANDIDATE_SIGNALS`.
- **`btc_bot/shadow/signals.py`** — add `cushion_drift_v5(view, params, …)`.

## Data flow

feed closes → `drift_per_second` / `sigma_per_second` (paper.py) →
`PaperSnapshot.drift_per_second` → `build_view` → `SnapshotView.drift_per_second`
→ `cushion_drift_v5` computes regime → adaptive bar → `ShadowSignal | None` →
`record_shadow` logs to `btc_model_shadow_positions` (model_id `cushion_drift_v5`).

## Error handling

- `drift_per_second is None` or `sigma_per_second` falsy/≤0 → `regime = 0` →
  behaves exactly as `v2`. Never raises.
- Pure signal, no I/O; degrades silently to the control behaviour.

## Testing

- **`drift_per_second`**: known close series → known mean log-return; < 2 points → 0.0;
  flat series → 0.0; monotone up → positive, down → negative.
- **`cushion_drift_v5`** (pure, hand-built `SnapshotView`):
  - `drift=None` / `regime=0` ≡ `cushion_favorite_v2` on the same view.
  - Strong up-regime: a Down that v2 *took* is now vetoed (Down bar raised); an Up
    near the floor is now taken (Up bar lowered).
  - Strong down-regime: the symmetric opposite for Down.
  - Clamp/floor honoured; reason string carries cushion, bar, and regime.
- **runner**: `cushion_drift_v5` appears in the model registries and logs a row.

## Validation / success criteria

Judged in shadow, **stratified by BTC regime** (so we are not re-reading the
trend): Down win-rate > 55% (vs 50% today), Down ROI ≥ 0, total ROI ≥ `v2` and ≥
`down_skeptic_v4` — **without** the Up side carrying everything. Results are
provisional until the sample includes a down-trending regime.

## Out of scope (Phase 2, #95)

Binance futures connector for basis/funding; `cushion_basis_v6`, `cushion_regime_v7`.
Gated on v5 showing signal. Shipped as separate candidates for attribution.
