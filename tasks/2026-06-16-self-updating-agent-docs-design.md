# Design Spec — Self-Updating AI-Agent Documentation System

**Project:** btc-5m-binary-fair-value (Polymarket BTC 5-minute binary fair-value trading bot)
**Date:** 2026-06-16
**Status:** Approved design → implementation plan next
**Author:** Zayan (byline only)

---

## 1. Problem

The repo already has docs (`AGENTS.md`, `README.md`, `PRD.md`, 28KB `CHANGELOG.md`, `docs/{ARCHITECTURE,BACKTESTING,CHRONOS_INTEGRATION,FILE_MAP,OPERATIONS_RUNBOOK,RESEARCH_LOOP,ROADMAP}.md`). The problem is not absence — it is that **the docs actively mislead an AI agent**, because each describes only one half of a two-halved system and they contradict each other.

Verified by import tracing across a 21-agent audit:

- `README.md` / `PRD.md` / `docs/ARCHITECTURE.md` describe `btc_5m_fv/` as "the codebase" and never mention `btc_bot/`.
- `docs/FILE_MAP.md` / `docs/ROADMAP.md` / `docs/RESEARCH_LOOP.md` describe `btc_bot/` as "the codebase" and never mention `btc_5m_fv/`.
- They disagree on the dashboard framework (Gradio vs FastAPI), the entry point, and which tree is canonical.

Consequence: an agent told to "fix the live signal" via `README` edits `btc_5m_fv/strategy/` — which only feeds **backtests**. The live signal is `btc_bot/strategy.py`. There is **no routing table anywhere** answering *"to change X, edit which file?"*

Two staleness bugs are real-money footguns:
1. `README.md`: *"No live orders are placed. No private key required."* — **false**; `btc_5m_fv/execution/live.py` is a full, gated live CLOB executor.
2. `docs/OPERATIONS_RUNBOOK.md`: go-live sets only `BTC_LIVE_CONFIRM`, **omits `BTC_BOT_MODE=live`** — both required, so the runbook literally leaves the bot paper-trading while the UI reads "armed."

## 2. Goal

A documentation system where any coding agent (Claude Code **or** Codex) can orient in under one screen, correctly route a change to the right file/tree, and where the facts that change cannot silently rot — because they are machine-generated and drift is CI-enforced.

## 3. Governing principle

Every fact lives in exactly one of two buckets, never mixed:

- **Facts that rot** → machine-generated, never hand-typed: module inventory, role one-liners, wired-vs-dead status, import coupling, test count, env-knob table, entrypoint assertion.
- **Facts that endure** → hand-written in fenced blocks the generator never touches: safety rules, the two-tree routing logic, the "why."

## 4. Verified architecture facts (the source of truth the docs must encode)

**Both `btc_5m_fv/` and `btc_bot/` are CANONICAL and ACTIVE. Neither is legacy.** They are bidirectionally coupled halves of one live system.

| Concern | Lives in |
|---|---|
| Trading loop, signal math, paper fills, self-improvement (calibration, adaptive pause, params) | **`btc_bot/`** (`paper.py:run_paper_loop` is *the* loop; `btc_bot/strategy.py` is the live signal math) |
| Execution gates, live CLOB executor, connectors, dashboard, recorder, backtest harness | **`btc_5m_fv/`** (`execution/gate.py:RiskGate`, `execution/live.py:LiveExecutor`) |
| Shared foundation (imported by both, imports neither) | top-level `config.py` / `db.py` / `logging_setup.py` |

- **Entry:** `python main.py` → singleton `fcntl` lock (`data/bot.lock`) → `init_db` → uvicorn serving the FastAPI app `btc_5m_fv/ops/dashboard/app.py`. The Gradio `dashboard.py` is a fallback branch that never executes (`HAS_NEW_DASHBOARD` is always true).
- **Loop start:** operator presses ▶ Start in dashboard → `btc_bot/controller.py:request_start` → daemon thread runs `run_paper_loop`.
- **Coupling:** FastAPI app imports `btc_bot.{controller,paper,history,backtest}`; `btc_bot` imports back into `btc_5m_fv.{execution.live,execution.gate,connectors.chainlink_settlement}`.
- **Live trading exists, multi-gated:** `BTC_BOT_MODE=live` AND `BTC_LIVE_CONFIRM=YES_I_UNDERSTAND` AND private key present AND coherent wallet AND clean config parse. Agents never flip the gate; the operator launches.

