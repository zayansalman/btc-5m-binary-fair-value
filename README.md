# BTC 5m Binary Fair Value — a trading lab that stopped itself on evidence

**Status: ARCHIVED (research complete, 2026-07-10).** This repository is the full record of a
30-day quantitative research program on Polymarket's BTC 5-minute Up/Down binary markets:
a fair-value model, a paper/live execution stack, a five-model shadow "race", a validated
tick-replay backtester, and an autonomous agent ops-loop — ending in a **rigorous negative
result**, reached before it could get expensive.

> **The finding:** at retail latency, 5-minute BTC direction is a coin flip priced correctly
> to within the venue's taker fee. Every apparent edge (seven model variants, three
> "race leaders") died on contact with data recorded *after* the decision that found it.
> The actors who do profit are execution businesses — venue-subsidized market makers and
> sub-100ms latency snipers — not predictors. Total real-money cost of the answer: **−$19.35
> across 351 fills**, with taker fees exceeding 100% of the loss.

Most trading repos claim an edge. This one demonstrates the machinery for proving you
don't have one — which is the harder and more valuable build.

---

## Headline results (all venue-true, fee-inclusive)

| Question | Answer | Evidence |
|---|---|---|
| Does the fair-value model have directional edge? | **No** | Unfiltered control: ≈$0 over 400+ settled shadow trades |
| Do freshness/cushion/edge-cap gates create edge? | **No** | All 7 variants regressed to null as n grew; final candidate's post-freeze segment: −$0.41/trade, WR 48.6% (n=37) |
| Does switching to the recent leader help? | **No** | Simulated on own race data: follow-the-leader +$3–6 vs hold +$16.60 — switching buys the day *after* the big day |
| Do regimes (time/edge/vol/basis) hide an edge? | **No** | A-priori bands, side-attributed, permutation + FDR: 0/12 and 0/75 cells survive |
| Where did the live losses actually go? | **Fees** | Gross +$6.27 vs taker fees −$23.51 (June era); realized WR 55% vs fee breakeven 55.7% |
| Who *does* earn here? | **Execution businesses** | Makers get 0 fees + 20% rebates of taker fees + a >$5M/month liquidity-rewards pool; our resting-quote backtest measured their adverse selection (fills 98% of losers, 75% of winners) |
| Could passive (maker) execution fix it? | **No** | Backtested: adverse selection, not fees, is the maker's rent — from a slow quoter it's a loss |

## Why this repo is worth reading

The negative result is load-bearing because of *how* it was produced:

- **Fee-true accounting everywhere.** One fee model (`btc_bot/shadow/fees.py`,
  `0.07·p·(1−p)` per share) settles the shadow ledger, the live book, the replayer, and the
  breakeven math identically. The books were reconciled to the venue's own Data-API
  cashflows when they disagreed.
- **Pre-registration before evaluation.** Every gate variant was written down as a
  hypothesis in a GitHub issue — thresholds frozen — *before* the replay that judged it
  (#144, #149, #162). In-sample slicing was treated as hypothesis generation only.
- **A validated simulator.** `tools/replay_race.py` reconstructs window outcomes from the
  settlement feed's own next-window print (**100.00% agreement** on 1,043 ground-truth
  outcomes) and reproduces the recorded shadow ledger **exactly** (side and entry price) —
  so when the replay disagreed with hope, the replay won.
- **A clean ablation, not a bake-off.** The five racing models form strict subsets
  (`f45 ⊂ v7 ⊂ v8∩v2 ⊂ v0`), so each gate's *marginal* PnL is directly measurable — which
  is how "the edge lives in the fresh∩cushion interaction" was isolated, and later how it
  was watched decaying to zero out-of-sample.
- **Kill criteria written before the data arrived.** Deploy bar (95% CI > 0 net of fees,
  sign-consistent segments), kill floors, and a sunset date were pre-registered in
  `docs/POSTMORTEM_2026-07.md` and enforced — including against three tempting
  "race leaders" that later collapsed, and finally against the project itself.
- **Multiple-testing discipline.** Regime and slice claims had to survive
  Benjamini–Hochberg FDR and one-vs-rest permutation tests. None did, twice.
- **An agent-operated ops loop** ran assess→build→log cycles every 6 hours
  (`tasks/race_loop.md` charter with binding guardrails; `tasks/race_log.md` is the full
  audit trail), shipping 13 PRs of instrumentation while never touching the live gate.

