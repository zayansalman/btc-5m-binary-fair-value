# Changelog

## v0.4.8 — Set trade size in shares from the CONTROLS panel (2026-06-17)

Closes #89. The operator thinks in shares (contracts), not dollars — a binary's $ cost varies with price. The CONTROLS panel now takes a **share count** (≥5, the Polymarket minimum), shows the **$ value** of that many shares at the live price (plus the $2.50–$5-style range), and an **infographic** of the 5-share venue minimum. The share setting drives sizing everywhere — paper + live, next tick, no restart.

### What
- **`btc_5m_fv/execution/gate.py`**: new runtime knob `btc_runtime.trade_shares` (`set/get_runtime_trade_shares`, `runtime_trade_shares`, refreshed each tick). When set it takes precedence over the dollar `max_trade_usd` override: `effective_max_trade_usd` returns `trade_shares` (N shares cost ≤ ~$N since binary prices < 1, so the per-trade cap never blocks the bot's own N-share clip). DRYed the config reads behind `_read_positive`.
- **`btc_bot/paper.py`**: new pure `_share_sized_notional(side, notional, up_ask, down_ask, trade_shares)` — when a share target is set, `notional = trade_shares × the chosen side's ask`, so the loop sizes to ≈N shares (exact in paper; ≈N in live within rounding). The executor still auto-bumps to the venue minimum (#87). Unset → the dollar path is untouched (backward compatible).
- **`POST /api/runtime-config`**: new key `trade_shares` (validated 5 ≤ v ≤ 1000, audited).
- **CONTROLS panel** (`panels/controls.py`, wired in `ems.py`): shares input (`min=5`, live `≈ $` value computed in `dashboard.js:updateShareValue()` from the favoured side's ask), `$` range, share-minimum infographic (pips + label), updated hint. `setTradeShares()` POSTs the new key. STRATEGY sizing line shows `N shares (~$X)`.
- **Tests**: `test_share_sizing.py` (the resize helper), `test_risk_gate.py` (shares precedence + cap derivation), `test_runtime_config.py` (endpoint accept ≥5 / reject <5), `test_dashboard.py` (panel + handler). Full suite green (555); ruff clean; no new mypy errors.

### Why this shape
- The cap becomes a **target the venue minimum may exceed**, bounded by the derived dollar cap; the bankroll cap (when enabled) remains the dollar guard. The share count is the stable quantity the operator controls; the $ cost is shown as a derived, live estimate. The dollar `max_trade_usd` knob remains as a fallback when no share target is set (fully backward compatible).
- **Out of scope:** #83 (backtest leak) / #84 (signal overfit) — live *edge*, not sizing.

## v0.4.7 — Auto-bump sub-minimum orders to the venue share minimum (2026-06-17)

Closes #87; **supersedes the v0.4.6 floor**. v0.4.6 stopped the operator from setting a clip below ~$5, which removed legitimate control — Polymarket's real constraint is **5 shares/order**, which at the ≥0.50 favourites floor costs only **$2.50–$5** depending on price, not a flat $5. Operator wants to set any clip and still have small orders place. So instead of forbidding small caps, the bot now **rounds any sub-minimum order up to exactly the venue minimum** so it always places.

The "max trade size" cap becomes a **target, not a hard ceiling**: it may be exceeded only by what the venue minimum requires (e.g. a $3 clip at price 0.70 places 5 shares = ~$3.50), bounded so an abnormally large minimum can't overspend the bankroll.

### What
- **Reverted v0.4.6's floor** — `POST /api/runtime-config` accepts any `0 < v ≤ 1000` again; the gate no longer drops sub-floor overrides (`gate.py` back to `value if value > 0 else None`); the CONTROLS input `min` is `0.5` again and the hint explains auto-bump. Any positive clip is valid.
- **`btc_5m_fv/execution/live.py`**: in `submit_entry`, when `size < min_size` the order size is bumped UP to `min_size` (the book's `min_order_size`, default 5) and placed, instead of blocked. `record_buy_notional` uses the bumped size; the bump is logged (`live_executor.entry_bumped_to_min`). Guard: if `min_size > MAX_AUTO_BUMP_SHARES` (= 2 × `DEFAULT_MIN_ORDER_SIZE` = 10) the order is BLOCKED rather than overspend — a venue minimum that large is too expensive for this bankroll.
- **`btc_bot/paper.py`**: mirrors the bump (`shares = max(shares, DEFAULT_MIN_ORDER_SIZE)` before the top-of-book cap) so paper stays a faithful preview of live (#64).
- **Tests**: `test_live_executor.py` — the old `test_entry_blocked_below_min_order_size` becomes `..._bumps_to_minimum` (places 5 shares), plus a new too-large-to-bump guard test. Dropped the v0.4.6 floor tests. Full suite green (542); ruff clean; no new mypy errors.

### Why this shape
- The 5-share minimum is the venue's, and the smallest *placeable* favourite order ($2.50) is well below $5 — a flat floor over-restricted the operator. Bumping to the exact minimum gives full size control while never blocking on "too small". The cap-as-target trade-off (slight overspend only when the venue forces it) is bounded to ≤ `MAX_AUTO_BUMP_SHARES × price` (~$10 worst case); the bankroll cap (when enabled) remains the dollar-level guard.
- **Out of scope:** #83 (backtest leak) / #84 (signal overfit) — live *edge*, not placement.

## v0.4.6 — Enforce a min-trade floor on the runtime max-trade cap (2026-06-17)

Closes #85. Live trading was **100% blocked**: the dashboard BLOCKED panel showed every entry rejected — `size 1.78 shares below Polymarket minimum 5.00 at price 0.5600`. The operator had set the runtime **Max trade size to $1.00** from the dashboard (#50). At the favourites-only entry floor (price ≥ 0.50), $1.00 buys < 2 shares — below Polymarket's **5-share venue minimum** — so `LiveExecutor.submit_entry` (`execution/live.py`) correctly refused every order, every window. Funds were untouched; the bot was stopped.

Root cause: the #50 slice shipped **no floor** on the per-trade cap. Its CHANGELOG states the flawed assumption directly — *"a value below min gives a smaller fixed clip … no `min` changes needed."* But the cap is the **ceiling** of the confidence-sizing range, and `notional_from_confidence` clamps every order to `[min_trade, max_trade]`; a ceiling below `BTC_PAPER_MIN_TRADE_USD` ($5 in the operator's env) pins every order's notional to $1 → sub-minimum. Nothing enforced the floor: `POST /api/runtime-config` validated only `0 < v ≤ 1000`, the HTML input hardcoded `min='0.5'`, and the gate read accepted any `value > 0`.

### What
- **`btc_5m_fv/execution/gate.py`**: new `_runtime_override_or_none(value)` — a stored override that is non-positive **or below `BTC_PAPER_MIN_TRADE_USD`** is invalid and dropped, so the gate falls back to the (placeable) env default. Wired into both `RiskGate.refresh_runtime_limits()` (per-tick) and the module reader `get_runtime_max_trade_usd()` (dashboard display), so the live bot **auto-heals the stale $1.00 on the next tick** — no manual DB surgery, no migration. The floor is read from `config` at call time (no restart to change it).
- **`btc_5m_fv/ops/dashboard/app.py`**: `POST /api/runtime-config` now rejects `value < BTC_PAPER_MIN_TRADE_USD` with an actionable error ("must be at least the $5.00 min trade size — a smaller cap … blocks all entries") instead of silently accepting an unplaceable clip.
- **`btc_5m_fv/ops/dashboard/panels/controls.py`**: the number input's `min` attribute now tracks the displayed `min_trade` floor (was a hardcoded `0.5`), so the browser widget and the "min $X" hint agree.
- **Tests**: `test_runtime_config.py` (endpoint rejects sub-floor / accepts at floor); `test_risk_gate.py` (a stored $1 override is dropped → effective cap falls back to env default — the exact incident). Existing cap tests now pin `BTC_PAPER_MIN_TRADE_USD` explicitly, removing a latent dependency on the local `.env` (floor of 5 vs the CI default of 1). Full suite green (544, +3 new).

### Why this shape
- The invariant is **effective per-trade cap ≥ min-trade size** — the ceiling can't sit below the floor without inverting the sizing range. Enforced at every boundary (endpoint reject → operator feedback; gate read drop → heals legacy state; HTML `min` → honest widget), mirroring the existing "invalid override → None → env default" handling rather than adding new machinery.
- The gate drop is silent (no per-tick log spam) and the reader masks the stale value, so the dashboard already shows the corrected env-default cap. The stored $1 is inert until overwritten.
- **Out of scope:** #83 (backtest look-ahead leak) and #84 (entry-signal overfit) — those question whether the signal has live *edge*; this change only unblocks order *placement*.

## v0.4.5 — UI-settable max trade size (2026-06-16)

Part of #50 (the max-trade-size slice). Resizing the clip required editing `.env` and restarting uvicorn; with `BTC_PAPER_MIN_TRADE_USD=BTC_PAPER_MAX_TRADE_USD=5` every trade went in at a fixed $5 with no way to tune it mid-session. Now the operator sets it from the dashboard and it takes effect on the next tick — paper AND live, no restart.

### What
- **`btc_5m_fv/execution/gate.py`**: new runtime per-trade cap override. `RiskGate.refresh_runtime_limits()` re-reads `btc_runtime.max_trade_usd` from the `config` table every tick (runs in BOTH modes — it's a tuning knob, not the paper-only loss-halt bypass). New `runtime_max_trade_usd` (raw override) and `effective_max_trade_usd` (override else env default) properties; `block_reason` enforces the **effective** cap. Module helpers `set_runtime_max_trade_usd` / `get_runtime_max_trade_usd` with validation.
- **`btc_bot/paper.py`**: `paper_tick_once` calls `refresh_runtime_limits()` each tick; `_strategy_params()` uses the runtime override for the sizing ceiling when set (else env default — fully backward-compatible). So one knob governs both the sizing ceiling and the gate cap (unified). `notional_from_confidence` already clamps to `[min, max]`, so a value below min gives a smaller fixed clip and above min re-enables confidence-scaled sizing — no `min` changes needed.
- **Dashboard CONTROLS card** (`btc_5m_fv/ops/dashboard/panels/controls.py`, new; wired in `ems.py`, first grid row after RISK GUARDRAILS): shows current max (operator vs env default), a number input + Apply. `POST /api/runtime-config` (`app.py`) validates (0 < v ≤ $1000), persists via the gate setter, audits to `notification_feed`. `setMaxTradeSize()` in `dashboard.js`; `.ctl-input`/`.ctl-row` theme CSS. STRATEGY card's sizing line now reflects the effective cap.
- **Tests**: `test_risk_gate.py` (override applies in both modes, refresh fallback, clear, set/get round-trip, invalid-value handling); `test_runtime_config.py` (endpoint persists + validates against an isolated DB); `test_dashboard.py` (CONTROLS card + handler render). Full suite green (503 tests, +15 new).

### Why this shape
- Mirrors the existing per-tick `refresh_overrides` + `config`-table pattern (#65), so no new machinery and no restart. Unset key = exact prior behaviour (backward-compatible). The UI follows the existing panel architecture (pure `render()` panel, data in `ems.py`, POST + `refreshAll`, theme CSS) — no bespoke surface.
- Singleton position mode and multi-position were deliberately left untouched (deferred at operator request); `EntryRequest` / `GateConfig` / `LiveExecutor` single-position state are unchanged.

## v0.4.4 — Bankroll Cap Opt-In + RISK GUARDRAILS Panel (2026-06-15)

Closes #61. Investigation of "why did the bot stop trading after lunch?" surfaced a UX gap: the daily $30 bankroll cap had been silently rejecting every entry from 10:46 UTC onward (43 BLOCKED entries journaled in `btc_live_orders`), but nothing on the dashboard showed it. Operator had to grep logs and query SQLite to figure out the cap was hit.

### What
- **`config.py`**: new `_env_optional_float` helper; `BTC_LIVE_BANKROLL_CAP_USD` is now `Optional[float]` — blank / unset / ≤0 → `None` (gate disabled). Default behavior is now **no cap**. The per-trade cap and the daily loss halt are unchanged — both still mandatory.
- **`btc_5m_fv/execution/live.py`**: the bankroll-cap gate in `entry_block_reason` is skipped when `bankroll_cap_usd is None`. The persisted `daily_buy_notional` counter keeps incrementing on every matched fill regardless, so the dashboard can still show throughput when the cap is off.
- **RISK GUARDRAILS panel** (new wide card in `btc_5m_fv/ops/dashboard/ems.py`, first row of the EMS grid). Four columns:
  - **DAILY SPEND** — filled notional today, cap status (disabled or $X with headroom), submitted-entry count + total notional.
  - **LOSS HALT** — realized day P&L, halt threshold (–$10), headroom, OK/HALTED pill.
  - **BOT STATE** — state pill (RUNNING/STOPPED/OFF), uptime, last loop detail line (red when it contains error/fail/crash keywords — the NoneType crash that triggered today's investigation would have surfaced here), auto-pause status.
  - **BLOCKED (LAST 5 TODAY)** — newest-first tail of risk-gate rejections from `btc_live_orders` WHERE `status='BLOCKED'`, full reason on hover.
- Knock-on callsites (`btc_bot/controller.py:_default_detail`, `btc_5m_fv/ops/dashboard/app.py` `_brief_html`/`_settings_html`) render the cap as "disabled" when `None`.
- New unit test `test_bankroll_cap_none_does_not_block` confirms the gate bypasses arbitrarily large cumulative spend when the cap is unset.

### Why this, not a config flip
- The cap is "opt-in default off" rather than ripped out — same code path, just a new sentinel. If a future operator wants a budget guard back, they set the env var; no code change needed. Reversible.
- The guardrails panel makes the cap's status (and the loss halt's, the loop's last error, and any silent BLOCKED queue) **visible by default** — the original failure mode was lack of observability, not the cap itself.

### Out of scope (filed separately)
- The `TypeError: unsupported format string passed to NoneType.__format__` crash in the live loop tick at 11:24 UTC (surfaced today's investigation). Needs its own fix.
- Orphan live position 1222 from the morning's failed `exit_untracked` event — manual ledger reconcile required.

## v0.4.3 — Layer 1 Self-Improvement: Isotonic Calibration (2026-06-15)

Fixes #37 (new). Closes the prediction → outcome loop on the strategy's own raw probability without touching strategy logic.

### What
- **`btc_bot/calibration.py`**: pure-Python pool-adjacent-violators (PAV) isotonic regression, no sklearn/numpy dependency. `IsotonicCalibrator` (monotonic piecewise-constant map, JSON round-trip), `IdentityCalibrator` (no-op fallback so the bot is a no-op until a fit exists), `apply_to_pair` (calibrates fair_up and 1-fair_up, renormalises to sum=1 since exactly one outcome resolves).
- **`btc_bot/calibration_fit.py`**: CLI that pulls closed clob trades from the journal, derives `(model_p_side, side_won)` pairs from existing `edge`, `entry_price`, `realized_pnl_usd` columns (no schema change), fits, persists to `$DATA_DIR/calibration.json` atomically, prints Brier-before vs Brier-after and the fitted blocks.
- **`btc_bot/paper.py`**: applies the calibrator to `fair_up_raw` before computing side edges; preserves raw value on the snapshot as `fair_up_prob_raw` for diagnostics. Cached at module level with a `reload_calibrator()` helper for next-tick refresh after a refit.
- **Dashboard STRATEGY card**: new "Calibration" row showing kind / n_samples / Brier raw → calibrated (delta). Renders `identity (no fit yet)` until the first fit.
- **Tests**: 14 new unit tests (PAV correctness, monotonicity, identity, JSON round-trip, missing/corrupt/unknown-kind fallback). 442 green.

### Fit on live DB (n=844 closed clob trades)
- Brier **0.275 → 0.242 (+0.033 absolute, ~12% relative)**.
- Calibration curve makes the bias plain: model's claimed 95% confidence trades win ~59%, claimed ~68% win ~54%. The bot has been paying that overconfidence at every entry. Renormalised pairs compress: raw fair_up=0.93 maps to calibrated (0.62, 0.38).

### Why this, not Hugging Face (yet)
- HF time-series foundation models (Chronos, TimesFM, Lag-Llama) and FinBERT are interesting but speculative for a 5-minute BTC binary; the calibration layer is the no-regret first step that the existing journal already supports. Layer 2 (param auto-tune) and Layer 3 (model ensembles) are deferred to later issues.

### Out of scope (next)
- Daily/scheduled refit (run `python -m btc_bot.calibration_fit` manually for now).
- Layer 2: rolling-window auto-tune of `entry_edge_min`, sigma floor, sizing curve via the existing backtest harness.
- Layer 3: optional HF time-series model as a second probability source, A/B'd via the replay harness.

## v0.4.2 — Bloomberg-EMS Theme + Mode Selector + Single-Process Lock (2026-06-15)

Refs #37, #36.

- **Bloomberg-EMS retheme**: amber accent (the trading-terminal signature) on dark slate, amber category-header bars, dense monospace grid; convention colors (green/red/amber/blue). Replaces the teal "modern fintech" palette per EMS design references.
- **Paper/Live mode selector** in the topbar: a runtime toggle (`/api/mode`, `controller.set_mode`) that switches mode via the config table and restarts the loop cleanly (stop-before-start = single loop). Live is gated exactly like boot (`assert_live_boot_allowed`); the LIVE option is disabled with the reason when the gate isn't met, and a confirm dialog precedes any switch to real.
- **Single-process lock** (`main.py`, advisory flock on `data/bot.lock`): a second instance fails fast. This is the root-cause fix for #36 — multiple overlapping processes each ran a loop with independent live-executor state, which is why "live" entries silently took the paper path. `run_paper_loop` now reads the runtime-selected mode.
- 428 tests green; retheme + selector verified in-browser.

## v0.4.1 — EMS-Style Dashboard (2026-06-15)

Fixes #37. Rebuilt the dashboard as an execution-management terminal.

- New `btc_5m_fv/ops/dashboard/ems.py`: status ribbon (mode/run pills, session equity, day P&L, open risk, daily-halt headroom, feed chips, uptime, auto-pause/kill state); STRATEGY panel (model, edge band, entry floor, sizing, settlement rule, auto-pause); LIVE MARKET (fair-Up gauge, Up/Down book, spot/ref/basis/edge, decision); PERFORMANCE/ALPHA (inline SVG equity curve, net P&L, ROI, win rate, expectancy, profit factor, max DD); TCA (quoted spread, taker half-spread, signaled-vs-realized edge capture, Brier, SVG calibration); TRADE BLOTTER. Inline SVG charts — no JS charting dependency.
- Performance/TCA/blotter use a **recent rolling window** (current regime), not the lifetime blend that mixed in older experimental configs; the ribbon stays session/day-scoped. Honest framing, labeled "recent N".
- Dark trading-terminal CSS (tabular monospace numbers, P&L color coding, dense panels); reuses the SSE pipeline (`ems` added to `/api/data` + `/api/stream`; JS swaps `#ems-content`).
- Fixed a latent `float(None)` crash in `load_paper_summary` when a tick has no up-ask (now common in live).
- Dashboard tests rewritten to the EMS contract. 428 green; verified visually in-browser.

## v0.4.0 — Adaptive Risk Controller + AI Research-Loop Design (2026-06-14)

Fixes #36. The "self-improving" layer, done rigorously — adaptive risk control over our own journal, not price prediction.

### Adaptive risk controller (`btc_bot/adaptive.py`)
- Rolling expectancy / win-rate / **Brier calibration** over the last N closed clob trades of the active style. Model probability reconstructed as `edge + entry_price`; outcome = `realized_pnl > 0`. No schema change.
- **Auto-pause**: blocks NEW entries when rolling ROI drops below a floor after a minimum sample — STICKY until an operator clears it (`tools/clear_auto_pause.py`). Catches EDGE DECAY before losses pile up; complements (does not replace) the hard −$10/day halt. Notifies on trip; existing positions still settle.
- Config: `BTC_AUTO_PAUSE_ENABLED/WINDOW/MIN_TRADES/MIN_ROI`. 8 tests (metric math, calibration, style/quote/state filtering, sticky no-auto-resume, warm-up, disabled).

### AI research loop (designed, `docs/RESEARCH_LOOP.md`)
- Nightly agent mines the journal for loss clusters → proposes filters → backtests walk-forward OOS on the recorded archive → surfaces only survivors with numbers for operator approval. AI proposes, human disposes; never auto-applies to live. Built once ≥~50 live fills accumulate.

### Won't do
- RL auto-tuning on a live $30–40 bankroll (overfits to noise). No live-param change without OOS validation + operator sign-off.

443 tests green.

## v0.3.7 — Existing-Wallet (MetaMask) Onboarding + Auto-Detect (2026-06-12)

Fixes #34. For users who already hold funds in a connected-wallet Polymarket account, added a no-fund-movement path: trade the existing balance in place using the wallet's signer key.

- `tools/live_detect_wallet.py`: given the signer key in `.env`, derives every wallet it could control (EOA / POLY_PROXY / Gnosis Safe via the SDK's `derive_proxy_wallet_address` / `derive_safe_wallet_address`), reads each candidate's on-chain pUSD balance on Polygon (multi-RPC fallback: publicnode / 1rpc / drpc), and writes the funded one's address + matching signature type into `.env`. Deterministic — the funded address's derivation *is* its signature type (EOA=0, POLY_PROXY=1, GNOSIS_SAFE=2); no guessing.
- Runbook documents Path A (existing connected wallet, auto-detected) vs Path B (fresh isolated deposit wallet), with the key-blast-radius tradeoff stated plainly.
- pUSD collateral token address (`0xC011a7E1…`, Polygon) sourced from the SDK's PRODUCTION environment config.

## v0.3.6 — Verified Self-Serve Deposit-Wallet Onboarding (2026-06-12)

Fixes #33. Hardened and verified `tools/live_setup.py` end-to-end against the live Polymarket API:

- **Key never leaks**: the generated private key is written straight into `.env` (perms `0600`, `.env.bak` saved) and is never printed to stdout — so it cannot land in terminal scrollback or an assistant transcript. Only the public signer/deposit addresses are shown. `_merge_env` updates keys in place, preserves comments/other keys, and is unit-tested to NEVER auto-write `BTC_LIVE_CONFIRM` (the operator's conscious go-live step).
- **Correct deposit-wallet flow** (verified live): mint a Builder API Key from the signer key (L1→L2→builder, self-serve, used only for the gasless deploy then discarded) → `SecureClient.create(api_key=...)` deploys the deterministic type-3 deposit wallet gaslessly. The earlier `setup_trading_approvals()` call was the EOA method and hit a relayer allowlist rejection; removed. Collateral allowance is set by `update_balance_allowance` on first funded connect (executor + preflight already call it) — no separate approval step.
- `polymarket-client` declared as the optional `setup` extra (one-time onboarding only; runtime trading uses `py-clob-client-v2`).

## v0.3.5 — Real Live-Setup Flow (2026-06-12)

Fixes #32. The runbook's "export private key from Polymarket settings" step does not exist in the product (operator-verified; absent from current docs). Setup rewritten to the documented reality — you bring a wallet you control:

- `tools/live_setup.py` — one-time onboarding via official py-sdk (optional install): generates a fresh key if needed, deploys the deterministic deposit wallet (signature type 3), runs idempotent gasless trading approvals, prints the `.env` block and funding instructions. Existing UI funds move by **withdrawing to the funder address** — no export anywhere.
- `tools/live_preflight.py` — read-only GO/NO-GO: boot gate, credential derivation, CLOB reachability, funder balance/allowance as the CLOB sees it.
- `LiveExecutor.start()` now refreshes the CLOB balance/allowance cache (`update_balance_allowance`, best-effort) — the documented pre-first-order step.
- Runbook "Going Live" rewritten (wallet decision table, scripted path, preflight gate); `.env.example` signature-type guidance corrected (type 3 recommended).

## v0.3.4 — Migrate to py-clob-client-v2 (2026-06-12)

Fixes #31 (launch blocker). Upstream archived `py-clob-client` (v1) with "no longer functional — should not be used"; live mode would have failed at first auth. Migrated `LiveExecutor` to official `py-clob-client-v2` (1.0.1):

- `create_or_derive_api_creds()` → `create_or_derive_api_key()`; `cancel(order_id)` → `cancel_order(OrderPayload(orderID=...))`; import paths updated. Constructor surface (incl. `signature_type`/`funder` for proxy wallets), `set_api_creds`, `get_order`, `cancel_all`, order/cancel response bodies, and `OrderBookSummary` fields (`min_order_size`, `tick_size`, worst→best level ordering) are unchanged — verified against installed v2 source.
- v1 removed from the venv and dependency declarations; suite green with zero v1 references; v2 smoke-tested against the live CLOB API (`get_ok`, `get_server_time`).

## v0.3.3 — Anti-Adverse-Selection Entry Filters (2026-06-12)

Fixes #29. The 26h settle soak (n=225, -14.4% ROI overall) revealed structured losses: PnL by claimed edge decreases monotonically (4.5–7%: +7.0% ROI; >15%: -36% to -57%), and entries below 50¢ lose badly while favorites win 63–72%. Large apparent edge = the model lagging a fast market (adverse selection), not opportunity.

- `BTC_PAPER_ENTRY_EDGE_MAX` (default **0.07**): entries whose claimed edge exceeds the cap are rejected — "skip: edge above cap (stale-model guard)"
- `BTC_PAPER_MIN_ENTRY_PRICE` default raised 0.05 → **0.50**: favorites only
- In-sample, the joint surviving slice (edge 4.5–7%, entry ≥ 50¢) ran **+22.8% ROI, 73% win, n=48**, positive in both sample halves. This is post-hoc structure: the filtered strategy must hold in a fresh out-of-sample soak before the live gate opens.

## v0.3.2 — Settle-Style Strategy Profile (2026-06-11)

Fixes #28, closes #27. First honest-baseline soak (135 trades / 70 min) showed the legacy scalp shape is structurally negative under real fills: -$7.87, median hold 8s, up to 17 entries per window, 65 STOPs (-$55) vs 48 TARGETs (+$44) — it pays the spread every few ticks and stops out on noise. The +31% April backtest used the opposite shape.

- New `BTC_EXIT_STYLE`: **`settle` (default)** — max one entry per window (kills churn by construction), no TARGET/STOP/BAND/TIME exits; positions ride to resolution and close at the Chainlink-settled 1.00/0.00. `scalp` keeps the legacy behavior for experiments.
- Live mode: `LiveExecutor.record_settlement(won, window_slug)` registers the resolution outcome (PnL into the persisted daily-loss halt, journal `SETTLEMENT` row, slot freed) without placing an exit order; any resting entry remainder is cancelled. Winning tokens await operator redemption — runbook section added.
- Positions journal `strategy_style`; KPIs aggregate only the active style (third baseline reset; prior rows remain as audit trail).
- 7 new tests (style gating, one-entry-per-window, settlement win/loss accounting, no-exit-order bypass).

## v0.3.1 — Data Integrity: CLOB Quotes + Settlement Feed (2026-06-11)

Fixes #21, #22, #23. The signal path now prices, fills, and settles against the same data the market actually uses.

### Executable quotes (#22)
- Signal edge is computed against the CLOB best ask per outcome token (`signal_from_executable_edges`); Gamma `outcomePrices` are journaled (`gamma_up_price`) only to quantify their staleness, never used for pricing
- Honest paper fills: BUY at best ask, SELL at best bid, capped by top-of-book size; empty/crossed books skip with a journaled reason
- Window-rolled paper positions settle at the actual Chainlink resolution (1.0/0.0 via the settlement endpoint) instead of pricing the old position off the new window's book
- Tick journal gains top-of-book columns (both sides) + `quote_source`; performance KPIs exclude pre-fix rows (re-baseline — old rows stay as audit trail)

### Settlement-aligned Chainlink feed (#21)
- New `ChainlinkSettlementConnector` (REST): window reference open with provisional-revision stabilization, fast settlement via `open(N+1) == close(N)`, cache-busting, full Chrome-fingerprint WAF headers
- New `ChainlinkWsFeed` (WS): live 1s prints from `ws-live-data.polymarket.com` (byte-exact compact subscribe filters, literal-PING keepalive, reconnect with backoff, 429-aware), feeding spot + sigma
- REST spot poll fallback: `openPrice` of a window starting seconds ago IS the near-live settlement print — the engine survives WS outages/rate limits without mixing sources
- Binance demoted to volatility-shape fallback and backtest tooling — its LEVELS never touch the model (measured Chainlink−Binance basis ≈ −$50.7, std $3.8)
- Tie rule: fair value now includes the discrete tie mass P(close == open), which resolves Up — `fair_up > 0.5` when price pins the reference
- A degraded settlement feed blocks new entries and suppresses fair-value-based exits (BAND_REENTRY); time/target/stop exits still run

### Dashboard state (#23)
- Controller state derives from the actual runner thread in both directions: never STOPPED while ticking, never RUNNING while dead

## v0.3.0 — Live Execution Mode (2026-06-10)

Closes #20. Adds an opt-in live trading mode that routes the existing signal path through Polymarket's CLOB via `py-clob-client`. **Paper remains the default**; nothing changes unless the operator explicitly flips every gate.

### Live executor (`btc_5m_fv/execution/live.py`)
- `LiveExecutor` wraps the synchronous `ClobClient` behind an async API (all network calls via `asyncio.to_thread`)
- `start()` derives + sets CLOB API creds (`create_or_derive_api_creds` / `set_api_creds`) and verifies reachability before any trading
- Entries: GTC limit BUY at best ask (book best ask, gamma price fallback); price rounded to the market tick size, size rounded **down** to the CLOB 2-decimal share granularity, Polymarket minimum order size enforced from the order book (default 5 shares)
- Exits: GTC limit SELL at best bid for the **matched** entry size (`get_order` → `size_matched`), so the bot never sells shares that never filled; any resting entry remainder is cancelled before the sell
- Cancel-on-roll: unfilled entry orders are cancelled on `WINDOW_ROLL` and `BAND_REENTRY` exits (matched size is captured post-cancel so fills landing mid-cancel still get flattened)

### Hard risk limits (enforced in code BEFORE every order)
- Per-trade cap: `BTC_LIVE_MAX_TRADE_USD` (default $3)
- Max 1 open live position/order
- Daily realized-loss halt: `BTC_LIVE_DAILY_LOSS_HALT_USD` (default $10, UTC day) — **persisted in SQLite and reloaded at boot**, so Stop/Start or a restart cannot reset it within the day
- Daily bankroll cap on summed buy notionals: `BTC_LIVE_BANKROLL_CAP_USD` (default $30/UTC day, persisted); the unfilled remainder of a cancelled entry is credited back
- Entry slippage guard: `BTC_LIVE_MAX_ENTRY_SLIPPAGE` (default 0.02) blocks buys when the live ask has gapped above the signal price that produced the edge
- Kill switch: the file `data/KILL` blocks all NEW entries and cancels resting orders, checked every tick plus immediately before each entry POST (TOCTOU guard); it re-arms when the file is deleted. Exits stay allowed under kill — flattening only reduces exposure
- Realized PnL feeds the loss halt from CONFIRMED exit fills (actual matched size at the executed order's limit price), never from paper-price estimates at submission time

### Exit lifecycle & stop safety
- Exit SELLs never rest: the order is awaited up to `BTC_LIVE_EXIT_FILL_TIMEOUT_SECONDS` (default 10s) and cancelled if unfilled, so no stale GTC exit can sit in a 5-minute book into resolution; partial fills are accounted per tranche and only the remainder is retried
- A failed/blocked/unfilled live exit **keeps the ledger row OPEN** and is retried on the next tick — the ledger can never claim flat while real tokens remain on the exchange
- Cancels are verified against the DELETE response body (`canceled` list) with a terminal-status re-check; on cancel failure the order id stays tracked for retry instead of being forgotten
- Live entries write the ledger row BEFORE the order is submitted (failed submits delete it), so a DB failure after submit can never leave a real position unmanaged
- Stop: the controller sets the stop flag and **waits for the runner thread** to cancel resting orders and flatten through the executor on its own event loop — single-threaded executor ownership, no stop race, no paper-closing of live positions; unflattenable rows are reported for manual action

### Boot gating & reconciliation
- Live mode REFUSES to start unless `POLYMARKET_PRIVATE_KEY` is set AND `BTC_LIVE_CONFIRM=YES_I_UNDERSTAND` — checked on the dashboard `/api/start` path and again at loop start; it never silently falls back to paper
- Boot also REFUSES when: `POLYMARKET_FUNDER` is empty with signature type 1/2 (such orders are signed with the EOA as maker and rejected by the CLOB), the signature type is unknown, or any risk-limit env var failed to parse (no silent fallback to looser defaults)
- Boot reconciliation: `start()` cancels ALL resting CLOB orders on the account and re-adopts any open ledger position from the order journal (exchange-confirmed fill size); paper artifacts / never-filled rows are closed as `RECONCILED_*`; unreconcilable state refuses boot instead of trading on top of unknown exposure
- Wallet config: `POLYMARKET_FUNDER` (proxy wallet), `POLYMARKET_SIGNATURE_TYPE` (0 EOA / 1 email / 2 browser, default 1)
- The private key is never logged and never journaled

### Audit trail
- New SQLite table `btc_live_orders` journals every order/cancel attempt — including risk-gate BLOCKED attempts that never reach the network
- Engine ledger (`btc_paper_positions`) mirrors live fills (executor price/size), notifications use `btc_live_entry` / `btc_live_exit` events

### Dashboard & docs
- Status/brief/settings copy is mode-aware: "LIVE — orders are real" vs paper; stale "no live orders are placed by this build" claims removed
- `.env.example` documents all new vars (key/funder ship empty); `docs/OPERATIONS_RUNBOOK.md` gains a "Going live" section with launch steps, risk limits, and kill-switch drill

### Tests
- 70 new unit tests with a fully mocked `ClobClient` (boot refusal incl. funder/signature/parse-error gates, order construction/rounding/min-size, slippage guard, all risk gates incl. restart persistence, kill switch incl. re-arm and TOCTOU, exit timeout/partial-fill lifecycle, boot reconciliation, failed-exit-keeps-row-open wiring, paper-default invariance, dashboard copy)
- `py-clob-client` pinned in `requirements.txt` and `pyproject.toml`
- **395 total, all passing**

## v0.2.2 — Deterministic Test Fills (2026-06-10)

Fixes #24.

- **Flaky paper-execution tests** — `PaperExecutionManager._determine_fill()` rolled module-level unseeded `random.random()` per order (1% PARTIAL_FILL, 0.1% REJECTED), so FILLED-assuming unit tests failed in ~30-40% of full-suite runs. The RNG is now injectable via a new `rng: random.Random | None` constructor param (default unchanged: fresh unseeded `random.Random()`); test fixtures pin a deterministic always-fill RNG. Partial-fill/reject paths keep their dedicated tests. Verified 20/20 consecutive green runs of `tests/unit/test_paper_execution.py`.

## v0.2.1 — Runtime Blockers (2026-06-10)

Fixes #19.

- **main.py boot crash** — imported `DASHBOARD_PORT`, renamed to `DASHBOARD_SERVER_PORT` in the v0.2 dashboard migration. FastAPI entrypoint never started.
- **Binance endpoint unreachable** — `api.binance.com` was hardcoded in 4 modules and times out on some networks. New `BINANCE_API_BASE` env var (default `https://data-api.binance.vision`, Binance's public market-data mirror with identical `/api/v3` routes) threaded through `btc_bot/paper.py`, `btc_bot/backtest.py`, `btc_5m_fv/backtest/conditional.py`, and `BinanceConnector`.
- **Backtest resilience** — kline fetches in both backtest modules now retry 3× with backoff; a single transient SSL timeout no longer kills a 2,688-combination grid run.

## v0.2.0 — Full System Rebuild (2026-05-28)

A complete architectural rebuild from monolithic demo to modular trading system. Every open GitHub issue has been addressed.

### Architecture
- **New package structure** (`btc_5m_fv/`) with 7 sub-packages: `core`, `strategy`, `connectors`, `storage`, `backtest`, `execution`, `ops`
- **Interface-driven design** — all components implement ABCs from `core.interfaces`
- **`pyproject.toml`** replaces `requirements.txt` with modern Python packaging
- **GitHub Actions CI** — tests on Python 3.11/3.12, lint with ruff, type-check with mypy

### Core (closes #10, #15)
- `core/types.py` — 16 frozen dataclasses (MarketWindow, Signal, Tick, PaperOrder, PaperPosition, etc.)
- `core/interfaces.py` — 5 abstract base classes (market connector, price connector, signal generator, execution manager, risk service)
- `core/exceptions.py` — 5 custom exceptions with hierarchy

### Strategy
- Extracted from `btc_bot/strategy.py` into 3 focused modules:
  - `strategy/fair_value.py` — `sigma_per_second()`, `fair_up_probability()`
  - `strategy/sizing.py` — `confidence_from_edge()`, `notional_from_confidence()`
  - `strategy/signal.py` — `signal_from_edge()` with `SignalAction` enum

### Connectors (closes #11, #12, #16, #9)
- `connectors/polymarket.py` — market discovery with slug pattern matching
- `connectors/binance.py` — spot price, reference price, recent closes with rate limiting awareness
- `connectors/chainlink.py` — stub for Chainlink Data Streams integration (#9)
- `connectors/registry.py` — registration, health checks, history tracking
- All connectors implement `AbstractPriceConnector` / `AbstractMarketConnector`

### Storage & Backtest (closes #2, #3, #4)
- `storage/recorder.py` — `MarketDataRecorder` persists windows, ticks, CLOB snapshots to SQLite
- `storage/replay.py` — `DeterministicReplay` feeds recorded data through signal generator
- `backtest/harness.py` — `FullMarketBacktestHarness` runs strategy on ALL recorded windows
- `backtest/metrics.py` — `BacktestResult` with exit attribution, `FrictionModel` for realistic simulation
- `backtest/conditional.py` — original trade-history conditional backtest preserved

### Execution & Risk (closes #13, #14, #5)
- `execution/paper.py` — `PaperExecutionManager` with explicit order lifecycle: PENDING -> ACKNOWLEDGED -> FILLED
- `execution/risk.py` — `RiskService` with pre-trade checks, drawdown monitoring, win/loss tracking
- `ops/controller.py` — `BotController` unified tick loop

### Operations (closes #6, #7, #8)
- `ops/telemetry.py` — `FeedHealthTracker` (p50/p95/p99 latency) and `LatencyTracker`
- `ops/incidents.py` — `IncidentManager` state machine + `RunbookActions` for every incident type
- `tests/conftest.py` — deterministic fixtures for reproducible tests
- 18 test files, 321 total tests

### Dashboard Migration
- Replaced Gradio (150MB+ dependency) with **FastAPI + Jinja2**
- `ops/dashboard/app.py` — FastAPI with routes, SSE for real-time updates
- `ops/dashboard/templates/` — Jinja2 templates (5 tabs)
- `ops/dashboard/static/` — CSS and vanilla JS
- Server-Sent Events replace `gr.Timer` polling
- Visual design preserved exactly

### Tests
- 288 unit tests (network-free, deterministic)
- 11 integration tests
- 23 e2e tests
- 4 preserved smoke tests
- **321 total, all passing**

---

## v0.1.0 — Initial Demo

Original monolithic implementation:
- Gradio dashboard with custom CSS
- Paper trading loop in `btc_bot/paper.py`
- Strategy math in `btc_bot/strategy.py`
- SQLite persistence in `db.py`
- 4 smoke tests
- 7 commits, 14 Python files
