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