## Architecture

Two coupled trees plus a small shared foundation:

```
btc_bot/                  # the live loop + signal math
├── paper.py              #   tick loop, snapshots, settle-style position lifecycle
├── controller.py         #   start/stop, watchdog (#147), silent-stop detector (#138)
├── strategy.py           #   fair-value math + executable-edge signal (pure)
├── params.py             #   operator-tunable runtime params
└── shadow/               #   the model race
    ├── signals.py        #   candidate strategies as PURE functions (view → signal | None)
    ├── runner.py         #   roster, per-tick recording (idempotent), live dispatch
    ├── ledger.py         #   INSERT OR IGNORE journal + fee-true settlement
    └── fees.py           #   the single Polymarket taker-fee model

btc_5m_fv/                # execution / connectors / ops
├── core/                 #   domain types, interfaces, exceptions
├── strategy/  connectors/  storage/  backtest/
├── execution/            #   paper lifecycle + LIVE executor (multi-gated) + RiskGate (#64)
└── ops/dashboard/        #   FastAPI operator dashboard (SSE), panels, runtime controls

config.py  db.py  logging_setup.py   # foundation: env parsing, SQLite + migrations, structlog
tools/                    # research instruments (see below)
tests/                    # 828 tests, network-free, DB-isolated
```

**Design decisions that carried the project:**

- **Pure signal functions.** Every candidate strategy is
  `fn(SnapshotView, params) → ShadowSignal | None` over **frozen dataclasses** — no I/O, no
  clock, no DB — so the same function is unit-tested with hand-built fixtures, raced live
  by the runner, and replayed over history by the backtester with zero behavioral drift.
- **Idempotent journaling.** Shadow recording is `INSERT OR IGNORE` against a unique
  `(window, model)` index: crash-replays and duplicate ticks cannot double-count.
- **One risk path for paper and live.** The same `RiskGate` (per-trade cap, daily trailing
  loss-halt, bankroll cap) evaluates both modes, so paper is a faithful preview of live —
  and the halts fired correctly in production three times.
- **Live is multi-gated, never implicit.** Real orders require mode + literal confirm
  string + key + coherent wallet + clean config parse; any missing gate refuses to boot
  rather than degrade. A kill-switch file flattens and halts.
