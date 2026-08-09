# Factors — every input, how it is computed, and why it is present

**Status: design document, nothing implemented.** Companion to
[STRATEGY_DESIGN.md](STRATEGY_DESIGN.md) (thesis, market selection, strategies) and
[MODEL_STACK.md](MODEL_STACK.md) (the models these factors feed).

**Terminology:** **vola** = volatility, **vol** = volume.

## Standing data rules

1. **Returns may be compared across venues; levels never.** A persistent basis of roughly
   $50 exists between Chainlink and exchange prices. Any computation that subtracts one
   venue's price level from another's is wrong by construction.
2. **Basis is computed within a single venue only** — that venue's perp against that
   venue's spot.
3. **Every factor must be computable identically at fit time and at run time.** A factor
   that can only be computed in batch is a lookahead bug waiting to happen.
4. **Filtered, never smoothed.** Any latent-state estimate uses only information available
   at that timestamp.

---

## 1. Pricing model inputs — N(d₂)

These build one ratio. None of the raw inputs means anything alone: a lead only matters
relative to the ground the remaining time can cover, and that ground only exists relative
to time and vola.

| Factor | Description | Calculated with | Source | Why it is needed |
|---|---|---|---|---|
| **S** — spot | Current BTC price | Read directly | Chainlink live stream (~1/sec) | The current position in the race |
| **K** — strike | The level the close must reach or exceed for Up to win; fixed at window open | Read directly | Polymarket crypto-price API (`openPrice`) | Defines the contract |
| **ln(S/K)** — the lead | How far spot is ahead of or behind the strike, in log terms (≈ percentage for small gaps) | `ln(S ÷ K)` | Derived | Logs because percentage moves compound and log-returns add cleanly |
| **σ̂** — vola | Typical size of a one-second return right now | Vola engine (EWMA / HAR-RV / GARCH, QLIKE-selected) | Chainlink 1s returns; fitted on exchange history | A given lead is large on a quiet day and trivial in a storm — vola sets the scale |
| **τ** — time remaining | Seconds left in the window | `window_end − now` | Market schedule | More time means more opportunity for the lead to be overturned |
| **σ̂√τ** — coverable ground | How far spot could realistically still move before settlement | `σ̂ × √τ` | Derived | Square root because independent random steps add in variance, not standard deviation: 4× the time gives only 2× the spread |
| **z** | The lead expressed in units of coverable ground | `ln(S/K) ÷ (σ̂√τ)` | Derived | The single number combining lead, vola and clock |
| **Φ(z)** | Fair probability of Up, absent directional knowledge | Standard normal CDF | Mathematics | The share of the outcome distribution falling above the strike |
| **Tie mass** | Correction for the venue crediting `close = open` to Up, given discrete (~2dp) prints | Normal density at the strike × one print-width, capped | Venue rules + print granularity | Structural: makes fair value strictly > 0.5 when spot equals the reference |

**Worked example.** S = $61,050, K = $61,000, σ̂ = 8.5×10⁻⁵/sec, τ = 180s.
Lead = +0.082%. Coverable ground = 8.5×10⁻⁵ × √180 = 0.114%.
z = 0.082 / 0.114 = 0.72. **Φ(0.72) ≈ 76%.**

Anchor cases: z = 0 → 50%. z = 1 → 84%. z = −1 → 16%. z = 2 → 98%.

**Settlement source varies by rung** (per the venue's market descriptions, 2026-08-09;
re-verify exact rule text at build time): 5m/15m resolve on Chainlink prints; the **1h
market resolves on the Binance BTC/USDT 1-hour candle**, and the **daily market on the
Binance 1-minute candle at 12:00 ET** (noon-to-noon). S and K must always come from the
rung's own settlement source. The returns-not-levels rule governs any cross-source
comparison.

### Implied vola — the Vola trade's factors (STRATEGY_DESIGN §7)

