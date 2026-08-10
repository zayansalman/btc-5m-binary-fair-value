# Strategy design — what we are trading, why, and on what evidence

**Program scope (operator decision, 2026-08-11): the 1h and daily markets only.** The 5m
and 15m rungs and the Scalper are out of scope entirely (§4, §6).

**Status: design document, nothing implemented.** This is step 2 of issue #170's process
(*discuss → write it down and confirm it makes sense on paper → only then test on samples*).
No claim in this document has been tested by the measurement program it specifies. Where a
number comes from the archived v1.0.0 research it is cited; where it comes from arithmetic
on a formula it is marked as such; where it is an untested hypothesis it says so.

**Terminology used throughout this repo:** **vola** = volatility. **vol** = volume.
Regimes are named *low-vola* / *high-vola*. This convention exists because the two words
are load-bearing in different layers of the model and "vol" is ambiguous between them.

Companion documents:
[MODEL_STACK.md](MODEL_STACK.md) (every model, what it calculates, reuse-vs-build) ·
[FACTORS.md](FACTORS.md) (every input, how it is computed, from where) ·
[FINDINGS.md](FINDINGS.md) (the archived negative result this design starts from).

---

## 1. What we are trading, and what the product actually is

Polymarket lists BTC binary Up/Down markets over a fixed window. The contract pays **$1 if
the Chainlink close print is ≥ the open print**, $0 otherwise (ties credit Up). Prices are
quoted in cents and are therefore *probabilities*: a ticket at 62¢ is the market asserting
a 62% chance of Up.

That has a consequence which shapes every decision below. **The product is not a price
prediction. It is a calibrated probability forecast.** You do not need to know where BTC
is going. You need your stated probability to be closer to the truth than the market's, by
more than the cost of trading. This is why the evaluation regime in this design is
Brier score and reliability — proper scoring rules for probabilistic forecasts — and only
secondarily PnL.

### The cost stack — the constraint everything else answers to

Per share bought at price `p`:

| Cost | Formula / size | At p=0.50 | At p=0.90 | At p=0.05 |
|---|---|---|---|---|
| Taker fee | `0.07 · p · (1−p)`, charged at entry regardless of outcome | 1.75¢ | 0.63¢ | 0.33¢ |
| Half-spread | you buy at ask, not mid | ~0.5–1¢ | ~0.5¢ | ~0.5¢ |
| Slippage | ≈0 at small clips; real beyond top-of-book | — | — | — |
| **Breakeven win rate** | `p + fee` (+ half-spread in practice) | **51.75%+** | **90.63%+** | **5.33%+** |

Two structural facts follow, and both drive design decisions later in this document:

1. **The fee is a parabola peaking exactly at coin-flip prices.** The venue charges most
   where uncertainty is highest. Mid-range taker trading is the most expensive place on
   the curve to operate.
2. **Maker pays no fee and earns the spread** — but inherits adverse selection. The v1.0.0
   research tested this and killed it (see §9); the sibling research folder's VPIN evidence
   for this market category corroborates it independently.

Fee formula source: `btc_bot/shadow/fees.py`, verified against real venue fills during the
July 2026 reconciliation (`docs/POSTMORTEM_2026-07.md`).

---

## 2. Ingredients — the observable inputs and what each one contributes

| Ingredient | Description |
|---|---|
| **Volatility (vola)** | Sets the fair anchor. Invertible: the binary's price implies a σ (run Φ backwards), so market-implied σ versus a realized-vola forecast is a direct, per-second *measurement* of vola mispricing rather than a guess about whether mispricing exists. |
| **Volume (vol)** | The classifier. A one-sided taker burst **with** a spot move indicates informed flow — stand aside. A burst **without** a spot move indicates noise flow pushing price off fair — the fade signal. |
| **Depth** | The amplifier. The same $200 of retail flow displaces a thin book far more than a thick one. Depth imbalance gives the expected displacement per unit of flow, and which side of the book is fragile. |
| **Market lag** | The clock. How long the binary takes to fully re-price after a spot jump. The first tick belongs to colocated participants — that race is lost and stays lost. Incomplete adjustment over a 5–30 second tail is the only capturable remnant. |
| **Kraken spot + perp** | Independent eyes. Cross-venue confirmation of a real move versus noise, plus funding and basis as a running gauge of which side the leveraged crowd is leaning — the direction from which retail overflow is likely to arrive. |

---

## 3. Signal families — the institutional direction toolkit, filtered

Short-horizon directional alpha is, in practice, flow alpha. Volatility is sign-free by
construction and never yields direction on its own; it sets the scale against which
directional information is measured. The families below are the standard institutional set,
assessed against our specific constraints (retail latency, the fee stack above, and the
horizons in §4).

