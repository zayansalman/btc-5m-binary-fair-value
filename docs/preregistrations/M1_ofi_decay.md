# Pre-registration — M1: the OFI horizon-decay curve

**Issue:** #180 · **Status: FROZEN as of this commit.** Committed before the first byte of
measurement data was downloaded. Nothing below moves after data arrives. Any deviation must
be recorded in this file as a dated amendment *before* the deviating run, with its reason.

## Question

Does any order-flow signal retain enough predictive content at the 1-hour or daily horizon
to clear the cost bar of Polymarket's corresponding up/down market? This decides whether
Strategy 1 (Accumulator) has any live rung ([STRATEGY_DESIGN.md](../STRATEGY_DESIGN.md)
§4, §11). It is the cheapest test that can kill the largest remaining part of the design.

## Stated prediction (for honesty, not part of the gate)

The test **fails**: aggressor-imbalance predictive content decays with a half-life of
seconds; what survives to 1h+ is permanent impact, already in the price. P(pass) ≤ 15%.

## Data (frozen)

- **Source:** Binance USDT-M futures `aggTrades` monthly archives from
  `data.binance.vision` (public, checksummed), plus 1m klines for returns and realized
  vola. Futures tape, not spot: the documented flow-information results are on the perp.
- **Assets:** BTCUSDT and ETHUSDT for the primary run. SOL/XRP/DOGE/BNB only if the
  primary run passes (generalisation is M3's job, not M1's).
- **Span:** 2024-07 through 2026-07 (24 months). Development months: 2024-07..2024-09 may
  be inspected freely while building the pipeline; they are **excluded from the scored
  run**. The scored window is 2024-10..2026-07, touched only by the frozen pipeline.
- **Settlement alignment:** forward returns computed on the same Binance series the venue
  settles on (1h candle open/close for the hourly rung; noon-ET 1m closes for daily,
  timezone `America/New_York`).

## Features (frozen)

Signed aggressor-volume imbalance `(V_buy − V_sell)/(V_buy + V_sell)` over trailing
windows of 1s, 10s, 60s, 300s, sampled at each decision time; plus trade-count imbalance
over the same windows. Decision times: every 5 minutes on the clock. All features
**orthogonalised against `z`** (the standardized current-window lead) before any fit —
`ln(S/K)` is the return this window's own flow caused, and skipping this step
manufactures β̂.

k = 8 features. No additions after this commit.

## Fit (frozen)

- Probit MLE of the settled direction on features, with `z` as a fixed offset.
- Ridge prior on β, scale set so the prior-implied per-trade Sharpe ≤ 2.0 annualized —
  the maximum credible for this signal class.
- Walk-forward: expanding window, refit monthly, forecasts strictly out-of-sample.
- Forward horizons: h ∈ {10s, 60s, 300s, 900s, 3600s, 86400s}. The short horizons exist to
  verify the pipeline reproduces the *known* result (flow predicts seconds-to-minutes);
  failure there means a bug, not a discovery.

## Metric and kill condition (frozen)

For each horizon, the fraction of scored decision times where the fitted out-of-sample
tilt satisfies `|βᵀx| > cost/φ(0)`:

| Rung | Cost stack | Bar on \|βᵀx\| |
|---|---|---|
| 1h | 2.75¢ | **0.0689** |
| daily | 2.25¢ | **0.0564** |

**KILL Strategy 1 entirely if BOTH:** fewer than 1% of 1h decision times clear 0.0689,
**and** fewer than 5% of daily decision times clear 0.0564. (Either rung individually
surviving keeps only that rung.)

**Additionally required for a pass** (guards against phantom tilt): per-fold sign
consistency of each surviving β across walk-forward folds ≥ 75%, and the surviving cells'
`φ(0)·√(k/N_eff)` phantom-tilt bound must be < half the cost bar, else the cell is
reported as "unmeasurable", not "pass".

## What a pass does and does not mean

A pass promotes exactly one next step: the forecast-encompassing regression against
recorded venue prices ([STRATEGY_DESIGN.md](../STRATEGY_DESIGN.md) §11). It does not
authorize trading, sizing work, or any strategy build. A kill closes Strategy 1 at every
rung this venue lists and is recorded in [CORRECTIONS.md](../CORRECTIONS.md)'s companion,
the killed-hypotheses table.
