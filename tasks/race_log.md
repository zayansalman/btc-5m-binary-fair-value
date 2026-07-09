# Race Log — cushion_fresh_v7 kill-or-deploy

Charter: `tasks/race_loop.md`. Newest entries at top after iteration 0.

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