| Factor | Description | Calculated with | Source | Why it is needed |
|---|---|---|---|---|
| **σ_implied** | The remaining-window vola the market's price asserts | `ln(S/K) ÷ (Φ⁻¹(price) · √τ)` — the pricing formula run backwards | Mid-window market price (CLOB mid or ask) | Turns the market's opinion about vola into a measurable number |
| **Vola gap** | How far the market's vola opinion sits from the engine's forecast | `ln(σ_implied ÷ σ̂)`, z-scored against its own history | Derived | The Vola trade's entry signal; z-scoring separates "unusual gap" from "normal disagreement" |

**Degeneracy warning:** the inversion is undefined at `price = 0.5` or `S = K`, and
precision decays as either is approached — at window open these contracts carry no vola
information at all. A frozen floor on `|z|` gates the factor; the tradeable region is
therefore away from 50¢, which is also where the fee parabola is cheapest.

---

## 2. Regime classifier inputs — HMM

| Factor | Description | Calculated with | Source | Why it is needed |
|---|---|---|---|---|
| **log RV, short window** | How violent the market is right now | `ln(Σ r²)` over the trailing short window | Chainlink 1s stream live; exchange history for fitting | The fast symptom. Logs because the HMM assumes Gaussian emissions and raw realized vola is heavily skewed — log-RV is near-Gaussian |
| **log RV, long window** | The backdrop the current reading sits against | Same, longer trailing window | Same | A quiet minute inside a storm is not a calm regime. One horizon cannot separate "regime" from "lull" |

**Frozen configuration** (set before any measurement, per the FDR discipline):

| Choice | Value | Rationale |
|---|---|---|
| Number of states K | 3 (calm / normal / storm) | Checked against BIC on history, then frozen — K is a knob, and knobs are how the previous system curve-fit itself |
| Decoding cadence | 1 minute | Regimes do not flip second to second; sub-minute break detection is BOCPD's job |
| State labelling | By vola rank | Prevents label-swapping across refits |
| Refit cadence | Weekly, offline | Live decoding is a single forward step per bar — cheap enough for any loop |

### Deliberate exclusions, with reasons

| Excluded | Why |
|---|---|
| **Signed returns** | Sign is direction — the payload of a different layer. Regime is about magnitude. The library's BTC evidence also shows the up-vs-down vola asymmetry is weak and wrong-signed |
| **Volume (vol)** | Tempting, but every added factor multiplies the overfit surface. Vol enters the *direction* layer as OFI, where it carries its real information |
| **Funding, liquidations** | Candidate regime factors, admissible only via pre-registered tests, one at a time, after the two-factor version proves its conditioning value |
| **Price levels** | Non-stationary. A state fingerprinted at BTC ≈ $60k is meaningless at $80k. Returns and realized vola are level-free |

---

## 3. Direction layer — order-flow imbalance

Every trade has a patient side (posted and waited) and an impatient side (paid for
immediacy). Impatience is costly, so it is only rational if you believe waiting is worse —
which makes the trade stream a stream of votes on the near future. Exchange trade data
carries an aggressor flag per trade, so no trade-sign inference (Lee–Ready or similar) is
required.

| Factor | Description | Calculated with | Source | Why it is needed |
|---|---|---|---|---|
| **OFI, short window** | Share of recent impatient flow that was buying: −1 all sellers, +1 all buyers, 0 balanced | `(buy-aggressor vol − sell-aggressor vol) ÷ total aggressor vol`, trailing short window | Exchange aggregated-trade stream | The burst detector — catches pressure as it ignites |
| **OFI, medium windows** | The same measure over longer lookbacks | Same, longer windows | Same | Pressure has rhythm: a brief spike and a sustained lean are different phenomena. Both are measured; the fit decides which matters |
| **OFI, window-length** | The whole-window lean | Same, over the market's own window length | Same | The slow grind the Accumulator cares about |
| **Trade-count imbalance** | The same vote share counting *trades* rather than notional | `(# buy − # sell) ÷ total`, per window | Same | 200 small buyers and 1 whale are different signals. Separates crowd behaviour from single-actor moves |

**Fitting target:** forward return ÷ `σ̂√τ` — dimensionless, so one set of weights applies
across calm and storm alike. Fitted strictly walk-forward.

---

## 4. Direction layer — liquidations and forced flow

