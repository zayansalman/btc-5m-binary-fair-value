# Race Loop Charter — autonomous assess/iterate cycle

Scheduled agent loop (cron `23 */6 * * *` local, durable, created 2026-07-07). Each firing
executes **one iteration**: assess → advance one backlog item → log. The mission is to reach a
**kill-or-deploy verdict on `cushion_fresh_v7`** with maximum evidence per day and **zero
interference** with the running bot or the race's integrity.

## 1. Guardrails (BINDING — no exceptions)

1. **Never touch the bot lifecycle.** No start/stop/restart, no mode flips, no strategy/model
   flips, no orders, no POSTs to the dashboard. Read-only observation. If the bot is down and
   the race is not accruing, say so loudly in the log — the operator restarts it.
2. **Ledger is read-only.** Query only an immutable snapshot:
   `cp data/btc_5m_binary_fair_value.db $SCRATCHPAD/ledger.db` then
   `sqlite3 "file:$SCRATCHPAD/ledger.db?immutable=1"` (direct read-only opens fail under the
   sandbox; `PRAGMA integrity_check` after copy).
3. **Tests never hit the live DB.** Always `DB_PATH=<tmp> pytest …`.
4. **CI/CD**: GitHub issue before code → branch `feature/<issue>-<slug>` from `develop` →
   tests + build green → merge/push to `develop` only. **Never touch `main`.**
5. **No failed spikes in develop**: negative/abandoned experiments are documented in the issue
   and the branch is deleted unmerged.
6. **No in-sample gate mining.** New gates/params enter only via hypotheses pre-registered in a
   GitHub issue BEFORE running, evaluated in `tools/replay_race.py` over full history with a
   pre-race OOS segment. The racing specs (v0/v2/v7/v8) are frozen — never modified mid-race.
7. **Analysis discipline**: fee-true only; common start `2026-07-02T14:50:50`; settled only;
   daily decomposition (never rank on a multi-day sum alone); per-side attribution; win rate is
   NOT the objective (expectancy is).
8. **Dead ideas stay dead** (never re-propose): night/hour gates (02–04 replication p=0.51),
   maker/passive execution, drift & cross-market feeds, regime auto-selection, scalp exits.
9. Docs: never hand-edit `docs/FILE_MAP.md` or `<!-- GENERATED -->` blocks.

## 2. Iteration procedure

A. **Assess** (always): run `.venv/bin/python tools/race_status.py` (SHIPPED #150, f0333f0) —
   it immutable-snapshots the ledger and prints per-model standings (n, total, mean/trade,
   z-CI + bootstrap CI, WR vs fee breakeven `p + 0.07·p·(1−p)`, maxDD, today delta), the live
   book, bot state/mode/heartbeat, and the deploy-bar tracker (required n + ETA to CI>0). Add
   `--json` for machine parsing. Then eyeball `notification_feed` for any operator activity and
   note the delta vs the last log entry. (Only drop to ad-hoc SQL for a cut the tool doesn't
   surface, e.g. per-side attribution or freshness slices.)
B. **Develop** (one item per iteration, in order): ~~#149~~ ✔ → ~~#150~~ ✔ → ~~#151~~ ✔ →
   ~~#138~~ ✔ → ~~#122~~ ✔ → next candidates: **#155 (add f45 to shadow roster —
   OPERATOR-GATED, agent preps only)**, #157 (tick-cadence observability — the watchdog is
   blind to journaling gaps during feed flapping), #137 (maker/taker fill telemetry). File new
   issues before working, speculative ideas as `[P2]`. Roster/lifecycle changes are
   recommend-only.
C. **Log**: append a dated entry to `tasks/race_log.md` (format below), commit both docs to
   `develop`, push.

## 3. Log entry format

```
## YYYY-MM-DD HH:MM UTC (iteration N)
- Race: v7 +$X.XX/nNN (Δ +$X/nN since last) · v8 … · v2 … · v0 … ; today: …
- Deploy bar: mean $0.XX, CI [x, y]; needs ~N trades (~D days) at current estimate
- Live book: lifetime −$XX.XX (Δ …) ; bot: <mode/state/heartbeat> ; race accruing: yes/no
- Health flags: …
- Advanced: #NNN <what shipped / found> ; Next: …
```

## 4. Decision framework (pre-registered — recommendations only; the operator flips switches)

Per `docs/POSTMORTEM_2026-07.md` restart protocol (2026-07-02):

- **Deploy recommendation**: full-race 95% CI > 0 net of taker fees AND sign-consistent OOS
  half. Live stays OFF until a model clears this.
- **Kill recommendation (v7)**: race cumulative mean/trade < 0 at n≥150, OR the #149 extended
  replay fails to reproduce CI>0 on its OOS segment while the race mean sits < +$0.10 at n≥100.
- **Program sunset**: nothing clears the bar by ~8 weeks from 07-02 → recommend retiring live
  trading (per postmortem).
- On reaching any verdict: write it at the top of the log, notify the user, **stop re-arming
  the cron**.

## 5. Cron upkeep

Recurring jobs auto-expire 7 days after creation (created 2026-07-07; expires ~2026-07-14 with
one final fire). If within 36h of expiry and no verdict yet: re-create via CronCreate
(`23 */6 * * *`, durable, same prompt) and note the re-arm in the log.

## 6. Standing context (evidence base — do not re-derive)

- Race = 4-model ablation on identical sizing (~5 sh/$2.70): v0 base; v2 = +cushion;
  v8 = +fresh(entry ≤60s into window); v7 = fresh+cushion+edge-cap(0.065). Clean subsets:
  v7 ⊂ v8∩v2 ⊂ v0. Day-5 ablation: the PnL lives in fresh∩cushion (v7 +$12.2/47); fresh-only
  windows −$11.4/65; cushion-only −$8.4/91 — the interaction, not either gate alone.
- Fee-true verified: booked pnl matches 0.07·p·(1−p)·shares exactly (loss = notional+fee).
- Live fill fidelity at 5-share size is good: 16-fill probe (07-06/07) matched shadow with
  small POSITIVE entry slip on every matched window (#137 tracks telemetry).
- Lifetime real-money book: **−$19.35 / 351 fills** (June era −$15.28, fees > 100% of loss;
  v7 probe −$4.07/16, ended by the daily trailing halt 2026-07-07 06:40 UTC).
- Capacity ceiling: ~$140–200 resting at ask; at 8–11 trades/day and +$0.18–0.34/trade the
  bull case is ~$2–4/day at current size. Keep expectations calibrated in every report.
