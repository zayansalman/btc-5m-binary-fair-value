# Corrections to the strategy design

This project's value comes from being honest about what it got wrong. The design documents
committed on 2026-08-09 contained four errors serious enough to invert the central
decision. They were found by an adversarial review on 2026-08-10, and every claim below was
independently re-derived before being accepted.

This file records what was wrong, why, and what replaced it. The errors are left visible in
the history rather than quietly overwritten.

---

## C1 — The horizon ranking table was a tautology

**What was published.** [STRATEGY_DESIGN.md](STRATEGY_DESIGN.md) §4 ranked the rungs by
"cost as a % of achievable edge": 118% / 68% / 42% / 8% for 5m / 15m / 1h / daily. The 1h
rung was named **primary** on the strength of its 42%.

**Why it is wrong.** That column is algebraically `required_Sharpe ÷ 15.28`, where 15.28 is
the annualized Sharpe of the document's own illustrative assumption (2%/day drift against
2.5%/day vola). Reproducing the published figures from that identity gives 119.6 / 69.1 /
42.2 / 7.0 against the published 118 / 68 / 42 / 8 — i.e. the column was a constant
rescaling of a quantity it never named, benchmarked against a forecaster nobody has ever
built.

**What replaced it.** The required annualized Sharpe itself, derived from `cost/φ(0)`:

| Rung | Required annualized Sharpe |
|---|---|
| 5m | 18.3 |
| 15m | 10.6 |
| 1h | **6.46** |
| daily | **1.08** |

**Consequence.** The 1h rung is not a business — 6.46 is roughly triple the best documented
systematic programs. **The 1h Accumulator is cut.** Only the daily rung (1.08) is
physically achievable, and the same section establishes that no factor in this design
predicts at daily horizons — so the Accumulator is blocked, not merely re-sited.

The one inference the old column *did* support survives, because it is one-sided: where
cost exceeds the edge a generous drift assumption produces, the rung is dead under any
assumption. That is why the 5m conclusion stands.

---

## C2 — The Scalper's gamma ladder was decorative

**What was published.** §6 argued that a 0.1% spot move is worth +26¢ with 4:40 remaining,
+33¢ at 2:30 and +44¢ at 1:00, against a ~5¢ round trip — presented as evidence that
near-expiry gamma made the trade easy.

**Why it is wrong.** It held the spot move fixed *in percent* while the clock shrank. The
probability of a 0.1% move shrinks exactly as fast as the gamma grows. In the only units
comparable across τ:

```
required conditional move = round_trip / φ(0) = 0.125 σ of the remaining window
```

**invariant to τ and to σ.** There is no late-window sweet spot.

**Consequence.** The bar is an information coefficient of ~0.125 on the next 30–120 seconds
*after* our own latency, on a venue where [FINDINGS.md](FINDINGS.md) measured the
actionable window at 5–12ms. **The Scalper is cut.** Two further contradictions were
recorded: the claim that "path volatility, not drift, so §4 does not apply" is false (edge
scales as `μ·t_hold/√τ_rem`, so early exit *reduces* edge while doubling the toll), and the
shared chassis' own ~8¢ staleness cap forbids essentially every trade the strategy existed
to take.

---

## C3 — The book-switching formula was missing a Jacobian

**What was published.** §4 derived `τ = (T·σ/μ)²` as the horizon required to reach a target
edge `T`, and made its premise the first measurement (M0).

**Why it is wrong.** `T` is an edge in probability units; converting to a standardized tilt
requires dividing by `φ(0)`. The correct form is `τ = (T/(φ(0)·(μ/σ)))²`, larger by
`1/φ(0)² = 6.3×` in τ. At T = 2.75¢ and a daily Sharpe of 0.05 the published formula
returned 7.3 hours where the correct value is 45.6 hours. **That error is precisely what
made the hourly book appear viable.**