| Signal family | Mechanism | Horizon | Survives our constraints? |
|---|---|---|---|
| **Order-flow imbalance (OFI) on the CEX tape** | Aggressive buyers lifting asks faster than sellers hit bids drift price up over the following seconds-to-minutes. The most documented short-horizon predictor in market microstructure. | secs–mins | **Yes — primary.** Free trade streams; predicts BTC, not the Polymarket book, so no colocation race. Untested by v1.0.0. |
| **Liquidation / forced flow** | A liquidated leveraged position *must* trade at market regardless of price — the only flow in the market that is guaranteed neither informed nor discretionary. Clusters cascade. | secs–mins, event-driven | **Yes — event-driven.** High signal during cascades, silent otherwise. |
| **Perp funding & basis** | Funding is a posted price for being on the crowded side; it measures where leverage (and therefore liquidation fuel) is stacked. | hours | **As regime context only.** Too slow to call a single window; standalone alpha long decayed. |
| **Cross-venue lead-lag** | Faster venues lead slower ones. | ms–secs | **No** for the first tick (needs 5–12ms colocation — falsified in v1.0.0 and corroborated externally). The 5–30s incomplete-adjustment tail is the only testable remnant, and is not in the current scope. |
| **Intraday momentum / reversal** | Continuation after flow-backed moves; reversal after flow-less spikes. | mins | Marginal alone; the with-flow vs without-flow split is the usable part, and it is subsumed by OFI. |
| **Time-of-day seasonality** | Funding resets, session opens and expiries create semi-deterministic flow. | calendar | Small, free; a conditioning variable, not a signal. |
| **Options dealer positioning (GEX)** | Dealer hedging pins or accelerates spot. | hours–days | **No** — too slow for the horizons in scope. |
| **News / scheduled macro** | A CPI or FOMC release inside a window. | event | **As a stand-down gate**, not a direction signal (see §10). |

---

## 4. Horizon selection — what each rung actually requires

This is the most consequential finding of the design phase, and it is arithmetic on the
fee formula rather than an empirical claim.

**The fee is a fixed toll per trade. Directional edge grows with √τ. They do not scale
together.** The tilt a sustained drift produces over a window is `(μ/σ)·√τ`, while the fee
`0.07·p·(1−p)` is charged once per entry regardless of window length.

### The ladder that actually exists

Verified against the venue's live catalogue and UI (2026-08-09). The up/down binary family
is listed at **four horizons — 5m, 15m, 1h, and daily** — for eight assets: BTC, ETH, SOL,
XRP, DOGE, BNB, HYPE, ZEC. (A `-4h-` slug family exists in the API as stale leftovers with
zero active events; it is not in the UI and is excluded.)

**Settlement source differs by rung, and it matters.** Per the market descriptions: the
hourly market resolves on the **Binance BTC/USDT 1-hour candle** (close ≥ open), and the
daily market on the **Binance 1-minute candle at 12:00 ET**, noon-to-noon — not on
Chainlink prints as the 5m/15m markets are. Exact comparison wording to be re-verified at
build time, but the consequence is major: **for the 1h and daily rungs, strike and
settlement are the same free Binance data the measurement program backtests on.** The
Chainlink-proxy concern applies only to the short rungs.

Observed book state at the time of survey:

| Horizon | Book depth | Spread | Traded volume | Windows/day (BTC) |
|---|---|---|---|---|
| 5 min | ~$2,500 | 1¢ | small | 288 |
| 15 min | ~$4,300 | 1¢ | small | 96 |
| **1 hour** | ~$4,700 | ~2¢ | **$10k–$33k per window** | 24 |
| **Daily** | **~$36,700** | 1¢ | large | 1 |

The hourly market's traded volume is the standout: $10k–$33k per window is organic flow,
not seeded maker quotes. The daily book is the deepest on the ladder by roughly 8×.

### The economics across the ladder — required Sharpe

> **Correction (2026-08-10).** An earlier version of this section ranked rungs by "cost as
> a % of achievable edge" (118 / 68 / 42 / 8). That column was not a measurement. It is
> algebraically `required_Sharpe ÷ 15.28`, where 15.28 is the annualized Sharpe of this
> document's own illustrative assumption (2%/day drift against 2.5%/day vola) — a
> preposterous forecaster. Dividing every rung by the same constant conveyed no information
> and made 42% read as a business. The column has been replaced by the quantity it was
> obscuring. See [CORRECTIONS.md](CORRECTIONS.md).

The digital's price sensitivity to a standardized drift at the strike is `φ(0) = 0.3989`.
So the tilt required to clear the cost stack is exactly `cost / φ(0)`, and the annualized
Sharpe that implies is that tilt × √(windows per year):

