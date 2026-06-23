# Maker-mode backtest — findings (#130)

**Verdict: NEGATIVE. Do not pursue passive maker mode.** Phase 2 (live maker shadow) is
gated on a positive Phase 1; the gate failed. `fair_value_v0` stays a taker.

## Result (`tools/maker_backtest.py`, ~360 fair_value_v0 opportunities, 30s cutoff)

Per-opportunity EV (maker fee = taker 0.07, full fills). Taker baseline is the shadow
ledger (+$51 / +0.143/opp — itself fill-inflated; real live taker is ≈ breakeven/negative):

| policy | fill % | entry improvement | maker $/opp | maker total | vs taker total |
|---|--:|--:|--:|--:|--:|
| join_bid | 85% | +1.06¢ | **−0.10** | −$36 | +$52 |
| mid | 85% | +0.53¢ | **−0.12** | −$43 | +$52 |
| fair | 97% | −5.60¢ | **−0.18** | −$64 | +$52 |

Maker flips a +$52 (inflated) taker result into −$36 to −$64 on **every** policy.

## Why — structural adverse selection (the whole story)
- **Fills the losers, misses the winners:** fill rate among eventual **losers ≈ 98%** vs
  **winners ≈ 75%**. When the bet side is going to lose, price falls to your buy limit and
  you get filled (falling knife); when it's going to win, price runs up *away* from your
  limit and you never fill.
- **Missed winners:** ~52 winning trades skipped, forgoing **+$112** of the taker PnL — the
  quantified opportunity cost of resting passively.
- The +1¢ entry improvement is real but trivial against this selection.

## It is NOT a fee problem
With **maker fee = 0** (the rebate upside we don't even have), per-opportunity EV is still
negative on the real policies: join_bid −0.035, mid −0.054, fair −0.099. The 50%-fill haircut
nudges join_bid to +0.009/opp — but that only "works" by randomly dropping half the fills
(diluting the adverse selection); it's not a strategy and it's noise-level (per-trade SD ≈ 2.4).

## Caveats (honest)
- The "fair" policy is degenerate here: `fair_prob` sits above the ask, so posting at fair
  would cross and take at a *worse* price (improvement is −5.6¢) — it's not really making.
  The meaningful maker policies are `join_bid` and `mid`, both clearly negative.
- Fill model = bet-side **mid crossing the limit** on a *later* tick (forward-only),
  conservative, with a queue haircut. Top-of-book ~5s snapshots, not the trade tape — but the
  adverse-selection result is a directional/structural effect, robust to fill-calibration.
- The taker baseline is the shadow (assumed-fill) number; the real live taker is worse, so the
  real maker would be worse still. Either way maker < taker.

## So what
Passive making loses here because we'd be picked off (fill the falling knives, miss the
runners) and we have neither the speed to avoid it nor a maker rebate to pay for it — exactly
the "edge is latency, not fair value" point. **No live maker shadow.** The lever the data keeps
pointing at remains **entry selectivity**, not execution mode.
