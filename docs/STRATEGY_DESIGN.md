# Strategy design — what we are trading, why, and on what evidence

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
   research tested this and killed it (see §8); the sibling research folder's VPIN evidence
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
| **News / scheduled macro** | A CPI or FOMC release inside a window. | event | **As a stand-down gate**, not a direction signal (see §9). |

---

## 4. Horizon selection — why 5 minutes is the wrong market for direction

This is the most consequential finding of the design phase, and it is arithmetic on the
fee formula rather than an empirical claim.

**The fee is a fixed toll per trade. Directional edge grows with √τ. They do not scale
together.** The tilt a sustained drift produces over a window is `(μ/σ)·√τ`, while the fee
`0.07·p·(1−p)` is charged once per entry regardless of window length.

At BTC's typical ~2.5%/day vola and an assumed sustained 2%/day drift — the *same*
forecasting skill applied at different window lengths:

| Market | σ̂√τ (coverable ground) | Win rate from that drift | Edge over 50¢ | Cost (fee + spread ≈2.75¢) as % of edge |
|---|---|---|---|---|
| **5 min** | 0.15% | 51.9% | 1.9¢ | **145% — structurally underwater** |
| **15 min** | 0.26% | 53.3% | 3.3¢ | 83% |
| **1 hour** | 0.51% | 56.5% | 6.5¢ | 42% |
| **4 hour** | 1.02% | 62.8% | 12.8¢ | 21% |
| **1 day** | 2.50% | 78.8% | 28.8¢ | 10% |

**How to read this table correctly.** It does **not** say that longer horizons offer free
money — an efficient market prices an obvious drift, so if the drift is visible the daily
ask already sits at 78¢ and the edge is zero again. What the last column measures is **how
much forecasting skill survives translation into profit**. At 5 minutes, the toll exceeds
the entire tilt a strong drift can produce: even a perfect drift forecast cannot pay for
itself. At an hour, the same skill keeps ~58% of what it earns.

This reproduces, from the fee formula alone, the conclusion the v1.0.0 research reached
empirically and the sibling research folder reached from market-structure evidence: the
5-minute directional lane is **structural, not tunable**.

### Counterweights — why the answer is not simply "trade the longest window"

| Constraint | Effect |
|---|---|
| **Signal horizon must match market horizon** | OFI predicts seconds-to-minutes: strongest at 5m, worthless at daily. Longer horizons require different (trend, macro, positioning) signals. |
| **Verification time** | 288 windows/day at 5m; 24 at 1h; 1 at daily (~2.7 years for 1,000 live samples). Historical backtesting mitigates this, but ~730 daily windows in two years of history means only a *large* effect is provable. |
| **Competition** | Forecasting a day of BTC attracts far more skilled capital than forecasting five minutes. Cheaper toll, harder game. |

### Decision

| Market | Role | Rationale |
|---|---|---|
| **1 hour** | **Primary — Accumulator** | Best point on the curve: fee burden falls to ~42%, flow and positioning signals retain predictive power at this horizon, and 24 windows/day permits live validation in weeks. |
| **15 min** | Secondary — Accumulator | Fallback if hourly liquidity proves insufficient. |
| **5 min** | **Scalper only** | Directional holding is dead here by the table above. The gamma play is untouched by this analysis — it does not hold to resolution and does not depend on drift (§6). |
| **Daily / longer** | Rejected for now | On verification time and competition, *not* economics. Revisit if a slow, genuinely differentiated forecasting edge is ever identified. |

---

## 5. Strategy 1 — the Accumulator (1h primary, low-vola regime)

> When the market is calm and leaning, buy the leaning side window after window at flat
> size, hold each to resolution, and let a modest win-rate advantage compound across many
> windows.

| Model | What it calculates | How the math works | Why this model |
|---|---|---|---|
| EWMA / HAR-RV (vola engine) | σ̂, hence coverable ground σ̂√τ | Weighted average of recent squared returns; HAR blends three memory horizons | Banking and literature standards; one dial vs three learned weights; QLIKE picks the winner |
| HMM | P(low-vola) and E[regime duration] | Learns latent market states from history; live, updates a belief about the current state each bar | The only model that answers "will calm outlive the next N windows" — the licence to accumulate |
| BOCPD | "The world just changed" alarm | Bayesian posterior over run length; collapses within a few bars of a break | Regime death must be detected in seconds, not after a rolling window catches up |
| N(d₂) prior | Fair win probability Φ(z) with no directional knowledge | Lead ÷ coverable ground, evaluated on the normal CDF | Desk-standard digital pricing; a *proven* null — it priced the v1.0.0 market to ≈$0 |
| Direction layer Φ(z + βᵀx) | The drift correction from flow | OFI, forced flow and funding lean, each × a learned weight, shift the distribution's centre | The only legal entry point for directional information; self-disarming (β→0 if flow predicts nothing) |
| Cost model | Breakeven win rate per entry price | ask + `0.07·p·(1−p)` + half-spread | Venue-exact, verified against real fills |
| Flat sizing | Constant position size | No formula — deliberately | Kelly's own math: at weak, noisy edge estimates the optimal fraction collapses toward flat |
| Brier score | Whether P̂ is honest | Mean of (predicted probability − outcome)² | The correct ruler for an instrument whose price *is* a probability |

