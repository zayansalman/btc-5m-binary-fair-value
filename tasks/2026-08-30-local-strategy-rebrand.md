# Local strategy rebuild + full rebrand — self-paced /loop, started 2026-08-30

Running scratchpad for a `/loop 10m` build effort. Each tick reads this file,
adds a dated entry, and either continues the build or concludes. Follows this
project's own AGENTS.md convention: an agent never arms a live gate or places
a real order — this loop builds paper-trading infrastructure only.

## Decisions locked this session (do not re-litigate; if evidence contradicts
one, stop and flag it to the operator rather than silently overriding)

1. **No cloud hosting.** Local-only build, runs on the operator's machine.
2. **Stop all BTC work, stop all 5-minute-market work.** Both were already
   concluded dead in `tasks/2026-08-17-strategy-discussion.md` (tick 2) and
   `docs/archive/PIVOT_2026-07.md` — fees exceed the real edge in a
   latency-dominated venue, for both direction-prediction and copytrading
   mechanisms, on BTC specifically.
3. **New asset focus: non-BTC altcoins with less efficient/thinner markets**
   — DOGE is the operator's explicit flagship example ("the other markets
   like doge which are not efficient"). Thesis: retail-driven, thin-book
   altcoin markets are more likely to carry real, slower-moving mispricing
   than BTC's heavily-arbitraged book. This is a **hypothesis to shadow-test,
   not yet a finding** — same discipline as every prior chapter in this repo
   (shadow before capital, pre-registered bar, fee-true accounting).
4. **New timeframe: daily (24h window), not 5-minute.** Operator confirmed
   "Daily [asset] up/down (24h window)" as the mechanic before narrowing the
   asset away from BTC — so the up/down-on-a-clock-window mechanic carries
   forward, just on a 24h clock instead of 5m, and on alts instead of BTC.
   Removes the latency race entirely (24h is not a speed game); fee drag as
   % of a slower-moving edge should be far smaller than it was at 5m.
5. **Paper trade only, $10 positions.** No live gate touched. This is a
   shadow/paper build, same as every strategy in this repo's history before
   any live-arming discussion.
6. **Must be visible in the dashboard UI** — a new panel showing the
   strategy's positions, PnL, and a plain-language explanation of the
   mechanism (mirrors the existing panel architecture per `tasks/lessons.md`
   "Respect the existing UI architecture for dashboard work").
7. **Full rebrand, in-repo only** (not the GitHub repo itself — operator did
   not ask for that and it wasn't offered). New project name:
   **`polymarket-crypto`** (operator's explicit choice, after rejecting a
   generic-lab-name suggestion and a thesis-named suggestion). Package rename
   map decided this tick (no operator input on internal names, so this is my
   call, consistent with the chosen project name):
   - `btc_bot/` → `bot/`
   - `btc_5m_exec/` → `exec_engine/`
   - `pyproject.toml` `name = "btc-5m-exec"` → `"polymarket-crypto"`
   - `BTC_*`-prefixed env vars / config constants → same name minus the
     `BTC_` prefix (e.g. `BTC_BOT_MODE` → `BOT_MODE`,
     `BTC_LIVE_MAX_TRADE_USD` → `LIVE_MAX_TRADE_USD`). Safe to do cleanly:
     confirmed no `.env` file exists in this repo, so there is no existing
     operator config to break.
   - Deleted `btc_5m_fv/` and `btc_5m_exec.egg-info/` — confirmed both 100%
     untracked build/cache leftovers (zero `.py` source in `btc_5m_fv`,
     zero tracked files in either) predating the #169 rename, not real
     content.
   - Playbook follows the #169 precedent (`981c46a`,
     "refactor(#169): remove 'fair value' branding — btc_5m_fv →
     btc_5m_exec"): `git mv` directories, sweep text refs, regenerate
     `docs/FILE_MAP.md`/`docs/CODE_MAP.md` via `tools/gen_docs.py` (never
     hand-edit), run full test suite before committing.

## Known pre-existing gap (not part of this build, flagging so a future tick
doesn't rediscover it from scratch)

Read-only connector/API health check this session (before the rebrand)
found: Polymarket/CLOB/Gamma APIs and the Chainlink BTC feed all reachable;
`live_preflight.py` refuses cleanly with no `.env`; 177/177 targeted
connector/live-executor tests pass. One real gap — **not urgent, no wallet
configured yet**: `tools/live_detect_wallet.py`'s `polymarket` SDK import
(`polymarket.environments`, `polymarket._internal.wallet`) is not installed
in this environment (it's an optional extra never pulled into the base
install). Only matters once the operator actually tries to connect a real
MetaMask wallet — flag it then, don't fix speculatively now.

## Market research (this session, before build)

Polymarket Gamma API confirmed **non-5m altcoin markets exist and are
liquid**: e.g. `will-dogecoin-reach-0pt2-in-august-2026` — a monthly DOGE
price-threshold market, $105.8k volume, $37.2k liquidity, Binance
DOGE/USDT-sourced resolution. This is a **threshold-by-date** market shape
(will DOGE cross $X before date Y), not a repeating 24h up/down clock like
the old 5m family. **Open question for the next tick, before writing any
strategy code**: does Polymarket actually run a *repeating daily* DOGE
up/down market (the mechanic the operator confirmed), or only these
monthly/longer threshold markets? If only threshold markets exist for DOGE,
the mechanic needs to adapt to that market shape instead — check the Gamma
API for a recurring daily-close market on doge/sol/xrp/bnb before assuming
the 24h-up/down mechanic has a matching market to trade. This is a
precondition, not a detail — get it right before building the signal.

---

## Tick 1 (2026-08-30)

Wrote this scratchpad. Executing the mechanical rebrand (directory moves +
repo-wide text sweep + docs regen + test verification) this tick, per the
playbook above. Strategy design/build is next tick's work, gated on
resolving the "does a repeating daily market exist for these assets" open
question first.
