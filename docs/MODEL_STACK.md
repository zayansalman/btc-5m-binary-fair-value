# Model stack — every model, what it calculates, and where it comes from

**Status: design document, nothing implemented.** Companion to
[STRATEGY_DESIGN.md](STRATEGY_DESIGN.md) (thesis, market selection, strategies) and
[FACTORS.md](FACTORS.md) (inputs and how they are computed).

Every model below is a named, published standard. Nothing here is invented for this
project. Each row carries a provenance marker:

| Marker | Meaning |
|---|---|
| **reuse** | An implementation exists in `~/projects/quant-model-library` and works as-is |
| **adapt** | Right model, wrong data granularity — the library implementation targets daily bars and needs intraday inputs |
| **build** | No implementation exists anywhere in reach; must be written against the named standard |

Library paths below are relative to `~/projects/quant-model-library`. Repo paths are
relative to this repository.

---

## 1. Vola engine — estimates σ̂, which sets the scale for everything else

σ̂ is the denominator of the entire pricing model. Over-estimate it and the model never
sees an opportunity; under-estimate it and the model trades confidently into the fee. A
lagging σ̂ was ranked the most likely single cause of the v1.0.0 model's failure.

| Model | What it calculates | Mechanism | Provenance | Status |
|---|---|---|---|---|
| **EWMA** | One-step-ahead variance | `σ²ₜ = λσ²ₜ₋₁ + (1−λ)r²ₜ₋₁` — smooth exponential decay, no cliff | RiskMetrics (J.P. Morgan, 1994) | **reuse** — `src/volatility/ewma.py` |
| **HAR-RV** | Next-window realized vola from three horizons | OLS on short / medium / long realized-vola components, weights learned walk-forward | Corsi (2009) | **adapt** — `src/volatility/har_rv.py`; its RV proxy is squared *daily* returns (documented limitation), we have true intraday RV |
| **GARCH(1,1)** | Mean-reverting variance forecast | `σ²ₜ = ω + αr²ₜ₋₁ + βσ²ₜ₋₁`; ω gives vola a long-run home, so forecasts decay toward it | Bollerslev (1986) | **reuse** — `src/volatility/garch.py` |
| **QLIKE loss** | Which vola forecast is better | `r²/σ̂² − ln(r²/σ̂²) − 1`; punishes under-prediction far harder than over-prediction | Patton (2011) | **reuse** — `ewma.py::qlike_loss` |

**Design notes.**

- λ = 0.94 is a **daily-bar** constant and must not be copied. Memory half-life is
  `ln2 / (−ln λ)`: at 1-second bars λ=0.94 gives ~11 seconds of memory. λ is selected on
  our own data, walk-forward.
- **EGARCH / GJR (asymmetric GARCH) are rejected on evidence, not taste.** The library's
  own BTC run found the leverage asymmetry weak and wrong-signed
  (`src/volatility/asymmetric_garch.py`). Crypto is not equities; there is no leverage
  effect here to model. EWMA's symmetric treatment of return sign is therefore correct.
- GARCH's likely failure mode: at second-scale data fitted persistence tends to α+β ≈ 0.99,
  at which point it behaves like EWMA while being harder to maintain. It races anyway;
  QLIKE decides.
- Selection is by out-of-sample QLIKE. No model ships on reputation.

## 2. Regime classifier — decides which playbook page is active

Three distinct questions, no single model answers all three.

| Question | Model | What it calculates | Provenance | Status |
|---|---|---|---|---|
| Which regime? Will it persist? | **Gaussian HMM** | Soft `P(state)` per bar **and** `E[duration] = 1/(1−p_stay)` from the transition matrix | Baum–Welch; standard systematic-fund tooling | **adapt** — `src/time_series/hmm.py` (features must move to intraday) |
| Did the world just change? | **BOCPD** | Online posterior over run length; collapses within a few bars of a break. Also emits a break-aware causal vola forecast | Adams & MacKay (2007) | **reuse** — `src/regime_detection/bayesian_changepoint.py` |
| Baseline both must beat | **Frozen vola-percentile bands** | Regime label from trailing realized-vola percentile | — (no model) | **build** (trivial) |

**Division of labour.** HMM is slow by construction — regime switches are low-probability
events in its transition matrix. BOCPD is fast by construction — it is built to detect
resets. The Accumulator needs `E[duration]` to justify holding across many windows; that
number exists nowhere else in the stack. BOCPD's alarm does three things at once: flatten
the Accumulator, arm the Scalper, and **reset the vola engine's memory** — the direct fix
for the stale-σ̂ failure mode, since EWMA otherwise averages across a regime break for
minutes afterward.

**Markov-switching AR is rejected on engineering grounds, not statistical ones.** The
statsmodels implementation has no online update; out-of-sample inference requires a refit
per step (documented in `src/regime_detection/markov_switching.py`). That is fine in a
research notebook and unusable inside a live loop, and it would duplicate the HMM's job.