A liquidation is the only flow in the market guaranteed to be neither informed nor
discretionary: the position is force-closed at market regardless of price. Flow that
*must* happen is flow that can be anticipated.

| Factor | Description | Calculated with | Source | Why it is needed |
|---|---|---|---|---|
| **LIQ_net** | Net notional being force-closed, and on which side | `(long-liq $ − short-liq $)`, trailing short and medium windows | Exchange forced-order stream | The shove's direction and magnitude — the drift input |
| **LIQ_burst z** | Whether forced flow is abnormal relative to a typical period | Current liquidation rate ÷ its own trailing range | Same | Cascade-ignition detector — the Scalper's arming trigger |
| **ΔOI** | Whether leveraged positions are closing en masse | Open interest now − open interest one window ago | Exchange open-interest endpoint | Falling OI during a price fall confirms a cascade rather than fresh short-selling. Same price move, opposite implications |

**Honest limitation:** the public liquidation broadcast is throttled by the exchange
(roughly one event per second). These factors are an **intensity proxy, not a census**.
They must never be presented as a complete ledger of forced flow. Historical depth is also
partial, so the liquidation measurement may need live-recorded data to reach statistical
power — and cascades are rare (a few per month), so sample discipline matters doubly.

---

## 5. Direction layer — funding and basis (regime context)

A perpetual future never expires, so exchanges tether it to spot with a funding payment:
whichever side is crowded pays the other. Funding is therefore a *posted price for being on
the popular side* — a public meter of where leverage, and therefore liquidation fuel, is
stacked.

| Factor | Description | Calculated with | Source | Why it is needed |
|---|---|---|---|---|
| **Funding z** | How stretched the crowd's positioning is relative to its own normal | Current funding vs trailing mean and standard deviation | Exchange funding endpoints | Fragility gauge — tells you which cascade direction has fuel behind it |
| **Basis z** | The instantaneous version of the same lean | `(perp − spot) ÷ spot`, z-scored, **within one venue** | That venue's perp mark and spot | A faster read between funding settlements |
| **Time to funding** | Minutes until the next funding payment | Clock | Funding schedule | Mechanical position-trimming clusters near resets — mild, free seasonality |

**Role: context, never trigger.** Funding moves over hours and cannot call a single short
window; its standalone alpha decayed years ago. Its value here is interaction: the same
sell-side flow burst is far more dangerous when funding is stretched long, because the
stacked side is what a cascade ignites. Funding does not fire the gun — it identifies which
side of the room holds the gunpowder.

---

## 6. Cost and execution inputs

| Factor | Description | Calculated with | Source | Why it is needed |
|---|---|---|---|---|
| **Best ask / best bid** | The executable price, per outcome token | Top of book | Polymarket CLOB | Edge is measured against what can actually be executed, never against mid |
| **Size at best** | How much is available before the price worsens | Top-of-book size | Polymarket CLOB | A price with no size behind it is not a price. The previous system recorded this but never used it in the decision |
| **Spread** | Ask − bid | Derived | Polymarket CLOB | Half-spread on entry; full spread on a round trip |
| **Taker fee** | `0.07 · p · (1−p)` per share at entry | Formula | Venue schedule, verified against real fills | Charged win or lose; peaks at coin-flip prices |
| **Breakeven win rate** | The bar a directional bet must clear | `p + fee` (+ half-spread in practice) | Derived | The number every direction claim is judged against |
| **Round-trip cost** | The Scalper's bar | `2 × fee + full spread` ≈ 4.5–5.5¢ | Derived | Exiting before resolution doubles fee exposure and crosses the spread twice |

---

## 7. Factor hygiene checklist

Applied to every factor before it enters any model:

- [ ] Computable identically offline (fitting) and online (live) — no batch-only inputs
- [ ] Uses only information timestamped at or before the decision instant
- [ ] Level-free where cross-venue (returns, not prices)
- [ ] Stationary enough to fit (z-scored or log-transformed where the raw scale drifts)
- [ ] Its source's rate limits and completeness are documented, including known throttling
- [ ] Frozen in writing before it is measured, and counted in the multiple-testing family
