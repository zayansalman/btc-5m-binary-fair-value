# Plan — Adaptive layer + go live (2026-06-14)

Preflight GO: MetaMask key in, Gnosis Safe funder `0xc1Daa…` holds $37.86, sig
type 2, wallet already approved (max allowances). Hard caps in code: $5/trade,
1 position, -$10/day halt, $30/day cap, kill switch.

## "Leverage AI" — the honest split
- NOT a price predictor (loses to latency bots on 5m BTC).
- YES adaptive risk control + an AI research analyst over OUR OWN journal.

## Phase 1 — Adaptive risk controller (#36, build now, protects from trade 1)
- [ ] btc_bot/adaptive.py: rolling expectancy / win-rate / Brier calibration
      over the last N closed trades of the active style (model_prob = edge +
      entry_price; outcome = realized_pnl > 0).
- [ ] Auto-pause when rolling ROI drops below a floor (after a min sample) —
      sticky until operator clears. Catches EDGE DECAY, not just a bad day.
- [ ] Config + entry-path integration + journal/dashboard + clear tool. Tests.

## Phase 2 — AI research loop (design now, build once live fills accumulate)
- [ ] docs/RESEARCH_LOOP.md: nightly agent mines journal → proposes filters →
      backtests OOS on the recorded archive → surfaces survivors for operator
      approval. AI proposes, human disposes. Never auto-applies to live.

## Phase 3 — Go live
- [ ] Final preflight GO → start live bot → verify boot gate + first real entry
      → hand off with kill switch (touch data/KILL).

## Won't do
- RL auto-tuning on live $37 (overfits/blows up — sample far too small).
- Any live-param change without OOS validation + operator sign-off.

---

# Live Executor Build — Issue #20 (2026-06-10)

## Plan
- [x] Fix runtime blockers (#19): main.py import, Binance endpoint, backtest retries — merged to develop
- [x] Paper bot running end-to-end (dashboard :7860, ticks in SQLite)
- [x] Backtest grid running on April history (background)
- [x] Implement live execution mode (py-clob-client) with hard risk limits
- [x] Adversarial review: financial-risk, API correctness, safety gates
- [x] Fix findings, tests green (395 passing)
- [ ] Push to develop
- [ ] Operator (Zayan) provides POLYMARKET_PRIVATE_KEY + BTC_LIVE_CONFIRM and launches live

## Review

Adversarial review (3 reviewers, 22 findings: 4 critical / 8 major / 10 minor)
— all addressed on this branch:

- **Persisted daily risk counters** — daily loss halt + daily bankroll cap now
  live in SQLite (`config` table) and are reloaded at executor start; Stop/Start
  or a restart can no longer reset them inside a UTC day.
- **No false closes** — a blocked/failed/unfilled live exit keeps the ledger
  row OPEN and retries next tick; SKIPPED (confirmed zero entry fill) closes
  with zero PnL. Realized PnL is recorded inside the executor on confirmed
  fills only.
- **Exit lifecycle** — exit SELLs are tracked, awaited (bounded by
  `BTC_LIVE_EXIT_FILL_TIMEOUT_SECONDS`) and cancelled on timeout; partial
  fills accounted per tranche; max-1 gate holds until confirmed flat.
- **Stop race eliminated** — controller waits for the runner thread, which
  flattens through the executor before dropping it; live rows are never
  paper-closed.
- **Boot reconciliation** — cancel_all + journal-based re-adoption of open
  positions at start; unreconcilable state refuses boot.
- **Kill switch** — re-arms on file deletion, TOCTOU re-check before entry
  POST, exits allowed under kill (flatten-only).
- **Boot gate hardening** — funder required for signature types 1/2, unknown
  signature types refused, malformed risk-limit env values refuse live boot.
- **Misc** — entry slippage guard, ledger-before-submit ordering for entries,
  cancel response verification + post-cancel matched capture + bankroll
  credit-back, py-clob-client pinned, dashboard docstrings made mode-aware.

## Data-integrity build (#21/#22/#23) — 2026-06-11
- [x] CLOB executable quotes in signal path + honest fills (agent impl, hand-finished)
- [x] Chainlink settlement connector (REST + WS) + REST spot-poll fallback (hand-added after WS 429)
- [x] Tie-rule fair value; degraded-feed entry/exit gating
- [x] WS feed lifecycle wired into run loop (was missing — agent died mid-build)
- [x] KPI re-baseline: pre-clob rows excluded (hand-added)
- [x] Dashboard state derived from runner thread both directions (#23)
- [x] 16 new tests; full suite green
Review: workflow died on spend limit mid-implement; gaps found and closed by hand:
feed never started, KPI quarantine missing, degraded ticks journaled phantom edge,
WS 429 needed longer backoff + REST spot fallback (verified live end-to-end).

## Execution plan — soak to live gate (2026-06-11, issues #25-#27)

Soak started 2026-06-11 05:28Z on the honest baseline (clob quotes, chainlink
settlement feed, spread-paying fills). KPIs count only quote_source='clob' rows.

### Phase 1 — let it soak (no action)
- [ ] 4-6h runtime, target 100+ closed clob-baseline trades

### Phase 2 — quality review (#25)
- [ ] PnL/win/drawdown by exit reason and entry-time bucket
- [ ] Churn analysis; spec per-window cooldown if confirmed (#27)
- [ ] Fill-realism haircut on expectancy (top-of-book persistence)
- [ ] Gamma-vs-CLOB staleness stats (post-mortem of #22)

### Phase 3 — stability + ops (#26)
- [ ] Tick-gap/crash audit; WS 429 recovery check (rest_poll -> chainlink_ws)
- [ ] CI green on develop (websockets, py-clob-client deps)
- [ ] Kill-switch drill in paper mode

### Phase 4 — live gate (operator decision)
- [ ] Present haircut-adjusted expectancy verdict
- [ ] If GO: operator sets POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER,
      BTC_LIVE_CONFIRM=YES_I_UNDERSTAND, BTC_BOT_MODE=live and presses Start
      (runbook: docs/OPERATIONS_RUNBOOK.md "Going live"). Caps: $3/trade,
      1 position, -$10/day halt, $30 ceiling, kill switch data/KILL
- [ ] If NO-GO: iterate on cooldown/thresholds or stop — the $30 stays

### Backlog (non-blocking)
- [ ] Backtest harness on Chainlink data instead of Binance
- [ ] develop -> main merge (requires explicit operator approval)
- [ ] Evaluate beta polymarket-client SDK migration (wallet-flow risk noted)