- **Defense in depth on ops:** singleton `flock` (two loops can never share one ledger),
  in-process heartbeat watchdog that respawns a wedged *paper* loop but only notifies for
  *live* (#147), silent-death detection with one-shot alerts (#138), tick-cadence
  monitoring that catches feed-flap journaling stalls the heartbeat can't see (#157).
- **Additive, backfilled migrations.** Schema evolves via `ALTER TABLE` maps + JSON
  backfills (e.g. maker/taker attribution recovered retroactively from journaled CLOB
  responses); tools tolerate pre-migration snapshots via `NULL AS col` selects.
- **Docs that can't rot.** `tools/gen_docs.py` generates the module map and test counts
  into `docs/` inside `<!-- GENERATED -->` blocks; a CI drift job fails if they're stale.
- **Secrets redacted at the sink.** Every string persisted to the ledger or notification
  feed passes through redaction; the private key never logs.

## The research instruments (`tools/`)

| Tool | What it does |
|---|---|
| `replay_race.py` | Tick-replay backtester over the full quote history; self-validating (outcome reconstruction + shadow-ledger reproduction); fragility grid for gate variants |
| `race_status.py` | One-shot fee-true standings: per-model CI (z + bootstrap), WR vs fee breakeven, deploy-bar required-n/ETA, live book, maker share, bot health & tick cadence |
| `regime_attribution.py` | A-priori regime bands (time/edge/vol/basis), side-attributed cells, two-sided edge gate, one-vs-rest permutation + BH-FDR |
| `shadow_performance.py` | Wilson win-rate bands + exact binomial tails vs fee-adjusted breakeven |
| `reconcile_live_ledger.py` | Reconciles the bot's books to the venue's public Data-API cashflows (found the fee-blind booking bug) |
| `forecast_journal.py` | The successor experiment (#162): pre-registered forecasting-skill pilot for *slow* markets — Brier skill vs market + fee-true simulated PnL, verdict gated at ≥30 resolutions |
| `chainlink_lead_lag.py`, `offline_replay.py`, `gen_docs.py`, … | feed lead/lag measurement, offline replays, docs generation |

## The strategy that was tested

Fair probability of finishing Up from a log-normal diffusion around the settlement feed's
reference print — `z = ln(spot/ref) / (σ·√t_remaining)`, `P = Φ(z)` — priced against
executable CLOB asks, entered only through layered gates (edge band 4.5–7%, favorites
≥ 0.50, first-60s freshness, spot-vs-strike cushion, claimed-edge cap), held to settlement,
sized ~$3 with a singleton-position constraint. Settlement alignment mattered: windows
resolve on Polymarket's Chainlink stream, not Binance (measured basis ≈ $50), so spot and
reference come from the settlement-aligned feed with per-component provenance journaling.

## Strategy design (reopened, 2026-08)

The archive above answers the question it set out to ask. A second phase, tracked in
issues #169–#177, is documenting a redesigned strategy **on paper before any code**, per
the resolution process in #170 (*discuss → write it down and confirm it makes sense
independent of any backtest → only then test on samples*).

- **`docs/STRATEGY_DESIGN.md`** — thesis, ingredients, signal families, the horizon
  analysis, and the three candidate strategies with their model rosters. **All three are
  currently blocked or cut** — see the corrections below.
- **`docs/CORRECTIONS.md`** — an adversarial review on 2026-08-10 found four errors serious
  enough to invert the design's central decision: a ranking table that was a tautology, a
  gamma argument cancelled by its own arithmetic, a switching formula missing a Jacobian,
  and a promotion gate benchmarked against a null that cannot fail. Current program
  (operator decision 2026-08-12): **Sigma-Gap-only** on the 1h and daily rungs — the
  Scalper is cut on arithmetic, the Accumulator parked (direction demoted to a nuisance
  parameter), and the Sigma Gap is blocked on identification until it beats Deribit IV
  out-of-sample and survives the encompassing regression. After the second adversarial
  review (2026-08-12) the program of record is BTC/ETH only — daily hedged, 1h unhedged —
  the "optionless long tail" thesis having been refuted by live Deribit listings and dead
  long-tail books. Honest prior on finding a real edge: **~3%**.
- **`docs/MODEL_STACK.md`** — every model in the stack: what it calculates, the mechanism,
  its published source, and whether it is reused, adapted or must be built.
- **`docs/FACTORS.md`** — every input: description, how it is computed, from which source,
  and why it is present — including the deliberate exclusions and their reasons.

Nothing in those three documents has been tested. They specify the measurement program
that would test them.

## Reading the record

- **`docs/FINDINGS.md`** — the seven findings in full, with the evidence for each.
- **`docs/ARCHITECTURE.md`** — the engineering tour: patterns, ops defense-in-depth,
  data layer, testing/CI, and the process discipline that made the negative result credible.
- **`docs/TIMELINE.md`** — the thirty days, event by event.
- **`docs/POSTMORTEM_2026-07.md`** — the June live era: fee-blind booking bug, venue-true
  re-accounting, the restart protocol this project then obeyed.
- **`docs/PIVOT_2026-07.md`** — the endgame decision memo: who actually earns on 5m markets
  (with the venue's fee/rebate schedule), why competing there was rejected, and the
  pre-registered slow-market pilot.
- **`tasks/race_log.md`** — the full audit trail: every race assessment, every shipped PR,
  every operator action, three false leaders, and the final verdict.
- **`tasks/race_loop.md`** — the agent ops-loop charter (guardrails, iteration procedure,
  pre-registered decision framework).
- **`docs/CODE_MAP.md` / `docs/FILE_MAP.md`** — generated routing map and module status.
- **`CHANGELOG.md`** — v0.1 monolith → v1.0.0 archive, PR by PR.

## Running it (archived, paper-only)

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[test]"
cp .env.example .env
./.venv/bin/python main.py          # dashboard at http://127.0.0.1:7860
DB_PATH=/tmp/t.db ./.venv/bin/python -m pytest tests/ -q   # 828 tests, DB-isolated
```

The ledger ships with 2,924 shadow positions across 10 model variants and the full tick
journal — every number in this README is reproducible from it:

```bash
./.venv/bin/python tools/race_status.py
./.venv/bin/python tools/replay_race.py --grid
./.venv/bin/python tools/regime_attribution.py --axis vol
```

Live trading remains multi-gated and OFF. It should stay that way; that's the finding.

## License

MIT — see `LICENSE`.