| Market | Cost stack | Required tilt per window | **Required annualized Sharpe** |
|---|---|---|---|
| **5 min** | 2.25¢ | 0.0564 | **18.3** |
| **15 min** | 2.25¢ | 0.0564 | **10.6** |
| **1 hour** | 2.75¢ (2¢ book) | 0.0689 | **6.46** |
| **1 day** | 2.25¢ | 0.0564 | **1.08** |

**This is the number that decides the project.** For reference, a sustained annualized
Sharpe of 2–3 on a liquid asset is elite-fund territory; published crypto time-series
momentum sits below 1. On that scale:

- 5m, 15m and **1h are not businesses at any credible signal strength.** A 1-hour BTC
  direction signal built from public data would need Sharpe 6.46 — roughly triple the best
  documented systematic programs, at the horizon where signal is weakest.
- **Only the daily rung (1.08) is inside the realm of the physically possible.**

The one valid inference the old column supported still holds, because it is one-sided: if
cost exceeds the edge a *generous* drift assumption can produce, the rung is dead
regardless of assumptions. That is why the 5m conclusion survives its own table.

**The unresolved tension this creates.** The only rung with achievable economics is daily,
and §4's own counterweight table states that this design's entire factor set — OFI,
liquidation flow — is worthless at daily horizons. **The design has selected rungs for
which it has no matching signal.** No Accumulator is built until [FACTORS.md](FACTORS.md)
contains at least one factor with a demonstrated multi-day predictive half-life.

This reproduces, from the fee formula alone, the conclusion the v1.0.0 research reached
empirically and the sibling research folder reached from market-structure evidence: the
5-minute directional lane is **structural, not tunable**.

### Counterweights — why the answer is not simply "trade the longest window"

| Constraint | Effect |
|---|---|
| **Signal horizon must match market horizon** | OFI predicts seconds-to-minutes: strongest at 5m, weakened but plausibly alive at 1h, worthless at daily. Daily needs genuinely slower signals (trend, positioning, macro). |
| **Verification time** | Per asset: 288 windows/day at 5m, 24 at 1h, 1 at daily. Across the eight listed assets: ~192/day at 1h and ~8/day at daily — both testable in weeks-to-months. Cross-asset samples are correlated, so effective sample size is materially lower than the raw count. |
| **Competition** | Forecasting a day of BTC attracts far more skilled capital than forecasting five minutes. Cheaper toll, harder game. |
| **Window overlap** | Each 1h window sits inside the daily window (and the eight assets are highly correlated). Holding hourly and daily positions on the same lean is *doubled exposure to one view*, not diversification — handled in sizing, and accounted for in effective sample size. |

### Decision

**Program scope (operator decision, 2026-08-11): the 1h and daily markets only.** The 5m
and 15m rungs are out of scope entirely — no trading, no measurement, no recording.

| Market | Role | Rationale |
|---|---|---|
| **1 day** | **Sole Accumulator candidate — not yet built.** Vola-trade rung. | The only rung with an achievable directional bar (Sharpe 1.08) and the deepest book (~$36.7k). Accumulator blocked until a factor with a demonstrated multi-day predictive half-life exists; none is currently specified. |
| **1 hour** | **In scope for the Vola trade and for M1's pre-registered test. Directional accumulation stays dead-by-arithmetic** (required Sharpe 6.46) unless M1 clears its frozen 1h bar — which the stated prediction says it will not. | The vola bar at 1h is `\|dσ/σ\| ≈ 8%` at z≈1 — a real bar, unlike the directional one. Also 22% more expensive per trade than daily (2.75¢ vs 2.25¢; 2¢-wide book). |
| **15 min** | **Out of scope** (was: cut, required Sharpe 10.6) | Operator decision above. |
| **5 min** | **Out of scope** (was: cut, required Sharpe 18.3; Scalper cut §6) | Operator decision above. |

### Settlement audit (2026-08-10) — verified

Resolution text pulled verbatim from the venue for both target rungs:

| Rung | Rule | τ | Ties |
|---|---|---|---|
| **1 hour** | close ≥ open of the Binance BTC/USDT **1-hour candle** named in the title | 3,600s | credit **Up** |
| **1 day** | Binance 1-minute candle close at **noon ET** compared to the previous day's noon-ET close | 86,400s | resolve **50-50** |

Two results. First, the daily rung's horizon is genuinely 24 hours — the "1-minute candle"
is the *sampling rule* for the price, not the measurement horizon. Second, **the tie
convention differs by rung**, so the tie-mass term is not transferable: it applies at 1h
(and 5m/15m), and does *not* apply at daily, where a tie pays 0.5 to each side — which is
what a fair coin already pays, so the correction vanishes rather than favouring Up.