**Second, independent ground for withdrawal.** The rule requires `μ/σ` to differ across
regimes. By Merton (1980), `SE(μ̂) = σ/√T` depends only on calendar span, not sampling
frequency — finer bars buy nothing. Detecting a 0.5–1.5 Sharpe difference needs decades
against ~9 years of available history; the eight-asset panel improves the standard error by
only 5–16% because the assets are correlated.

**Consequence.** Switching rule withdrawn; M0 withdrawn. One rung, always. The regime
layer's `E[duration]` output is also dropped as a trading input — geometric duration is
memoryless, so expected residual life is constant per state and carries no timing
information.

---

## C4 — The promotion gate benchmarked against a null that cannot fail

**What was published.** M2 promoted a model on "beats the σ-only prior (Φ(z)) on Brier
out-of-sample."

**Why it is wrong.** If the model uses a subset of the information already in the market
price, then `Brier(market) ≤ Brier(ours)` unconditionally. Beating Φ(z) establishes only
that something was added to a driftless Gaussian — almost any signal clears that — and
carries no information about profitability.

**Aggravating factor.** This project had already pre-registered the correct bar in
`docs/PIVOT_2026-07.md` §4 ("Brier score vs the market-implied baseline; skill = market
Brier − ours ... AND simulated PnL 95% CI > 0 over ≥30 resolutions") and **implemented it**
in `tools/forecast_journal.py`. The design regressed against the repository's own shipped
tooling.

**Consequence.** Φ(z) is demoted to a diagnostic. The promotion gate is paired Brier
against the fee-adjusted **executable ask with size** — never a midpoint, since on a 2¢ book
the midpoint error is 1¢, 36% of the entire cost stack.

---

## C5 — Strategy 1 and Strategy 3 were mutually inconsistent

**What was published.** §5 (Accumulator) exists because μ ≠ 0 at the traded horizon. §7
(Sigma Gap, formerly "Vola trade") inverts the price to σ_implied under an assumption that μ = 0. Run together
they take opposite sides of the same residual.

**How large.** §7's worked example — spot 0.25% above strike at 30 minutes remaining, a 5¢
gap read as "the market prices ~32% more vola" — is **fully reproducible from drift alone
with zero vola disagreement**: at z = 0.69, `φ(0.69) · 0.163 = 5.1¢`, the entire claimed
edge. The example has been removed.

**Consequence.** σ_implied is not identified without also handling drift, tail shape
(~3.8¢ at z ≈ 0.69 under a standardized t₅, peaking in the intended trading band), Jensen
bias from plug-in pricing (+0.6¢ to +1.4¢, fixed sign — a term ~1000× larger than the rate
term [MODEL_STACK.md](MODEL_STACK.md) §3 carefully dismisses), and attenuation (you capture
`k·gap`, and `k < 0` whenever `ρ·σ_err(ours) > σ_err(market)`). The QLIKE gate is replaced
by a forecast-encompassing regression, and a Deribit implied-vol comparison — free and
uncontaminated — runs first.

---

## C6 — Smaller corrections

| Claim | Status |
|---|---|
| "The tradeable region away from 50¢ is where the fee parabola is cheapest" | **Wrong in risk-adjusted terms.** The half-spread does not scale with `p(1−p)`: `cost/√(p(1−p))` is 0.0550 at p=0.50, 0.0530 at 0.80, rising to 0.0706 at 0.97. Flat to ~4% across the book, worse at the wings. No fee escape hatch |
| "The direction layer is self-disarming — β→0 if flow predicts nothing" | **True in expectation, false in every finite sample.** With k factors and true β=0, phantom tilt is `φ(0)·√(k/N_eff)` ≈ 5¢ at the daily rung with this design's 11 candidates — *below* the 8¢ staleness cap. This is the f45 mechanism |
| Depth figures ($2.5k / $4.7k / $36.7k) used as the cost basis | **Book totals, not size at touch.** v1.0.0 measured 250–350 shares at the touch on the 5m book. Slippage is not ≈0; the cost stack must become a size-dependent function measured from recorded L2 |
| Two-stage fit on σ̂-standardized returns | **Wrong estimator.** The model is a probit with `z` as offset; fit it directly by MLE with a ridge prior. `x` must be orthogonalised against `z` first — `ln(S/K)` *is* the return this window's flow caused |
| Daily rung inherits the tie-mass correction | **No.** Verified 2026-08-10: daily ties resolve 50-50, hourly ties credit Up. The correction does not transfer |

---

## What the review confirmed as sound — do not change

- The fee model and cost accounting (`0.07·p·(1−p)` at entry, verified against real fills).
- Proper scoring rules as the primary ruler — correct *and* 5–20× cheaper than a PnL
  verdict. Only the opponent was wrong.
- Killing 5m directional holding. The conclusion survives its own flawed table because the
  inference is one-sided.
- `Φ(z + βᵀx)` as the functional form — it is a probit with `z` as offset, the coherent way
  to inject direction. The estimator was wrong, not the form.
- Flat sizing on Kelly-degeneration grounds; rejecting EGARCH/GJR on measured wrong-signed
  BTC leverage rather than taste; "filtered, never smoothed"; "returns across venues, levels
  never"; the factor-hygiene checklist; the killed-hypotheses table.
- The settlement-alignment insight, now verified: the 1h and daily rungs settle on Binance
  data, so backtest series and settlement series are identical.

---

## Second review round (2026-08-12) — additional corrections

- **C7 — Asset misclassification (fatal to the trading-home thesis).** The design claimed
  the trade's home is the "long tail without a liquid options market" (DOGE, BNB, HYPE,
  ZEC; SOL/XRP unclassified). Verified live: Deribit lists USDC-linear options on SOL
  (536), XRP (340) and HYPE (330); and the genuinely optionless assets (DOGE/BNB/ZEC)
  have no usable Polymarket books at 1h/daily (DOGE daily ≈ $14 liquidity, 96¢ spread).
  Thesis withdrawn; program re-homed to BTC/ETH. Root cause: classification asserted from
  memory instead of a live query, and long-tail liquidity assumed instead of measured.
