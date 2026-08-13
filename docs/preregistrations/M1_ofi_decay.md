# Pre-registration — M1: the OFI horizon-decay curve

**Issue:** #180 · **Status: FROZEN as of this commit.** Committed before the first byte of
measurement data was downloaded. Nothing below moves after data arrives. Any deviation must
be recorded in this file as a dated amendment *before* the deviating run, with its reason.

## Question

Does any order-flow signal retain enough predictive content at the 1-hour or daily horizon
to clear the cost bar of Polymarket's corresponding up/down market? This decides whether
Strategy 1 (Accumulator) has any live rung ([STRATEGY_DESIGN.md](../STRATEGY_DESIGN.md)
§4, §11). It is the cheapest test that can kill the largest remaining part of the design.

> **Superseded 2026-08-13** — read with the amendment log. The Accumulator is parked by
> operator decision (2026-08-12), so M1 no longer decides whether it has a live rung; M1 is
> drift-nuisance calibration for the Sigma Gap that retains kill authority over Strategy 1
> through its 1h leg alone (2026-08-13).

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

> **AMENDED 2026-08-13 — the daily conjunct is withdrawn.** The text above is left as
> published. The kill condition of record is now: **KILL Strategy 1 entirely if fewer than
> 1% of 1h decision times clear 0.0689.** The daily rung is still fitted, scored and
> reported, but it no longer carries kill authority — under the null it clears its own bar
> 19–27% of the time, and the phantom-tilt guard below foreclosed a daily verdict for this
> span in either direction. No numeric threshold moves. See the dated amendment below and
> [CORRECTIONS.md](../CORRECTIONS.md) C12.

**Additionally required for a pass** (guards against phantom tilt): per-fold sign
consistency of each surviving β across walk-forward folds ≥ 75%, and the surviving cells'
`φ(0)·√(k/N_eff)` phantom-tilt bound must be < half the cost bar, else the cell is
reported as "unmeasurable", not "pass".

> **CLARIFIED 2026-08-13 — "half the cost bar" means half the `|βᵀx|` bar** (0.03445 at 1h,
> 0.0282 at daily), not half the cost stack. The phrase was ambiguous between the two and
> the readings differ by ~6× in the N_eff they demand. The clarification is recorded rather
> than silently applied because it is a reading of a frozen phrase, and it is the *looser*
> of the two readings — it can only make a cell easier to report as measured. Under either
> reading the daily cell is unmeasurable for the scored span and the 1h cell is measurable
> by more than an order of magnitude; the choice therefore changes no verdict here.

## What a pass does and does not mean

A pass promotes exactly one next step: the forecast-encompassing regression against
recorded venue prices ([STRATEGY_DESIGN.md](../STRATEGY_DESIGN.md) §11). It does not
authorize trading, sizing work, or any strategy build. A kill closes Strategy 1 at every
rung this venue lists and is recorded in [CORRECTIONS.md](../CORRECTIONS.md)'s companion,
the killed-hypotheses table.

> **AMENDED 2026-08-13.** Both consequences now rest on the 1h leg alone, which makes each
> of them a stronger claim from a single reading — stated here so that strength is not
> discovered after the fact. A 1h kill closes Strategy 1 at **every** rung, daily included,
> on the ground that the daily rung cannot be measured on this span at all rather than on
> the ground that it was tested and failed. A 1h pass promotes only the encompassing
> regression, and under the 2026-08-12 purpose demotion it promotes it as nuisance
> calibration: no M1 outcome revives the Accumulator, which is parked by operator decision.

---

## Amendments (dated, per the amendment rule above; no numeric threshold has ever moved)

**2026-08-12 — Purpose demotion.** The Accumulator is parked (operator decision; see
STRATEGY_DESIGN header). M1's role changes from "decides whether Strategy 1 has any live
rung" to **drift-nuisance calibration for the Sigma Gap**: its fitted tilt magnitudes
feed the trend-filter design and quantify the drift contaminant in the σ-inversion. The
kill condition, features, fit spec, scored window and stated prediction are unchanged.

**2026-08-12 — Pause and control anomaly.** The scored run has not begun. During dev-month
pipeline verification the positive controls (short-horizon flow→return ICs) came out
negative where the literature says positive. Second review attributes this to the
**trade-price bounce artifact**: forward returns anchored on last-trade prices, where the
anchor trade is itself the final trade of the flow window. Remedy before any scored run:
compute short-horizon control returns mid-to-mid from the Binance `bookTicker` archive,
not from trade prints. This is a control-plumbing fix; the scored metric (settlement-based
outcomes) is unaffected.

**2026-08-12 — N_eff correction.** The in-code phantom-tilt guard used per-decision-time
counts; decision times within one settlement window share an outcome, overstating N_eff
by up to the windows-per-outcome factor (~17× at daily). N_eff = **distinct settlement
windows**, with cluster-robust weighting in the fit. `tools/m1_ofi_decay.py` to be
corrected before the scored run.

**2026-08-12 — Scope note.** Program of record is now BTC/ETH; the roster extension to
other assets (incl. HYPE's short archive) is deferred and will be its own dated amendment
if revived.

**2026-08-13 — The daily leg is withdrawn from the kill condition.** Operator decision D2,
before any scored run, while the run is paused on the bounce artifact. **No numeric
threshold moves.** Kill authority moves to the 1h leg alone, on two independent grounds:

1. *The daily leg cannot fire.* Under the null, the frozen ridge prior at N_eff ≈ 743 daily
   settlement windows gives a null tilt RMS ≈0.043–0.051, which clears 0.0564 in 19–27% of
   decision times. The leg fails only below 5%, so it reports "survive" against its own null
   essentially always; the conjunction's power is of order 5%.
2. *It cannot deliver a legitimate pass either.* The phantom-tilt guard needs N_eff > 1,601
   at daily (≈3.9 yrs of BTC/ETH) against 743 available (1.8 yrs) — 10,060 under the strict
   reading. The cell is reportable only as "unmeasurable", which is what §11's own
   sample-size accounting already said, in contradiction with the kill condition above it.

Recalibrating the daily bar to a null quantile was rejected: it freezes a threshold chosen
*after* seeing its null, and ground 2 would leave the leg unmeasurable anyway.

The 1h leg is unaffected — it clears the guard by >10× (N_eff ≈ 17,800 vs 1,073 needed).
Daily is still fitted, scored and reported as measurement (the drift-nuisance calibration
the 2026-08-12 demotion made M1's job), labelled "unmeasurable" where the guard says so.
`tools/m1_ofi_decay.py` to be corrected before the scored run.