Both rungs settle on Binance data, confirming that for these rungs the backtest series and
the settlement series are identical. Timezone must be resolved as `America/New_York`, never
a fixed UTC offset.

### Book switching by regime — withdrawn

An earlier version of this section derived a switching rule `τ = (T·σ/μ)²` and made it the
premise of measurement M0. **It is withdrawn on two independent grounds.**

*It was dimensionally wrong.* `T` is a target edge in probability units, so converting it
to a standardized tilt requires dividing by `φ(0)`. The correct form is
`τ = (T / (φ(0)·(μ/σ)))²`, which is larger by `1/φ(0)² = 6.3×` in τ. At T = 2.75¢ and a
daily Sharpe of 0.05, the published formula returned 7.3 hours where the correct value is
45.6 hours. **That error is precisely what made the hourly book appear viable.**

*Its premise is unmeasurable in the relevant form.* The rule requires that `μ/σ` differ
across regimes. By Merton (1980), `SE(μ̂) = σ/√T` depends only on calendar span, not on
sampling frequency — slicing finer bars buys nothing. Detecting a difference of 0.5–1.5
annualized Sharpe between regimes needs on the order of decades against the ~9 years of
Binance history available, and the eight-asset panel improves the standard error by only
5–16% because the assets are highly correlated.

**Consequence: one rung, always. No switching machinery, and the regime layer's
`E[duration]` output is dropped as a trading input** — a geometric duration is memoryless,
so expected residual life is a constant per state and carries no timing information. The
regime layer is retained only for σ̂ conditioning and the BOCPD stand-down alarm.

---

## 5. Strategy 1 — the Accumulator (daily rung only; **blocked, not scheduled**)

> When the market is leaning, buy the leaning side window after window at flat size, hold
> each to resolution, and let a modest win-rate advantage compound across many windows.

**Status: blocked.** §4 leaves the daily rung as the only viable venue (required Sharpe
1.08) and simultaneously establishes that no factor in this design predicts at daily
horizons. The strategy is not built until that gap is closed by a factor with a
demonstrated multi-day predictive half-life. Two further constraints are recorded here so
they are not rediscovered later:

- **Confirmation is impossible on this rung.** With one window per day across eight highly
  correlated assets, effective sample size is ~1.2–1.7 independent bets per day. At 1¢ of
  edge per trade a t = 2 verdict takes on the order of two decades; at 3¢, ~2.5 years. The
  daily Accumulator can be *researched* historically. It can never be *confirmed forward*
  before capital is committed.
- **The direction layer is not self-disarming.** The earlier claim that "β → 0 if flow
  predicts nothing" is true in expectation and false in every finite sample — and the entry
  gate selects precisely the upper tail of that estimation noise. With `k` candidate
  factors and a true β of zero, the phantom probability tilt is `φ(0)·√(k/N_eff)`. At this
  document's 11 candidate factors that is roughly 5¢ at the daily rung — comfortably below
  the 8¢ staleness cap, and exactly the mechanism that produced f45.

