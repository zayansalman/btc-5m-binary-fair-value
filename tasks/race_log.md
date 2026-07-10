# Race Log — cushion_fresh_v7 kill-or-deploy

Charter: `tasks/race_loop.md`. Newest entries at top after iteration 0.

## 2026-07-10 12:00 UTC (out-of-band — post-relaunch verification + pivot memo, operator "do whatever you think is right")

- **Relaunch verified healthy at +47min**: PID 37735 up, cadence 101/10min ✓, race accruing. **#122 confirmed working in production** — 10/10 new shadow rows carry sigma/spot at decision time. **f45 wiring confirmed correct**: the only v7 fire since relaunch was at "fresh 48s", which f45 correctly skipped (the 46–60s bucket it exists to exclude); no f45 row yet in 9 windows ≈ expected at ~3% selectivity, not a flag.
- **Wrote `docs/PIVOT_2026-07.md`** (operator grant): evidence → options → recommendation. Bottom line: (A) run the race to its pre-registered verdict (f45 bar ~56 trades, sunset 08-27) AND (C) pre-registered slow-market forecasting-skill pilot on fee-free/low-fee categories (paper only, Brier skill + simulated CI>0 over ≥30 resolutions before any funding); (B) 5m microstructure competition rejected — subsidized-MM war, wrong game for this operator/capital. **Filed #162** (pilot build).
- Standings unchanged in the hour (today red across the board; three of four models negative cumulative). Live OFF.
- Next scheduled iteration: assess (f45 first rows?) → dev item = #162 scaffold or #114.

## 2026-07-10 11:11 UTC (out-of-band — PROCESS RELAUNCHED under explicit operator grant "ok relaunch")

