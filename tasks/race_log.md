# Race Log — cushion_fresh_v7 kill-or-deploy

Charter: `tasks/race_loop.md`. Newest entries at top after iteration 0.

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
