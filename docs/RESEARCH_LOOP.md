# AI Research Loop (design — build once live fills accumulate)

The defensible way to "leverage AI" on this strategy. NOT a price predictor
(an LLM forecasting 5-minute BTC loses to latency bots reading the same
Chainlink feed). Instead: **AI as a research analyst over our own journal**,
inside a closed, human-gated loop.

## The loop

1. **Mine** — on a schedule (e.g. nightly), an agent reads the trade journal
   (`btc_paper_positions`, `btc_paper_ticks`, `btc_live_orders`) and the
   recorded book/Chainlink archive, and finds where PnL concentrates:
   by hour-of-day, volatility regime, entry-price band, claimed-edge band,
   side (Up/Down), time-to-roll at entry, fill quality (live vs paper).

2. **Hypothesize** — it proposes concrete, testable changes: new entry
   filters, feature tweaks (e.g. a vol-regime gate), sizing adjustments.
   Each hypothesis is a parameter/filter delta, not a vibe.

3. **Backtest** — every proposal is replayed on the recorded archive
   **walk-forward, out-of-sample** (train window → test window, never
   in-sample), using the existing replay harness. Selection-bias and
   multiple-testing caveats are reported (N hypotheses tried → expected
   false positives).

4. **Surface** — only proposals that clear OOS with a CI excluding zero are
   written up for the operator, WITH numbers (ROI, n, win rate, drawdown,
   per-half stability). Everything else is logged and dropped.

5. **Approve** — the operator decides. Approved changes update config
   (e.g. `BTC_PAPER_ENTRY_EDGE_MAX`); nothing auto-applies to live.

**AI proposes, human disposes.** The loop never changes a live setting on its
own — that is the line between adaptive research and curve-fitting yourself
into a blow-up.

## Why human-gated, not autonomous

With a $30–40 bankroll and a few trades/hour, the live sample is tiny and
noisy. Autonomous RL / auto-tuning would overfit to that noise and amplify
variance. The operator gate + mandatory OOS validation is the overfitting
firewall.

## Prerequisites before building

- Enough live fills to measure the paper-vs-live fill gap (the one thing the
  archive can't simulate). Target: ≥ ~50 live settles.
- The adaptive risk controller (#36, shipped) already provides the safety
  floor (edge-decay auto-pause) this loop's experiments run beneath.

## Status

Designed, not built. Build after the first live soak yields real fills.