| Model | What it calculates | How the math works | Why this model |
|---|---|---|---|
| EWMA / HAR-RV (vola engine) | σ̂, hence coverable ground σ̂√τ | Weighted average of recent squared returns; HAR blends three memory horizons | Banking and literature standards; one dial vs three learned weights; QLIKE picks the winner |
| BOCPD | "The world just changed" alarm | Bayesian posterior over run length; collapses within a few bars of a break | Stand-down trigger and σ̂ memory reset. (The HMM's `E[duration]` output is **dropped** — see §4.) |
| N(d₂) prior | Fair win probability Φ(z) with no directional knowledge | Lead ÷ coverable ground, evaluated on the normal CDF | Desk-standard digital pricing; a *proven* null — it priced the v1.0.0 market to ≈$0 |
| Direction layer Φ(z + βᵀx) | The drift correction from flow | A probit with `z` as a fixed offset; factors shift the distribution's centre | The functional form is correct. **The estimator must be probit/logit MLE on the binary outcome with `z` as offset, plus a ridge prior** — not the two-stage fit on σ̂-standardized returns, which puts a noisy x-correlated quantity in the target's denominator and yields anti-conservative standard errors |
| Cost model | Breakeven win rate per entry price | ask + `0.07·p·(1−p)` + half-spread, **as a size-dependent function** | Venue-exact on fees. Depth figures in §4 are book *totals*; v1.0.0 measured only 250–350 shares at the touch, so a $500 clip walks the book — slippage is not ≈0 and must be measured from recorded L2 |
| Flat sizing | Constant position size | No formula — deliberately | Kelly's own math: at weak, noisy edge estimates the optimal fraction collapses toward flat |
| Brier score **vs the market** | Whether P̂ beats the price, not the null | `Brier(market) − Brier(ours)` on identical timestamps | See §11 — beating Φ(z) is not evidence of edge |

**Mandatory before any fit: orthogonalise `x` against `z`.** `K` is the window's open and
`S` is spot now, so `ln(S/K)` *is* the return that this window's order flow caused.
Regressing the outcome on `[z, OFI]` unorthogonalised produces a large, unstable,
sign-flipping β̂ — not β → 0.

## 6. Strategy 2 — the Scalper — **CUT**

> Original thesis: during high-vola bursts, enter as impatient flow ignites and exit into
> the repriced book minutes later, trading the ticket's price path rather than the outcome.

**Status: cut on arithmetic (2026-08-10).** Not deprioritised — refuted.

**The gamma ladder was an illusion.** The earlier version of this section argued that a
0.1% spot move is worth +26¢ at 4:40 remaining, +33¢ at 2:30 and +44¢ at 1:00, against a
~5¢ round trip. The error is that it held the spot move *fixed in percent* while the clock
shrank. Express the bar in the only units that are comparable across τ:

```
required conditional spot move = round_trip / φ(0)
                               = 0.125 σ of the remaining window   (at a 5¢ round trip)
```

**This is invariant to τ and to σ.** The gamma leverage the section advertised is exactly
cancelled by the shrinking remaining volatility. Moving later in the window does not make
the trade easier; there is no sweet spot to find.

So the bar is an information coefficient of ~0.125 on the next 30–120 seconds of BTC —
**after** our own decision-to-fill latency, on a venue where [FINDINGS.md](FINDINGS.md)
already measured the actionable window at 5–12ms. The section's own requirement that
"entry must precede the repricing" is a restatement of the race §3 calls *lost and stays
lost*.

**Two further contradictions, recorded so the idea is not revived casually:**

- The claim that "this is path volatility, not drift, so §4's horizon arithmetic does not
  apply" is false. Edge scales as `μ·t_hold/√τ_remaining`, so exiting early *reduces* the
  drift-derived edge by `t_hold/τ` while doubling the toll.
- §8's staleness cap forbids acting on claimed edges above ~8¢ — a *measured* effect. This
  strategy advertised 26–44¢ of claimed ticket mispricing as its opportunity. **The shared
  chassis' own risk gate forbids essentially every trade this strategy existed to take.**

**Operator decision (2026-08-11): cut with no revival clause.** The 5m and 15m markets are
out of the program's scope entirely — no trading, no measurement, no recording. The
program is the 1h and daily markets only.

---

## 7. Strategy 3 — the Vola trade (mid-window) — **blocked on identification**

> Mid-window, the market's price implies a volatility. When the vola engine's forecast σ̂
> disagrees with that implied σ by more than the cost stack, buy the side the market has
> mispriced — the favorite if implied vola is too high, the underdog if too low. No
> directional view is required.

**The mechanism.** At window open, spot equals the strike, `z = 0`, and fair value is 50¢
*regardless of vola* — these contracts carry no vola information at open, and betting there
is a pure direction bet on the one quantity that is barely forecastable. Once spot has
drifted from the strike, the price becomes a joint statement about direction *and*
remaining vola, and the vola half is recoverable by running the pricing formula backwards:

```
σ_implied = ln(S/K) / (Φ⁻¹(price) · √τ)
```

Comparing σ_implied against σ̂ converts "is vola mispriced here?" from a speculation into a
per-second measurement (§2). The bet is then on the *magnitude* of remaining movement — the
quantity that vola clustering makes genuinely forecastable — not its sign.

**The bar, in the right units.** A vola disagreement moves the price by `z·φ(z)·(dσ/σ)`,
which is maximised near `z ≈ 1` (an ~84¢ ticket). Required fractional vola error:

```
|dσ/σ| = cost / (z · φ(z))   ≈ 8% at the optimum, before attenuation
```

That is a real bar rather than an absurd one — unlike the 6.46 Sharpe the Accumulator's
hourly rung required — which is why this remains the most promising of the three ideas.

### σ_implied is not identified — the problem that must be fixed first

> **Correction (2026-08-10).** The worked example previously given here (spot 0.25% above
> strike at 30 minutes remaining; market 70¢ vs model 75¢, read as "the market prices ~32%
> more vola") **is fully reproducible from drift alone with zero vola disagreement.** At
> z = 0.69 the drift this document itself assumes at 1h moves the price by
> `φ(0.69)·0.163 = 5.1¢` — the entire claimed gap. It has been removed.

The inversion `σ_implied = ln(S/K)/(Φ⁻¹(p)√τ)` assumes the market prices a driftless
Gaussian. Four effects break that, each individually as large as the claimed edge:

| Contaminant | Size | Note |
|---|---|---|
| **Drift** | ~5¢ at z≈0.7, 1h | **Strategy 1 exists because μ≠0. Strategy 3 is only valid if μ=0.** Run together they take opposite sides of the same residual |
| **Tail shape** | ~3.8¢ at z≈0.69 | Under a standardized t₅ the digital is 79.3¢ vs Gaussian 75.5¢, peaking at z≈0.67–0.73 — the exact centre of the intended band. Not an optional assumption: conditioning on a point σ̂ makes the return a variance mixture, so leptokurtosis arises mechanically |
| **Jensen bias** | +0.6¢ to +1.4¢ | Plug-in `Φ(u/σ̂)` instead of `E[Φ(u/√IV)]` is biased with fixed sign. [MODEL_STACK.md](MODEL_STACK.md) §3 dismisses the rate term (~5×10⁻⁷) while ignoring a term ~1000× larger |
| **Attenuation** | k = 0.50 at equal skill | You capture `k·gap`, not `gap`, where `k = (1−ρr)/(1+r²−2ρr)` and `r = σ_err(us)/σ_err(market)`. **k < 0 whenever `ρ·σ_err(us) > σ_err(market)`** — the trade is then negative-EV at every threshold |

**Required design changes** before this strategy is measured, let alone built:

1. The identifying test is a **forecast-encompassing regression** — logit of the settled
   outcome on `[p̂_ours, p_market]` — which returns `k` and its confidence interval directly
   in cents. Kill if the lower bound on `k` is ≤ 0. A QLIKE contest **cannot** identify
   `(r, ρ)` and is separately contaminated by the variance risk premium, which makes a QLIKE
   "win" near-automatic and therefore meaningless as a gate.
2. Price off an empirically fitted kernel (standardized-t with ν estimated per rung from
   the Binance archive) or the empirical CDF — not Φ.
3. Price as a mixture over σ̂'s predictive distribution, not a plug-in point estimate.
4. **Benchmark σ̂ against Deribit implied vol first** — free, liquid, uncontaminated by this
   venue. If σ̂ cannot beat Deribit out-of-sample, the strategy dies with zero venue data.
   Corollary: for BTC and ETH a maker can hedge into Deribit, so this trade's plausible home
   is the assets without a deep options market — SOL, XRP, DOGE, BNB, HYPE, ZEC.

| Model | What it calculates | Status |
|---|---|---|
| Vola engine + BOCPD | σ̂ — the forecast this strategy monetizes | Unchanged from §5 |
| Implied-σ inversion | The market's σ | **Must be re-specified** per changes 2–3 above |
| Encompassing regression | `k` — how much of the gap is actually ours to capture | **New; replaces the QLIKE gate** |
| Deribit IV comparison | Whether σ̂ has any skill at all | **New; runs first, needs no venue data** |
| Cost model, flat sizing, market-benchmarked Brier | As §5 | Unchanged chassis |

**Entry gates.** A floor on `|z|` — the inversion is degenerate at z ≈ 0. A floor on τ, and
the §8 staleness cap. Exit: hold to resolution by default (redemption is free; a round trip
pays the toll twice), closing early only when the market's bid passes our fair value by
more than the exit cost.

> **Correction (2026-08-10).** This section previously claimed the tradeable region away
> from 50¢ is "also where the fee parabola is cheapest." True in cents, false in
> risk-adjusted terms, because the half-spread does not scale with `p(1−p)`. Measured,
> `cost/√(p(1−p))` is 0.0550 at p=0.50, 0.0530 at 0.80, and *rises* to 0.0706 at 0.97 —
> flat to ~4% across the book and worse at the wings. There is no fee escape hatch.

**Why it can exist:** vola clustering is among the most replicated regularities in finance
— it is why EWMA, GARCH and HAR work; retail sets marginal mid-window prices on these
books; and any maker quoting σ formulaically inherits its staleness at regime breaks.

**Why it might not:** v1.0.0 was already, in effect, comparing its own Φ(z) to the market
price and found ≈$0. That is evidence against this strategy. It is reopened rather than
refuted only because v1.0.0 used a naive 120-second rolling stdev for σ, traded only the 5m
rung, and never scored σ̂ against σ_implied as forecasts — but until the identification
problem above is fixed, no measurement can distinguish a vola edge from drift, tails, or
Jensen bias.

---

## 8. Shared chassis

- **The regime layer conditions σ̂ and raises the stand-down alarm. It no longer switches
  books or strategies** — the switching rule is withdrawn (§4) and the Scalper is cut (§6).
  Ambiguous readings arm nothing. Flat is a position, and historically the most profitable
  one in this market.
- **No strategy is currently scheduled.** The Accumulator is blocked on a missing daily
  factor (§5); the Scalper is cut (§6); the Vola trade is blocked on identification (§7).
  This is the honest state of the design, not an oversight.
- **Correlated exposure.** The eight listed assets are highly correlated. Total directional
  exposure — not per-position size — is the quantity that must be capped, or "flat sizing"
  silently becomes a leveraged single bet expressed eight ways.
- **Staleness cap (~8¢).** The v1.0.0 soak measured PnL by claimed edge as *monotonically
  decreasing*: the 4.5–7% band returned +7% ROI while claimed edges above 15% returned
  −36% to −57%. A screaming edge means the model is lagging a fast market, not that the
  market is wrong. The cap is a measured effect, not a guess. **It does not catch estimation
  noise** — the phantom tilt from fitting k factors on limited data sits *below* the cap
  (§5), which is why the cap alone did not stop f45.
- **Flat sizing**, per §5.
- **Exit rule.** Hold to resolution by default: redemption is free, so a round trip pays fee
  and spread twice (~5.4¢ vs ~2.75¢ on the hourly book). Exit only when the market's bid
  exceeds current fair value by more than the exit cost, on a model flip, or on a BOCPD
  break that voids the σ̂ the position rests on. **No price-triggered stop-losses** — in a
  probability market a drawdown either means the model should update (exit on the model) or
  the position is now cheaper (a stop implements neither).
- **Shrinkage toward market price.** A price move against us is often information. Treating
  the market purely as an opponent is how a taker gets adversely selected — the measured
  anti-predictive wall above is that effect. Fair value should be shrunk toward the market
  rather than the market ignored.
- **Continuous Brier scoring against the market**, not against Φ(z) — see §11.
- **Pre-registration and FDR correction.** Regimes multiply hypotheses; v1.0.0 tested 75
  slices and 0 survived correction. All regime definitions, thresholds and pass criteria
  are frozen in writing before any measurement is run.

---

## 9. Killed hypotheses — do not re-litigate

Full evidence in [FINDINGS.md](FINDINGS.md); summary here so nothing is accidentally
rediscovered. All results are venue-true and fee-inclusive.

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Fair-value model has directional edge (v0, unfiltered) | **Dead** | ≈$0 over 400+ settled shadow trades |
| Freshness / cushion / edge-cap gates create edge (v2, v7, v8, f45, f45_spread) | **Dead** | All seven variants regressed to null as n grew; f45's post-freeze segment −$0.41/trade, WR 48.6% (n=37) |
| Switching to the current race leader helps | **Dead** | Follow-the-leader +$3–6 vs hold +$16.60 on the race's own data |
| Regimes (time / edge / vola / basis) hide an edge | **Dead** | A-priori bands, permutation + FDR: 0/12 and 0/75 cells survive |
| Maker execution fixes the fee problem | **Dead** | Adverse selection is the maker's true rent: fills 98% of losers, 75% of winners |
| Tick-level CEX lead-lag race at retail latency | **Dead** | Requires 5–12ms colocation; corroborated externally |
| Live losses were bad luck rather than fees | **Dead** | Gross +$6.27 vs taker fees −$23.51; net −$19.35 across 351 fills |

**What is *not* covered by these results, and therefore remains open:** OFI on the CEX
tape, liquidation/forced flow, funding as regime context, and the daily horizon. None of
these were ever instrumented in v1.0.0.

**Added to the kill list by the 2026-08-10 design review** (arithmetic, not measurement —
see [CORRECTIONS.md](CORRECTIONS.md)):

| Hypothesis | Verdict | Evidence |
|---|---|---|
| 1h / 15m rungs are viable for directional trading | **Dead** | Required annualized Sharpe 6.46 / 10.6 — multiples of any documented systematic program |
| Gamma scalping the 5m ticket path | **Dead** | Required conditional move is `RT/φ(0)` = 0.125σ of the remaining window, *invariant to τ* — the advertised gamma leverage is exactly cancelled by shrinking remaining vola |
| Book-switching by vola regime | **Withdrawn** | Formula was missing a `1/φ(0)²` Jacobian (6.3× in τ); premise needs decades of data to test (Merton 1980) |
| Regime `E[duration]` licenses accumulation | **Dead** | Geometric duration is memoryless — expected residual life is constant per state and carries no timing information |

---

## 10. News as a stand-down gate

Free sources exist (GDELT, crypto-outlet RSS, CryptoPanic's free tier, FRED for macro
release calendars, exchange announcement feeds). **Their intended role here is a
stand-down gate, not a directional signal** — headline-to-direction inference is difficult,
heavily gamed, and outside our latency budget. Scheduled macro releases inside a window
are a reason to *not trade*, and possibly a reason to expect vola to be underpriced.

Each source's current free-tier terms must be verified before any dependency is taken;
none is asserted here as fact.

---

## 11. Evidence bar and next step

### The benchmark must be the market, not the model's own null

> **Correction (2026-08-10).** The previous measurement program promoted a model on
> "beats the Φ(z) prior on Brier out-of-sample." **That gate cannot fail informatively.**
> If our model uses a subset of the information in the market price, then
> `Brier(market) ≤ Brier(ours)` unconditionally — so beating Φ(z) establishes only that we
> added *something* to a driftless Gaussian, which almost any signal does. It says nothing
> about profitability.

This project had already pre-registered the correct bar and then regressed from it:
`docs/PIVOT_2026-07.md` §4 specifies *"Brier score vs the market-implied baseline (skill =
market Brier − ours); fee-true simulated PnL... Success bar: positive Brier skill AND
simulated PnL 95% CI > 0 over ≥30 resolutions"* — and it is **already implemented** in
`tools/forecast_journal.py`.

| | Benchmark | Role |
|---|---|---|
| Φ(z) null | the driftless prior | **Diagnostic only.** Necessary, never sufficient |
| **Market ask, fee-adjusted** | the executable price | **The promotion gate.** Paired Brier on identical timestamps, plus fee-true PnL CI |

Two mechanics that decide whether this is honest: benchmark against **the ask with size**,
never a midpoint (on a 2¢ book the midpoint error is 1¢ — 36% of the whole cost stack);
and Brier-first is not only correct but ~5–20× cheaper, needing roughly 3,000 observations
for a t = 2 verdict against ~14,000 trades for the PnL test.

### The cheapest test that kills the design fastest — run this first

**The OFI horizon-decay curve.** Everything above reduces to one empirical question: does
*any* flow signal retain enough predictive content at 1h or daily horizons to clear
`cost/φ(0)`?

- **Data:** Binance **aggTrades** — not 1m klines. Aggregating to one minute destroys the
  sub-minute burst structure where the documented content lives and reduces the test to
  trailing signed-volume momentum, a long-mined public factor.
- **Method:** event-level signed aggressor imbalance at 1s/10s/60s/300s aggregations, fitted
  walk-forward against forward returns at h ∈ {10s, 60s, 300s, 900s, 3600s, 86400s}, with
  `x` orthogonalised against `z` (§5) and β shrunk by a pre-registered ridge prior.
- **Metric:** the fraction of windows where fitted `|βᵀx|` exceeds `cost/φ(0)` — 0.0689 at
  1h, 0.0564 at daily — plus per-fold sign consistency of β.
- **Pre-registered kill:** if fewer than 1% of 1h windows clear 0.0689, or fewer than 5% of
  daily windows clear 0.0564, the Accumulator has no live rung and Strategy 1 is dead at
  every horizon this venue lists.

Cost: one weekend of compute on free public data, no capital at risk. **This is the test
the earlier Phase A did not run.**

### Remaining measurements, re-scoped

- **M1 (Deribit gate, no venue data)** — can σ̂ beat Deribit implied vol out-of-sample? If
  not, the Vola trade dies immediately (§7).
- **M2 (identification)** — forecast-encompassing regression of the settled outcome on
  `[p̂_ours, p_market]`; kill if the lower confidence bound on `k` ≤ 0. Replaces the QLIKE
  gate, which cannot identify and is contaminated by the variance risk premium.
- **M3 (generalisation)** — does anything surviving hold across the eight listed assets, or
  is it a BTC-only artifact?
- **M0 is withdrawn** — its premise (the switching rule) is withdrawn, and its comparative
  form needs decades of data (§4).

**Start the venue top-of-book recorder now** — ask, bid and *size* at ≥1 Hz across rungs
and assets. It is free, needs no capital, and is the long pole: nothing about real
executable cost is knowable without it, and recorded time cannot be backfilled. A
venue top-of-book loader is currently **absent** from [MODEL_STACK.md](MODEL_STACK.md)'s
build list and must be added.

Sample-size accounting must be explicit throughout: cross-asset observations are
correlated, so *effective* sample size is materially below the raw count, and thresholds
are set against the effective figure. Any cell where the phantom tilt `φ(0)·√(k/N_eff)`
exceeds half the cost stack is not fitted at all.

**Honest prior: ~8% that this program finds a real, cost-clearing, market-benchmarked
edge — against a >70% chance that a poorly-benchmarked version of it would report a pass.**
That gap, not the market, is the principal risk this document exists to manage.