- **C8 — Hedge cost omitted from the cost stack.** At 1h, hedge notional is 45–80× face;
  perp round-trip alone is 1.6–2.9¢ (maker) to 4–7¢ (taker) per $1 face vs a 2.75¢ total
  stack — the hedged 1h variant is dead on arithmetic. Daily survives (9–16× face,
  0.3–1.5¢) and the hedge line is now a mandatory stack component.
- **C9 — OR/AND divergence.** STRATEGY_DESIGN restated M1's frozen kill condition as an
  OR; the pre-registration says AND (kill only if both rungs fail). Conservative in
  direction (could only produce false total-kills) but wrong; the pre-registration
  governs. Fixed.
- **C10 — Deribit-gate recurrence of C4.** The "cheapest killer" was specced as a raw
  QLIKE contest vs Deribit IV — pass-biased for the same VRP reason C4 killed the
  market-QLIKE gate. Re-specced: VRP-debiased, term-matched benchmark; fail = kill,
  pass = uninformative. Gate registry renamed (G1/M2′) with a never-reuse rule.

---

# Third review round (2026-08-13)

Five-lens review, top findings put to verifiers instructed to refute. What survived, and
only the part that changes what gets built:

- **C11 — The prize was never computed.** No document multiplies edge × clip × frequency.
  From the design's own inputs: gross ceiling ≈$5–55k/yr, central $1–3k/yr; at the doc's own
  ~3% prior, EV is a few hundred dollars. Order-of-magnitude only — the inputs are what the
  recorder is being built to measure. [FINDINGS.md](FINDINGS.md) §6 is routinely misquoted
  as killing the 5m program on prize size; it does not. It says a small absolute prize is
  *why* such pockets survive for retail, and the 5m program died on burden of proof.
  **Decision D1: this is a research program. Resource it like one.** Recorded data is the
  deliverable; no live path until a gate says otherwise.