**Genuinely dead / built-but-unwired (docs must flag so agents don't edit expecting effect):**
- top-level `dashboard.py` (Gradio fallback — never served).
- `btc_5m_fv/ops/controller.py:BotController`, `execution/risk.py:RiskService`, `connectors/registry.py` + the ABC connector classes (registry architecture built, live loop bypasses it).
- `btc_5m_fv/{execution/paper.py, storage/replay.py, backtest/*}` — unwired on the live path (live backtest uses `btc_bot/backtest.py`; dashboard reads precomputed `data/backtests/latest.json`).

**Dual forks (same logic in both trees, no import link):** `sigma_per_second` / `fair_up_probability` / `signal_from_edge` exist in *both* `btc_bot/strategy.py` and `btc_5m_fv/strategy/*`; `RiskGate` (live) vs `RiskService` (dead).

**Other gotchas:** new DB columns go in `db.py` migration dicts (not by editing `SCHEMA`); `BTC_TRADE_*` are canonical knobs, `BTC_LIVE_*` are deprecated read-aliases (`config.py:168-184`); `app.py:43-46` still imports the deprecated aliases (a drift point to fix); redaction is exact-value match — every new persistence sink must call `redact_secrets`.

## 5. Target doc set

```
AGENTS.md          Tier 0: constitution + START HERE + injected generated summary
  └─ CLAUDE.md     2-line pointer (Claude Code & Codex both land on the same source)
docs/CODE_MAP.md   Tier 1 KEYSTONE (NEW): two-tree truth + "change X → edit Y" routing
                   table + runtime flow + WIRED-vs-DEAD table + dual-fork warnings
docs/FILE_MAP.md   Tier 2: 100% GENERATED (do-not-edit) module index
CHANGELOG.md       keep (the "why"), append-only, tail fixes (knob renames, test count, PRs)
docs/OPERATIONS_RUNBOOK.md   keep, FIX go-live bug (require both env vars)
docs/BACKTESTING.md          keep, light fix (harness exists but unwired)
docs/CHRONOS_INTEGRATION.md  keep, fix fictional flag/CLI/API names
docs/RESEARCH_LOOP.md        keep (honest aspirational), cite params_propose/apply as shipped v0
docs/ARCHITECTURE.md, PRD.md fold into CODE_MAP/AGENTS, leave deprecation stubs
docs/ROADMAP.md              heavy-prune (strip already-shipped items)
tools/gen_docs.py            NEW generator (stdlib only)
.github/workflows/ci.yml     + docs-drift job (+ extend lint to btc_bot/)
.claude/settings.json        NEW hooks (SessionStart / PostToolUse / Stop)
```

### 5.1 Keystone — `docs/CODE_MAP.md`
Hand-written narrative + generated tables:
1. Two-tree verdict (§4) — both active, bidirectionally coupled.
2. **Routing table** — *"to change X, edit Y"*: live signal → `btc_bot/strategy.py`; risk gate → `btc_5m_fv/execution/gate.py`; new connector → `btc_5m_fv/connectors/`; dashboard panel → `btc_5m_fv/ops/dashboard/panels/`; env knob → `config.py` + `.env.example`; DB column → `db.py` migration dict.
3. Runtime flow — the one-screen ASCII map (main.py → FastAPI → `run_paper_loop` → feed/signal/risk/execute/store).
4. **WIRED vs BUILT-BUT-DEAD** (generated block).
5. Dual-fork warnings.

### 5.2 Generator — `tools/gen_docs.py` (no new dependencies; stdlib `ast`, `pathlib`)
Deterministic (stable sort, no timestamps, so `git diff --exit-code` is meaningful). Derives:
- **Module inventory + role** — tree walk over `btc_5m_fv/`, `btc_bot/`, top-level `*.py`, `tools/`; role = first line of each module's top docstring; missing → `(needs docstring)` marker (pressure, not failure).
- **Wired-vs-dead** — `ast`-parse imports, count non-test importers per module; zero ⇒ flagged DEAD, with an allowlist for true entrypoints (`main.py`).
- **Env-knob table** — parse `config.py` for `BTC_TRADE_*` canonical vs `BTC_LIVE_*` deprecated-alias map.
- **Test count** — `pytest --collect-only -q` (currently 488; docs say 321).
- **Entrypoint assertion** — `import btc_5m_fv.ops.dashboard.app` must succeed.

Writes `docs/FILE_MAP.md` in full; replaces only content between `<!-- BEGIN GENERATED:x -->` / `<!-- END GENERATED:x -->` markers in `AGENTS.md` and `docs/CODE_MAP.md`. Supports `--check` (regenerate to temp, diff, exit nonzero on drift) and `--print-summary` (emit the CODE_MAP summary block for hook injection).

### 5.3 Enforcement — CI `docs-drift` job (in existing `ci.yml`)
1. `python tools/gen_docs.py` → `git diff --exit-code` on generated files → fail if stale.
2. Banned-stale-string grep across **both** trees + docs: `"321 tests"`, `"No live orders"`, `"no private key required"`, `"gradio.Blocks"` (agent docs), `"live trading is intentionally absent"` → fail.
3. Assert entrypoint import succeeds.
Catches drift from any tool (Claude Code, Codex, manual). Note: current lint covers only `btc_5m_fv/ tests/`; drift check must also scan `btc_bot/`.

### 5.4 Self-heal — `.claude/settings.json` hooks (greenfield; `settings.local.json` keeps permissions)
- **SessionStart** → `gen_docs.py --print-summary` injected into context; every Claude session starts knowing the two-tree routing + wired/dead + test count.
- **PostToolUse** (matcher: Edit/Write on `config.py`, any `__init__.py`, new `.py`, `db.py`) → run `gen_docs.py` to regenerate; map self-heals mid-session. Fast + quiet; never fails the tool.
- **Stop** → run `gen_docs.py --check` locally so the agent sees drift before finishing.
Hooks fire only in Claude Code; the CI gate is the backstop for Codex/manual edits.

## 6. Safety fixes (immediate, hand edits)
1. `README.md` — replace "no live orders / no private key" with the real multi-gate invariant (§4).
2. `docs/OPERATIONS_RUNBOOK.md` — go-live requires **both** `BTC_BOT_MODE=live` and `BTC_LIVE_CONFIRM`.

## 7. Build order
1. `tools/gen_docs.py` + generate `FILE_MAP.md` (prove determinism: run twice, no diff).
2. Hand-write `CODE_MAP.md` (routing + narrative + fenced generated blocks).
3. Rework `AGENTS.md` (reword live-safety, absorb PRD scope, START-HERE + generated block); add `CLAUDE.md` pointer.
4. Fix README + RUNBOOK safety bugs; light-fix BACKTESTING/CHRONOS/RESEARCH_LOOP; prune ROADMAP; stub-deprecate ARCHITECTURE + PRD.
5. CI `docs-drift` job (+ lint `btc_bot/`).
6. `.claude/settings.json` hooks.
7. Flag dead `dashboard.py` in CODE_MAP (flag, not delete).

## 8. Verification
- Generator deterministic: run twice → zero diff.
- CI drift-gate: fails on a deliberately introduced stale string; passes on clean tree.
- `git diff --exit-code` clean after generation on a clean tree.
- SessionStart hook injection visible in a fresh Claude Code session.
- All existing tests still pass (`pytest tests/`); no runtime code touched.

## 9. Risks
- **Docstring dependency:** generator pulls role lines from top docstrings; modules lacking them render `(needs docstring)`. First pass may add a handful of one-line docstrings; generator must not fail on absence.
- **Hook noise:** PostToolUse regeneration must be fast and silent; matcher scoped tightly to structural files.
- **Multi-tool reality:** AGENTS.md targets Codex, CLAUDE.md targets Claude Code; both must carry the same generated block.

## 10. Out of scope (explicit)
- No trading-logic changes.
- No deleting code — dead `dashboard.py` and the dead `btc_5m_fv` registry/controller get **flagged** in CODE_MAP, not removed (separate refactor decision; not authored by this work).
- No resolving the two-tree fork itself (a refactor). The docs make the fork *navigable*, not gone.

## 11. Workflow
Per repo CI/CD rules: open a GitHub issue, branch `feature/<id>-self-updating-agent-docs` off `develop`, run tests before committing, push to `develop`. Never merge to `main`.