**Two traps documented in the library's BOCPD module, worth restating** because they are
the standard first mistakes: (1) `P(run length = 0)` is algebraically pinned to the hazard
rate under a constant hazard and **cannot** be used as a changepoint score — use the
collapse of the run-length posterior mode; (2) overlapping rolling-RV windows make naive
persistence a mechanically inflated baseline.

**Frozen knobs** (set before any measurement, per the FDR discipline): number of HMM states
K = 3, BOCPD hazard rate, and the percentile band edges.

## 3. The prior — the null model everything must beat

| Model | What it calculates | Mechanism | Provenance | Status |
|---|---|---|---|---|
| **N(d₂) digital** | `P(Up)` with no directional knowledge | `z = ln(S/K)/(σ̂√τ)`, `P = Φ(z)`, plus tie mass | Black–Scholes in-the-money term; digital options per Reiner & Rubinstein (1991) | **build** (~30 lines; `btc_bot/strategy.py::fair_up_probability` is this model) |
| **Implied-σ inversion** | The σ the market believes | Same formula solved backwards from the observed price | — | **build** |

**Dropped terms, justified numerically rather than waved away.** Full `d₂` carries a rate
term and a `−σ²τ/2` convexity term. Over a 300-second window at 5% annual rates the rate
term is ~5×10⁻⁷; the convexity term shifts `z` by ~0.0006 standard units against typical
`z` of 0.1–1. Both are thousands of times below the signal. At these horizons the desk
formula does not need approximating — it collapses into this form.

**The driftless assumption is the design's hinge.** Setting μ = 0 encodes "nobody can know
the direction of the next few minutes." A strong 2%/day trend contributes ~0.007% of drift
per 5-minute window against ~0.15% of noise — about 1/20th of a standard deviation. This is
*why* the market prices close to this formula, and why v1.0.0 — which was this formula
alone — found nothing: it was the null testing itself against the null. The direction layer
amends exactly this term.

**Tie mass** is a venue-specific correction, not a textbook term: Polymarket credits
`close = open` to Up, and Chainlink prints are discrete (~2dp), so exact ties carry real
probability. Approximated as the normal density at the strike times one print-width. Its
structural consequence — fair value is strictly above 0.5 when spot equals reference — is
documented in the existing implementation's docstring.

## 4. Direction layer — the new content

`P(Up) = Φ(z + βᵀx)`. In distributional terms, flow shifts the centre of the outcome
distribution; `βᵀx` estimates the standardized drift `(μ/σ)√τ`. Fitting target is the
forward return divided by `σ̂√τ`, so one set of weights applies across vola regimes.

| Model | What it calculates | Mechanism | Provenance | Status |
|---|---|---|---|---|
| **Order-flow imbalance** | Net aggressor pressure over several lookbacks | Signed aggressor volume, normalized | Cont, Kukanov & Stoikov (2014) | **build** — library has only snapshot depth ratios |
| **Kyle's λ** | Expected price displacement per unit of net flow | Linear fit of Δprice on net signed flow | Kyle (1985) | **reuse** — `src/microstructure/kyle_model.py`, including its bucket-size sweep |
| **Boosting challenger** | Nonlinear `P(Up)` from `[z, x]` | Gradient boosting, expanding-window folds | Industry standard for tabular alpha | **reuse** — `src/ml/boosting.py` fold machinery, features swapped |
| **Isotonic calibration** | Corrects systematically mis-stated probabilities | Monotone regression of outcome on predicted probability | Standard | **build** |
| **OBI evaluation harness** | Walk-forward scoring for flow signals | Per-fold coefficient, OOS R², Spearman IC, hit-rate vs 0.5 | — | **reuse** — `src/microstructure/order_book_imbalance.py` (the *harness*, not its numbers) |

**Naming honesty.** Strict "OFI" in the Cont–Kukanov–Stoikov sense is computed from
order-*book* changes. What is specified here first is its tape-side sibling — **signed
aggressor-volume imbalance** — because its raw material (exchange trade streams with an
aggressor flag) exists as years of free downloadable history, which is what makes the
measurement program runnable immediately. Book-based OFI on the Polymarket CLOB is a later
refinement, not the starting point.

**The layer is self-disarming.** If flow predicts nothing, the fitted β goes to ≈0 and the
model collapses back to the null prior Φ(z). It can only add tilt to the extent history
demonstrates tilt existed.

**Structural model wins ties against the boosting challenger**, per issue #170's
legibility mandate: if the black box cannot beat the explainable model out-of-sample on
Brier, the explainable model ships.

## 5. Path layer — Scalper only

| Model | What it calculates | Mechanism | Provenance | Status |
|---|---|---|---|---|
| **Digital delta / gamma** | Ticket cents per dollar of spot | Derivative of Φ(z) — the density at the strike; grows as 1/(σ̂√τ) | Standard digital greeks | **build** (alongside §3) |
| **OFI burst z-score** | Whether flow is abnormal now | Current short-window imbalance vs its own trailing range | — | **build** (trivial) |
| **Ornstein–Uhlenbeck fit** | Reversion speed θ, fair level μ, **half-life** | Exact-discretization AR(1) on the displacement series | Vasicek / OU tradition | **reuse** — `src/stat_arb/ornstein_uhlenbeck.py` |
| **Hysteresis entry/exit bands** | Entry and exit thresholds without churn | z-score state machine; lookback derived from the fitted half-life rather than guessed | — | **reuse** — `src/stat_arb/mean_reversion.py` |
| **Tradeability filter** | Whether a displacement reverts fast enough to be worth trading | Reject if half-life exceeds the remaining window | Avellaneda & Lee (2010) s-score | **reuse pattern** — `src/stat_arb/pca_stat_arb.py` |