## 6. Strategy 2 — the Scalper (5m, high-vola regime)

> During high-vola bursts, enter as impatient flow ignites and exit into the repriced book
> minutes later. Trades the ticket's price path; never holds to resolution.

| Model | What it calculates | How the math works | Why this model |
|---|---|---|---|
| BOCPD + HMM | Arming condition | Break alarm fires and the high-vola state confirms | Storms begin with breaks; this strategy only exists inside them |
| Digital delta / gamma | Cents of ticket movement per dollar of spot movement | The steepness of the distribution at the strike; grows as 1/(σ̂√τ) as the clock runs down | The leverage gauge — distinguishes a 30¢ opportunity from a 2¢ dead zone |
| OFI burst z-score | Whether impatience is abnormal right now | Current short-window flow imbalance versus its own recent range | Entry must precede the repricing; a z-score is the simplest honest ignition detector |
| Kyle's λ | Expected price displacement per unit of net flow | Linear fit of past price changes on past net signed flow | Converts "a burst of size X" into "expect Y dollars of move" — checks the move can clear the round trip *before* entering |
| Liquidation feed | Forced-flow confirmation | Notional of forced closures per side; ΔOI | Liquidated traders must trade regardless of price — the most predictable flow in crypto |
| OU half-life + hysteresis bands | Exit target, time-stop, churn guard | How fast displacements decay; entry and exit bands separated to prevent ping-ponging | Scalps die at the exit. Avellaneda–Lee rule: only trade displacements that revert *inside* the window |
| Round-trip cost model | The bar: 2 × fee + full spread ≈ 4.5–5.5¢ | Fee charged on entry and exit; spread crossed twice | Exiting early doubles costs — this must be priced before entry, not discovered after |

**Why the 5-minute window still supports this strategy after §4 killed directional holding
there:** near the strike, late in the window, the ticket's sensitivity to spot is extreme.
A 0.1% spot move (~$61) is worth roughly +26¢ at 4:40 remaining, +33¢ at 2:30, and +44¢ at
1:00 — against a ~5¢ round trip. Far from the strike the same move is worth ~2¢ and the
trade is not worth taking. This is a claim about *path volatility*, not drift, so the
horizon arithmetic in §4 does not apply to it. It is also an **untested hypothesis**: the
entry timing (flow must be detected *before* the reprice) is the hard part and is exactly
what the measurement program must falsify.

---

## 7. Shared chassis

- **The regime layer is the switchboard.** Low-vola arms the Accumulator, high-vola arms
  the Scalper, ambiguous readings arm nothing. Flat is a position, and historically the
  most profitable one in this market.
- **Staleness cap (~8¢).** The v1.0.0 soak measured PnL by claimed edge as *monotonically
  decreasing*: the 4.5–7% band returned +7% ROI while claimed edges above 15% returned
  −36% to −57%. A screaming edge means the model is lagging a fast market, not that the
  market is wrong. The cap is a measured effect, not a guess.
- **Flat sizing**, per §5.
- **Continuous Brier scoring against the Φ(z) null**, so degradation is visible in days
  rather than at a monthly PnL review.
- **Pre-registration and FDR correction.** Regimes multiply hypotheses; v1.0.0 tested 75
  slices and 0 survived correction. All regime definitions, thresholds and pass criteria
  are frozen in writing before any measurement is run.

---

## 8. Killed hypotheses — do not re-litigate

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
tape, liquidation/forced flow, funding as regime context, the 1-hour horizon, and the
gamma-scalp path trade. None of these were ever instrumented in v1.0.0.

---

## 9. News as a stand-down gate

Free sources exist (GDELT, crypto-outlet RSS, CryptoPanic's free tier, FRED for macro
release calendars, exchange announcement feeds). **Their intended role here is a
stand-down gate, not a directional signal** — headline-to-direction inference is difficult,
heavily gamed, and outside our latency budget. Scheduled macro releases inside a window
are a reason to *not trade*, and possibly a reason to expect vola to be underpriced.

Each source's current free-tier terms must be verified before any dependency is taken;
none is asserted here as fact.

---

## 10. Evidence bar and next step

Issue #170 sets the standard: `tools/replay_race.py` fills at real recorded best-ask,
fee-true, validated against 12 live fills. Any claim promoted from this document must meet
that bar.

**Next step is the Phase A measurement program**, specified but *not run*:

- **M1** — Does OFI (10s–60s) predict the sign of the next 15–60 minute BTC return?
  Walk-forward, Brier/AUC versus coin-flip and versus the σ-only prior.
- **M2** — Do liquidation bursts predict continuation or reversion over the following
  15–60 minutes?
- **M3** — Does [OFI + liquidations + funding] beat the σ-only prior on Brier
  out-of-sample, by enough to clear fee + spread at realistic asks?

Phase A requires **no Polymarket data**: M1–M3 are claims about BTC, testable against
years of free public exchange history. Only the final question — whether a surviving
signal clears the venue's cost stack — needs the venue itself.

**Nothing proceeds to implementation until M1–M3 pass pre-registered thresholds.**