- **C12 — M1's daily kill-leg was vacuous.** Under the null the daily leg clears its own bar
  19–27% of the time, so the AND-kill fired against its own null ~5% of the time; and the
  phantom-tilt guard needs N_eff > 1,601 at daily (3.9 yrs) against 743 available (1.8 yrs),
  so the cell was unreportable either way. The 1h leg clears the same guard by >10× (17,800
  available vs 1,073 needed). **Decision D2: the 1h leg alone carries the kill.** No
  threshold moved. Dated amendment in `docs/preregistrations/M1_ofi_decay.md` — that file
  governs, not this one.
- **C13 — M2′ is not identified as specified.** `S` is taken at decision time; the recorded
  quote carries an unknown lag. That EIV bias produces k̂ > 0 under the exact null M2′ exists
  to reject (~0.36 against a tested 0.5), concentrated in burst states — where we trade.
  **Fix is in the recorder: capture `S` at quote receipt, timestamped.** Cannot be
  retrofitted to data recorded without it.
- **C14 — The hedge line priced perp fees only.** Add funding carry (0.3–0.5¢/day normal,
  3–5¢ stressed — and the stressed regime *is* the trade), margin collateral ~1.8–3.2× face,
  the cross-venue variation-margin path (perp losses cash out on Binance while the binary
  stays locked to noon ET), and 0.5–1.6¢ basis noise. The quoted ~8% bar is 1h, unhedged,
  pre-attenuation. Daily's all-in bar is roughly 2× that; derivation belongs in the M2′
  pre-registration, not in prose.
- **C15 — Staleness duration is unmeasured and load-bearing.** The edge needs a maker's quote
  to *still* be stale when a retail taker arrives, but the chassis stands down on the BOCPD
  alarm and the 8¢ cap forbids break-sized gaps. v1.0.0 measured claimed edges >15% at −36%
  to −57% ROI — adverse selection, not staleness. Nearly free to measure from the recorder
  plus a Deribit feed. Flagged, not verified.
- **C16 — Sample-size arithmetic.** "27–67 obs/day" is impossible: BTC/ETH at 1h is 48 raw,
  ~29–31 correlation-adjusted; 67 needed the withdrawn 8-asset scope. Verdict window 6–25
  months, not 4–12.
- **C17 — The recorder spec was unsatisfiable.** Build item 5 records top-of-book; item 6
  requires a cost model "measured from recorded L2". Scope stated three different ways. **The
  recorder is the only blocking build item and recorded time cannot be backfilled.** Spec of
  record: full-book L2 + trade tape ≥1 Hz + `S` timestamped at quote receipt, 1h and daily,
  BTC/ETH.
- **C18 — Wrong facts, fixed in place.** §1 says Chainlink settlement and ties-credit-Up;
  both in-scope rungs settle on Binance candles and daily ties resolve 50-50. §7's worked
  example (71¢ at a 1.1% lead) implies 1.98%/day at τ=1d, not the stated 2.6% — that needs
  an unstated τ≈13.9h. §9's live-loss row: 6.27 − 23.51 = −17.24, not −19.35. §5's "μ̂
  survives in the pricing core" contradicts §7's μ̂ = 0 kernel. §7's SOL/XRP/DOGE/BNB/HYPE/ZEC
  corollary is refuted (C7) and still present. MODEL_STACK still carries four superseded
  specs: "self-disarming" direction layer, the two-stage fit, `E[duration]`, Whalley–Wilmott.
  1¢ tick quantization (±2–4% of σ) is in no contaminant table.

**Refuted by the verifiers — no action:** the Required-Sharpe table (correctly scoped to
sustained-drift signals; only the prose needs an "unconditional" qualifier), the ≈8% bar
itself, the −σ²τ/2 term's coverage by the μ̂ = 0 pin, and the Scalper / book-switching /
`E[duration]` kills.