**The Scalper's full entry chain**, each link a measured quantity:

```
burst z-score          →   λ × flow          →   Δ × (λ × flow)      >?   4.5–5.5¢
(abnormal flow now)        (expected $ move)     (expected ¢ move)        (round-trip cost)
```

**Caveats.** Linear price impact is an approximation — impact is concave at large size
(square-root law) — but linear is the correct regime at our clip sizes. λ is unstable
across regimes and must be estimated rolling and per-regime. Gamma is a warning label as
much as an opportunity: in the high-delta zone exposure is unstable, so time-stops are
tight and positions exit before the final ~30 seconds, where strike-pinning plus tie mass
make holding a pure gamble.

## 6. Cross-venue checks — validating the data proxy

The measurement program uses exchange data to predict Chainlink-settled outcomes. That
proxy must be validated, not assumed.

| Model | What it calculates | Provenance | Status |
|---|---|---|---|
| **VAR + impulse response** | Cross-lag structure and how many ticks one series takes to absorb a shock in the other | Sims (1980) | **reuse** — `src/time_series/var_vecm.py` |
| **VECM error-correction half-life** | How fast a spread closes, and *which* leg does the adjusting | Johansen (1991) | **reuse** — `var_vecm.py::error_correction_half_life` |
| **ARIMAX vs ARIMA vs naive** | The *marginal* out-of-sample contribution of a leading variable | Box–Jenkins tradition | **reuse** — `src/time_series/arimax.py` |
| **Kalman filter (filtered, never smoothed)** | Latent fair value and its one-step innovation | Harvey | **reuse** — `src/stat_arb/kalman_filter.py`, `src/time_series/state_space.py` |

**Lookahead warning, restated from the library's own documentation:** scoring against the
*smoothed* state is silent lookahead — a live system only ever has the *filtered* estimate.
`src/time_series/state_space.py` quantifies the gap.

**Standing data rule:** exchange and Chainlink **returns** may be compared; **levels**
never (a persistent basis of roughly $50 exists between venues).

## 7. Costs, execution and sizing

| Model | What it calculates | Provenance | Status |
|---|---|---|---|
| **Venue fee model** | `0.07·p·(1−p)` per share, plus breakeven win rate `p + fee` | Venue-specific; verified against real fills | **reuse** — `btc_bot/shadow/fees.py` |
| **Implementation shortfall** | Delay / execution / opportunity / commission decomposition | Perold (1988) | **adapt** — `src/microstructure/implementation_shortfall.py`, to a probability-space book |
| **Flat sizing** | Constant clip | Kelly's own edge-degeneration result | **reuse as justification** — `src/portfolio_optimization/kelly_criterion.py` |

The implementation-shortfall **opportunity-cost term** is the one cost a naive maker
backtest omits: what an unfilled limit order costs inside a fixed window.

## 8. Evaluation — the largest build gap

The library scores by R², RMSE, QLIKE, Sharpe and AUC. For an instrument whose price *is* a
probability, none of those is the right primary ruler.

| Model | What it calculates | Provenance | Status |
|---|---|---|---|
| **Brier score** | Mean squared error of stated probabilities | Brier (1950) | **build** |
| **Log loss** | Penalty for confident wrongness | Standard | **build** |
| **Reliability diagram** | Whether "70%" events happen ~70% of the time | Murphy decomposition | **build** |
| **Walk-forward harnesses** | Expanding-window folds, train-only baselines, shift-lagged features | — | **reuse** — patterns across `src/ml/boosting.py`, `src/signal_generation/mean_reversion_breakout.py` |
| **Benjamini–Hochberg FDR** | Multiple-testing control across the regime family | Benjamini & Hochberg (1995) | **reuse discipline** — this repo's pre-registration pattern |

---

## Build list — the complete set of things that do not yet exist

1. N(d₂) digital pricing, its delta/gamma, and implied-σ inversion
2. Event-level order-flow imbalance from raw trade streams
3. Brier / log-loss / reliability / isotonic calibration kit
4. Exchange historical and live loaders (trades, funding, open interest, liquidations)
5. A cost simulator matching the venue's fee structure on a probability-space book

Everything else in this document already exists with a walk-forward harness attached.

---

## Cross-cutting warning about the library's own numbers

`src/synthetic.py` draws its order-book imbalance term and its price innovation
**independently**. Every microstructure demo number in that library is therefore null *by
construction* — the modules' own docstrings say so. Those files provide validated
*pipelines*, not evidence of any effect. All numbers must come from our data.
