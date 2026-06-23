# Maker-mode strategy — design (#130)

## Goal
Decide whether running `fair_value_v0` as a passive **maker** (resting limit orders) beats the
current **taker** mode (crossing the spread), using recorded data with a **realistic** fill model.
The whole value is in not recreating the paper-vs-live mirage that has burned this system
(`live-edge-unproven`, scalp-soak). An honest result that says "maker is worse" is a success.

## Non-goals
- No live orders, no change to the live model (shadow-first; the agent never flips live).
- Phase 2 (a forward live maker shadow) is **gated** on Phase 1 and out of scope here.
- Not a new signal — same `fair_value_v0` signal; only the **execution mode** differs.

## Architecture (Phase 1 — this branch)
- `tools/maker_backtest.py` — offline replay + report (sibling of `tools/regime_attribution.py`).
- `btc_bot/shadow/fees.py` — add `maker_fee_per_share(price, fee_rate)` next to the taker fee.

## Data sources (read-only)
- **Opportunity set / taker arm:** settled rows of `btc_model_shadow_positions` where
  `model_id='fair_value_v0'`. Each row gives `(window_slug, created_at, side, entry_price[=ask
  taken], shares, fair_prob, won via realized_pnl_usd>0, taker realized_pnl_usd)`. This IS the
  taker baseline (already net of the taker fee via the shadow ledger).
- **Book paths / maker fills:** `btc_paper_ticks` for the same `window_slug` — per-tick
  `created_at, remaining_seconds, up_best_bid/ask, down_best_bid/ask, market_up_price,
  market_down_price`.

## Maker fill model (the crux — "backtest like reality")
For each opportunity, simulate a resting BUY of the bet side at limit price `L`:

1. **Set L at signal time** from the book AT the opportunity's `created_at` tick (per policy below).
2. **Forward-only fills.** Evaluate fills ONLY on ticks with `created_at` strictly AFTER the signal
   tick. The order never "sees" the tick it reacts to (our feed is ~5s stale — modeling that lag).
3. **Conservative fill rule.** Filled at the first later tick (within the window, before the cutoff)
   where the bet-side **mid trades through L** (`mid ≤ L`). Mid-crossing is a strong, conservative
   proxy for a real trade-through given we only have ~5s top-of-book snapshots (no trade tape).
   When in doubt, no fill.
4. **Queue / fill-rate haircut.** At the bid we sit behind existing size, so report results at
   100% and 50% of would-be fills (random-but-seeded drop) to bound queue optimism.
5. **Cutoff.** No posting/filling in the last `CUTOFF_SECONDS` (default 30s) — a maker would not
   rest into settlement chaos. Configurable.
6. **Settlement is deterministic per window**, so a filled position settles to the SAME recorded
   outcome (`won`) as the taker row. PnL = `shares * maker_net_pnl_per_share(L, won, maker_fee)`.
7. **No fill ⇒ no trade**, PnL contribution `$0`; record whether it would have won as a taker
   (missed-winner accounting).

### Limit-price policy sweep
- `join_bid`: `L = bet-side best_bid` (most passive, biggest improvement, lowest fill).
- `mid`: `L = (best_bid + best_ask)/2`.
- `fair`: `L = fair_prob` (post at model fair value; only fill at edge-non-negative prices).

## Metric that decides it — per OPPORTUNITY, not per fill
A maker that fills 40% at +5¢ better but skips 60% is only good if it skips the *losers*. Per policy:
- `n_opportunities` (= taker trades), `n_filled`, `fill_rate`
- avg entry improvement `= taker_ask − L` (per filled)
- **expectancy per opportunity**: `maker_total/n_opp` vs `taker_total/n_opp` (unfilled = $0) ← headline
- expectancy per fill, win rate (secondary)
- **adverse selection (the real verdict):** fill rate among taker-winners vs taker-losers;
  count + forgone PnL of *missed winners* (no-fill windows that won as taker); of filled buys,
  fraction whose mid kept falling below L after fill (falling-knife continuation).
- **sensitivities:** `maker_fee=0`; fill haircut 50%.

## Fee model
`maker_fee_per_share(price, fee_rate) = fee_rate * price * (1-price)`, default `fee_rate=0.07`
(= taker; no assumed rebate). Report a `maker_fee=0` sensitivity separately.

## Outputs
Printed report + structured dict (JSON-able), like `regime_attribution`. Per-policy table + the
adverse-selection block + sensitivities. Optional CSV of per-opportunity rows for audit.

## Testing (DB-isolated; `tests-hit-live-db`)
- `fees.maker_fee_per_share` math (zero at 0/1, peak at 0.5).
- Fill sim on synthetic tick paths: crosses L ⇒ fill at L; never crosses ⇒ no fill; a *pre-signal*
  cross does NOT fill (forward-only); cutoff respected; haircut drops the right fraction.
- EV accounting: unfilled counted as $0 in per-opportunity; missed-winner flagged correctly.

## Risks / honest caveats
- Top-of-book ~5s snapshots, not the trade tape ⇒ fill model is approximate; mitigated by the
  conservative mid-cross rule + haircut sensitivity.
- The mid-cross proxy can still over- or under-state fills; the 50% haircut bounds the optimistic side.
- Adverse selection from latency is real and partially modeled (forward-only + the diagnostics);
  the live result could still be worse than this backtest. State that in the report.
- Maker fee on Polymarket is uncertain; default = taker, fee=0 shown as upside.

## Phase 2 (gated, separate branch)
If Phase 1 shows a real per-opportunity edge that survives the haircut and adverse-selection
checks, add an `execution_mode` to the shadow runner so `fair_value_v0` logs a maker variant
forward, reusing this fill logic. Decision is the operator's after seeing Phase 1.