- **Old process (PID 65492, code of 07-06) terminated and relaunched as PID 37735 on current develop.** Sequence: operator had already stopped the loop at 10:50 (clean state, no live exposure — the 10 open live rows are June cruft #63-era); SIGTERM → uvicorn drained (held by a dashboard SSE stream) → SIGKILL after port release + loop-stop confirmed; relaunch via `nohup .venv/bin/python main.py > data/uvicorn_20260710.log` from repo root; flock picked up by the new PID; paper loop started via `POST /api/start` (requested_mode=paper governs).
- **Everything shipped this week is now ACTIVE and verified in the running process:**
  - **f45 in the live roster** (#155) — selector renders `cushion_fresh_v7_f45`; its accrual clock finally starts (day 0). Replay prior: OOS CI [+0.275, +0.887], bar ~56 trades.
  - **#151 feed label live** — detail now renders "Feed: spot Chainlink WS · ref Chainlink REST · vol Binance (vol shape) · quotes CLOB (settlement-aligned)".
  - **#122 regime columns** present on the shadow table — vol/basis logging begins with the first new row.
  - **#137 placement_status backfilled** (272 matched / 79 live) + captured forward.
  - **#138 silent-stop detector armed** in the new process.
- Guardrail note: charter §1 (never touch lifecycle) was overridden by a one-shot explicit operator grant, same precedent as #155. §1 remains binding for scheduled iterations.
- Engine active model untouched: `fair_value_fresh_v8` (operator's 03:29 selection persists).
- Next scheduled iteration: assess f45's first rows + regime-column population; dev item #114.

## 2026-07-10 ~10:45 UTC (out-of-band — v8 halt post-mortem + regime-switching verdict)

- **Operator switched the engine's active model to v8 at 03:29 — directly after v8's best day (+$20.56 on 07-09). It lost −$6.30/8 trades and tripped the paper trailing halt at 04:45.** Operator cleared the halt + auto-pause and Stop/Started at 10:38.
- **⚠️ Dashboard Stop/Start is a THREAD restart, not a code reload.** The process is still PID 65492 booted 07-06 01:17 — f45 (#155) and the #151/#138/#122/#137 merges are ALL still inactive. Activating them requires relaunching `main.py` itself (kill 65492 → `nohup .venv/bin/python main.py`). f45: still 0 rows, day 7 of dormancy.
- **Milestone: v7 crossed NEGATIVE** (−$1.36/n85 at 10:15 snapshot). Kill rule's sign condition met; n=85 < 150 floor → no formal §4 kill yet, but the race's namesake is underwater.
- **Regime-switching question tested on our own race data** (operator asked whether to switch strategies by regime). Simulated on all settled shadow rows since common start: hold-v8 +$16.60 · switch-to-cumulative-leader-daily **+$3.36** · switch-to-yesterday's-winner-daily **+$6.10**. Both switchers underperform holding; the trace shows they systematically buy the day AFTER the big day and eat the give-back (−15.99 after +19.44; −16.67 after +20.56). Matches the three live operator switches this month (v6 06-24, v7-live 07-06 −$4.07+halt, v8 07-10 −$6.30+halt) and the formal null (PR #121: 0/12 cells after FDR, perm p=0.21–0.65).
- **Verdict on the question**: regime AWARENESS stays (the gates ARE static regime conditioners; #122 now logs vol/basis for two new attribution axes post-relaunch; pipeline = axis clears two-sided FDR → pre-register variant in replay → OOS → shadow). Regime AUTO-SWITCHING remains a dead idea (charter §8) — switching selects among strategies but cannot create expectancy, and the control sits at ~zero over 400 trades.
- Standings at 10:15: v8 +$22.23/n216 (mean +$0.103, deflating; bar ~223d) · v0 +$4.78/n400 · v2 +$4.17/n237 · v7 −$1.36/n85. Live book unchanged −$19.35/351. Bot running, cadence ✓.
- Recommendation to operator (standing): (1) relaunch the PROCESS to activate f45 + fixes + regime logging; (2) park the engine's active model — the engine paper book generates halts, not evidence; the shadow race does the comparing.

## 2026-07-10 06:53 UTC (iteration 7)

- Race (settled, fee-true; Δ since it6 00:53): **v8 +$22.91/n203 (Δ −$14.02/+25 — REGRESSING)** · v2 +$5.27/n226 (Δ +$15.48/+28) · v7 +$0.14/n81 (Δ −$3.02/+10, ≈zero) · **v0 +$0.12/n380 — EXACTLY zero over 380 trades.** Today: v8 −$10.4/29, v0 −$11.1/54, v2 +$10.1/35.
- **The second leader is collapsing on schedule.** v8's mean fell $0.21→$0.11 in 6h; side attribution shows it was never two-sided — Up leg +$22.26/n100 carries ~everything, Down +$0.65/n103 ≈ 0 (directional tilt, fails the side test). Daily decomp: one day (07-09 +$20.56) is the whole total; ex-best-day ≈ +$2.35/174 ≈ 0. Same regression-to-null v7 showed at it4. Field = coin-flip-after-fees; v0's 380-trade zero is the cleanest statement of it yet.
- Deploy bar (nominal leader v8): mean $0.1128, z-CI [−0.227, +0.453]; needs ~1842 → **~182d** (40d at it6 — bar receding again as the estimate deflates).
- Live book: **−$19.35/351 (Δ$0)** — live OFF. Bot: mode=paper, state=running, accruing=YES, cadence 107/10min ✓. Overnight: operator cleared an auto-pause 03:29; **paper loss halt fired 04:45** (−6.30 ≤ trailing floor) — shadow accrual continued through it (#146 works as designed; last shadow row 06:50).
- Health flags: (1) **still no restart** — f45 0 rows; #151/#138/#122/#155/#161 all merged but inactive on the running pre-merge process. (2) v7 kill-watch: mean +$0.0017 at n=81 — flat, not negative; needs <0 at n≥150.
- **Advanced: #137 SHIPPED** (PR #161, `709983e`) — `placement_status` column + idempotent json_extract backfill + insert-time derivation; race_status LIVE BOOK now shows **272 crossed (taker) / 79 rested = 23% maker share** (validates the issue's ~21%). Calibration so-what: fee-true books overstate live fees by ~23% of entries; NOT a licence to chase maker fills (#130 dead idea stands). 8 tests, **808 total green**.
- Verdict check (§4): none met — but the pattern (two leaders regressed, control at exact zero) points toward the **08-27 sunset** as the likely outcome absent f45 evidence. Cron created 07-07, expires ~07-14 — ~4d out, >36h, no re-arm.
- Next: iteration 8 → assess → #114 (persist conditionId/token_id). Pending operator (5th consecutive iteration): **restart the paper loop** — f45 is the only untested hypothesis with replay CI>0, and every day unrestarted is a day of no f45 evidence.

## 2026-07-10 00:53 UTC (iteration 6)

- Race (settled, fee-true; Δ since it5 18:58): **v8 +$36.93/n178 (Δ +$16.00/+23)** pulling ahead · v0 +$11.20/n333 (Δ +$4.62/+36) · v7 +$3.16/n71 (Δ +$1.96/+8) · v2 −$10.21/n198 (Δ −$6.61/+18, sinking). ~85 new trades in 6h — healthy accrual resumed.
- **Feed flap self-resolved ~20:00 UTC WITHOUT a restart.** Tick cadence recovered 24→537→615→639→644→536/hour (19h→00h); feed_source last 2h = 1211 chainlink_ws / 44 rest_poll (~96% healthy WS). The #157 concern stands: it under-accrued ~6h invisibly.
- Deploy bar (leader v8): mean **$0.2075** (sd $2.46), z-CI [−0.153, +0.568], boot95 [−0.159, +0.560]; needs ~539 → **~40d** (down from ~125d — bar receding as v8 strengthens). Still straddles 0; NOT a verdict. v8 has led it4/5/6 with growing n — the model to watch, not yet crown.
- Live book: **−$19.35/351 (Δ$0)** — live OFF. Bot: mode=paper, state=running, **accruing=YES, cadence healthy (94/10min ✓ via new #157 signal)**.
- Health flags: (1) **bot NOT restarted** — f45 still 0 rows, and #151/#138/#122/#155 all still inactive on the running (pre-merge) process. No start/stop notifications since 07-08 22:04. (2) v7 on kill-watch (mean +$0.044, n=71 — needs <0 AND n≥150). (3) mission framing note: charter names v7 as the verdict target, but v7 collapsed and **v8 is the de-facto leader** — verdict framework (§4 deploy bar) applies to all models regardless.
- **Advanced: #157 SHIPPED** (PR #160, `8400354`) — race_status.py now reports `ticks_last_10min` + flags a running loop below ~30/10min as a JOURNALING STALL, visible even when accruing=YES (the 07-09 flap signature). Read-only. 4 tests, **800 total green**.
- Verdict check (§4): none met — v8 CI straddles 0; v7 +ve at n=71; sunset ~08-27. Cron created 07-07, expires ~07-14 — ~3.5d out, >36h, no re-arm.
- Next: iteration 7 → assess → #137 (maker/taker fill telemetry). Pending operator (unchanged): restart the paper loop to activate f45 + the 4 merged fixes.

## 2026-07-09 ~19:30 UTC (out-of-band — operator "do as you wish")

- **Applied #155: `cushion_fresh_v7_f45` added to the shadow roster** (PR #159, `5c3ac71`). Previously agent-gated; applied under an explicit operator grant. Additive/shadow-only — racing specs v0/v2/v7/v8 byte-identical, ablation intact, existing v7 clock untouched. Wired across _MODELS/SELECTABLE/LABELS/DESCRIPTIONS/CANDIDATE_SIGNALS; 3 tests (fires ≤45s, not at 50s, logs #122 state); **796 total green**.
- **⚠️ Does NOT accrue until the operator restarts the paper loop** — the running process predates this merge (as it does #151/#138/#122). One restart activates all four + gives Chainlink WS a fresh connection (helps the #157 feed-flapping throttle).
- If f45's replay edge is real, its deploy bar is ~56 trades (~6d of accrual) vs the current leader v8's ~125d — this is the single biggest lever on the verdict timeline, now unblocked pending that restart.
- No new race data assessed here (out-of-band action, not a scheduled iteration). Verdict framework unchanged; cron not re-armed (>36h to 07-14 expiry).

## 2026-07-09 18:58 UTC (iteration 5)

- Race (settled, fee-true; Δ since it4 13:07): **v8 +$20.93/n155 (Δ $0/0)** · v0 +$6.58/n297 (Δ +$4.33/+2) · v7 +$1.20/n63 (Δ $0/0) · v2 −$3.60/n180 (Δ $0/0). Only v0 moved (+2). Standings essentially frozen.
- **WHY frozen: Chainlink settlement feed is flapping and throttling accrual.** Only 2 shadow entries in ~6h. Tick cadence collapsed to 11–24/hour (vs ~247 earlier) with a clean **40-min tick gap (18:18→18:58)**; last-3h feed_source = 46 chainlink_ws / 11 chainlink_rest_poll / 2 `ref=unavailable`; resuming tick read "skip: settlement feed degraded". Models correctly skip on a degraded ref (#21 design) — this is throttled accrual, NOT bad trades. Loop is alive (tick 4s old), #147 watchdog quiet (heartbeat stays fresh through the gaps → it's blind to journaling stalls).
- Deploy bar (leader v8): mean $0.135, z-CI [−0.253, +0.523]; needs ~1283 → ~125d. Nothing clears; all CIs straddle 0. Field still converging on coin-flip-after-fees.
- Live book: **−$19.35/351 (Δ$0)** — live OFF. Bot: mode=paper, state=running, accruing=YES-but-throttled.
- Health flags: (1) **feed instability → filed #157** (watchdog blind to tick-cadence gaps; observability fix proposed). (2) **#155 still NOT approved** — f45 absent (0 rows). (3) running bot predates #151/#138/#122 merges — all take effect on next restart.
- **Advanced: #122 SHIPPED** (PR #158, `7d4f029`) — shadow rows now log spot/ref/sigma/drift at decision time; regime_attribution gains a-priori **vol** (3e-5/6e-5) + **basis** (5/15bps) axes, resilient to pre-migration DBs. Cutoffs frozen from observed scale (units-calibration, not fitted). 10 tests, **795 total green**. Also filed #157.
- Verdict check (§4): none met — v7 +$0.019 at n=63 (kill needs <0 AND n≥150); v8 unproven; sunset ~08-27. Cron created 07-07, expires ~07-14 — >36h out, no re-arm.
- Next: iteration 6 → assess → #157 (tick-cadence observability, mission-relevant given today's feed flapping) unless operator approves #155. Pending operator: approve f45 (#155); restart to activate #151/#138/#122 (+ hope feed stabilizes).

## 2026-07-09 13:07 UTC (iteration 4)

- Race (settled, fee-true; Δ since it3 00:53): **v8 +$20.93/n155 (Δ +$1.78/+21)** now leader · v7 +$1.20/n63 (Δ **−$7.79**/+8 — collapsed) · v0 +$2.25/n295 (Δ −$2.71/+37) · v2 −$3.60/n180 (Δ −$7.35/+22, now NEGATIVE).
- **The story: everything is regressing toward zero.** v7 fell from leader (+$0.164 it3) to +$0.019 as n grew — textbook regression of a lucky small sample; its **Up leg has gone negative (−$0.114/n35)**, so it's no longer two-sided +ve. v8 is now the only cleanly two-sided model (Down +$0.087/n79, Up +$0.185/n76) but at +$0.135 its CI [−0.253, +0.523] still straddles 0. Do NOT crown v8 — n=155, unproven.
- Deploy bar (leader v8): mean $0.135 (sd $2.47), z-CI [−0.253, +0.523], boot95 [−0.261, +0.518]; needs ~1283 trades → **~125 days**. Nothing clears; all four CIs straddle 0.
- Live book: **−$19.35/351 (Δ$0)** — live OFF, correct. Bot: mode=paper, state=running, accruing=YES; loop healthy (10 ticks/20min). Primary paper strat quiet since 05:20 (9 trades today, −$4.95) — NOT auto-paused (auto_paused=0), just correctly skipping edges outside its [0.045,0.07] band (current tick edge +0.142 → SKIP). Benign.
- Health flags: (1) running bot still shows old "Binance fallback" feed string — it predates the #151 merge; fix goes live on next restart (deployment lag, not a bug). (2) **#155 NOT approved** — f45 still absent from roster (0 rows). (3) stale open rows (v1/v1.1) persist — pre-existing cruft.
- **Advanced: #138 SHIPPED** (PR #156, `ac542a3`) — get_status() now emits one `btc_silent_stop` notification per silent death (with last-heartbeat time) instead of silently healing the stale 'running' row; complements #147. Pure `is_silent_stop()` helper; 9 tests, **785 total green**. Directly attacks the 40h-dark uptime risk.
- Verdict check (§4): none met — v7 +$0.019 at n=63 (not <0, not n≥150); v8 unproven; #149 OOS-confirmed; sunset ~08-27. **v7 KILL-WATCH: if it crosses negative AND reaches n≥150, §4 triggers a kill rec.** Cron created 07-07, expires ~07-14 — >36h out, no re-arm.
- Next: iteration 5 → assess → #122 (log spot/ref/sigma/drift on shadow rows → regime axes) unless operator approves #155. Two operator actions still pending: approve f45 (#155), restart to pick up #151/#138 fixes.

## 2026-07-09 00:53 UTC (iteration 3)

- **RACE ACCRUING AGAIN** — operator restarted paper bot 07-08 22:04:53 UTC; ~2.8h of fresh data. Bot healthy, ticking every 5s, last tick 00:53:29.
- Race (settled, fee-true): **v7 +$8.99/n55 (Δ −$0.72/+2)** · v8 +$19.15/n134 (Δ +$15.11/+9) · v2 +$3.75/n158 (Δ −$2.64/+8) · v0 +$4.96/n258 (Δ +$15.90/+16). v7 still two-sided +ve (Down +0.272/n24, Up +0.079/n31).
- New-data decomposition (charter §7 — small sample, treat as noise): 07-08 22:04→24:00 mixed (v8 +8.68/6 hot, v2 −6.4/4 cold); 07-09 00:00–00:53 everyone green but only 7 windows. **9 v8 trades and 16 v0 trades is noise — do NOT re-rank off a ~3h run.** v8's climb to #2-by-mean is worth watching, not concluding.
- Deploy bar (v7): mean $0.1635 (sd $2.47), z-CI [−0.488, +0.815], boot95 [−0.473, +0.795]; needs ~875 trades → **~91 days** at 9/day (receded slightly as mean dipped). Nothing clears — all four CIs straddle 0.
- Live book: **−$19.35/351 (Δ$0)** — live still OFF, correct. Bot: mode=paper, state=running, accruing=YES.
- Health flags: (1) stale OPEN rows for retired models `fair_value_v1` (n10), `fair_value_v1.1` (n2) + v0 (n11) predate the restart — excluded from settled standings, pre-existing cruft, candidate cleanup (not filed, low value). (2) Feed on new ticks: 1067 fully chainlink_ws, 479 chainlink_rest_poll (WS flaps to REST poll) + binance vol-shape — settlement-aligned throughout.
- **Advanced: #151 SHIPPED** (PR #154, `1b1fd1f`) — status panel now renders real per-component feed sources with a settlement qualifier instead of the false "Binance public fallback" string; 8 tests, 776 total green. **Filed #155** (add f45 to shadow roster — operator-gated, agent preps only). Charter §2B: all 3 original items done; next = #155/#138/#122.
- Verdict check (§4): none met — v7 +ve at n=55 (<150 kill floor); #149 OOS-confirmed; sunset ~08-27. Cron created 07-07, expires ~07-14 — >36h out, no re-arm.
- Next: iteration 4 → assess → #138 (notify on silent bot stop; addresses the 40h-dark uptime risk) unless operator approves #155 first.

## 2026-07-09 (iteration 2)

- Race: **v7 +$9.71/n53 (Δ+$0.00/Δ0 — STILL ZERO new data)** · v2 +$6.39/n150 · v8 +$4.04/n125 · v0 −$10.94/n242. Standings byte-identical to it1.
- Deploy bar (v7): mean $0.1833/trade (sd $2.47), z-CI [−0.480, +0.847], boot95 [−0.478, +0.839]; needs ~696 trades → **~71 days more** at 9/day. Unchanged.
- Live book: **−$19.35/351 (Δ$0)**. Bot: mode=PAPER, **state=STOPPED** since 07-08 16:51 UTC; last tick 07-07 06:40. **⚠️ RACE FROZEN ~2.6 DAYS — no notifications since it1, bot never restarted, f45 never added to roster. The loop is producing nothing. OPERATOR MUST PRESS START (paper) on the dashboard.**
- Health: no data movement to flag; ledger integrity OK; no operator activity in the feed.
- **Advanced: #150 SHIPPED — `tools/race_status.py`** (PR #153, `f0333f0`). One-shot read-only assessment CLI: immutable-snapshot → per-model standings (z-CI + bootstrap), live book, bot state/heartbeat, deploy-bar tracker; `--json` mode. Reproduces the hand-derived it1 standings exactly. 15 new tests, **768 total green** (DB-isolated). Charter §2A now runs this tool; §2B next = #151 (feed label).
- Verdict check (§4): none met — v7 positive at n=53 (<150 kill floor); #149 replay OOS-confirmed CI>0; 8-wk sunset ~08-27. Cron created 07-07, expires ~07-14 — not within 36h, no re-arm.
- Next: iteration 3 → assess via race_status.py → **#151 (fix stale "Binance public fallback" feed label)**. NB two operator actions still outstanding: (1) restart paper bot, (2) decide whether to add `cushion_fresh_v7_f45` to the shadow roster.

## 2026-07-08 22:52 UTC (iteration 1)

- Race: **v7 +$9.71/n53 (Δ+$0.00/Δ0 — ZERO new data since it0)** · v8 +$4.04/n125 · v2 +$6.39/n150 · v0 −$10.94/n242. v7 two-sided: Down +$4.46 n=23 WR=0.609, Up +$5.25 n=30 WR=0.600 ✔
- Deploy bar: v7 mean $0.183/trade (sd $2.47) → needs **~696 trades ≈ 78 days** at 9/day; already have 53 → still need ~643.
- Live book: **unchanged at −$19.35/351** (no new real-money trades since trailing halt 07-07 06:40). Bot state: mode=PAPER, state=**STOPPED** as of 07-08 16:51 UTC. Last tick: 07-07 06:40. **⚠️ RACE HAS NOT ACCRUED A SINGLE WINDOW IN 36h — OPERATOR MUST RESTART PAPER BOT FROM DASHBOARD.**
- Health: no new data, no new notifications, bot stopped gracefully; feed/quote sources unchanged.
- **Advanced: #149 (pre-registered v7 variant replay — H1 & H2 both confirmed)**
  - H1 `cushion_fresh_v7_f45` (≤45s): OOS CI **[+0.275, +0.887]**, mean +$0.58 vs current v7 +$0.35 → 66% lift; deploy bar collapses to ~56 trades ≈ 6 days. OOS CI lower bound 3.4× tighter.
  - H2 `cushion_fresh_v7_f45_spread` (f45 + spread≤1c): OOS CI [+0.233, +0.857]; marginal vs f45 alone; deferred until f45 has ≥50 race trades.
  - Shipped: `cushion_fresh_v7_f45` + `cushion_fresh_v7_f45_spread` in `signals.py`; `up_bid`/`down_bid` optional fields in `SnapshotView` (None in all production paths); replay grid extended; 12 new tests (753 total green). PR #152 merged to develop (`5c604c5`).
  - **Operator action needed**: add `cushion_fresh_v7_f45` to the shadow roster (edit `btc_bot/shadow/runner.py`) so it starts accumulating race-era data alongside the existing v7. This does NOT modify the racing v7 spec and does NOT reset the existing race clock. The deploy bar for f45 starts fresh from 0.
- Next: iteration 2 → assess (including f45 delta once bot restarts) → #150 (race_status.py CLI tool).

## 2026-07-07 06:55 UTC (iteration 0 — baseline, manual session)

- Race (common start 07-02 14:50, fee-true, settled): **v7 +$9.71/n53** (mean +$0.18, t≈0.54,
  CI straddles 0) · v8 −$4.6/n129 · v2 +$1.6/n150 · v0 −$10.9/n242 (v8/v2/v0 approx — refresh
  next iteration). Today (through 06:40): everyone red except v2 (v7 −$7.70/15).
- Deploy bar: at current mean $0.18 (sd≈2.45) CI>0 needs ~690 trades ≈ **2–3 months** at
  8–11/day; at replay's +$0.34 estimate ~200 trades ≈ 3.5 weeks total. Bar receding as of today.
- Live book: lifetime **−$19.35/351**. v7 live probe 07-06/07: 16 fills −$4.07; daily trailing
  halt fired 06:40:06 UTC (realized −8.10 ≤ floor −6.35 = peak +3.65 − 10.00) → live stopped.
  Watchdog correctly declined to auto-restart a LIVE loop.
- **Race accruing: NO** — bot fully stopped since 06:40 UTC (mode=live, state=stopped). Shadow
  data stops with the loop; operator must restart in PAPER from the dashboard for the race to
  continue.
- Health: feed on race entries = chainlink_ws 512/520 ✔; status label stale (#151); fee-true
  booking verified ✔; sizing uniform ✔.
- Filed: #149 (pre-registered v7 f45/spread-guard replay — next dev item), #150 (race_status
  tool), #151 (feed label). Cron `23 */6 * * *` (durable, expires ~07-14) created.
- Next: iteration 1 → assess deltas + start #149.
