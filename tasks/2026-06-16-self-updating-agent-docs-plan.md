# Self-Updating AI-Agent Documentation System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give any coding agent a correct, self-updating map of the btc-5m-binary-fair-value codebase so it can orient in one screen and route any change to the right file — with drift made un-mergeable by CI and auto-healed by Claude hooks.

**Architecture:** A stdlib-only generator (`tools/gen_docs.py`) owns the facts that rot (module inventory, wired-vs-dead status, env knobs, test count) and writes them into `docs/FILE_MAP.md` (full file) and fenced `<!-- GENERATED -->` blocks inside hand-written `AGENTS.md` / `docs/CODE_MAP.md`. A CI `docs-drift` job regenerates and `git diff --exit-code`s to block stale docs; `.claude/settings.json` hooks regenerate on edit and inject the map at session start.

**Tech Stack:** Python 3.11 (stdlib `ast`, `argparse`, `pathlib`, `subprocess`), pytest, GitHub Actions YAML, Claude Code hooks JSON.

**Spec:** `tasks/2026-06-16-self-updating-agent-docs-design.md`

---

## File Structure

| File | New/Mod | Responsibility |
|---|---|---|
| `tools/gen_docs.py` | Create | The generator: inventory, import-graph wired/dead, env-knob table, test count; render FILE_MAP + fenced blocks; CLI `--check`/`--fast`/`--print-summary` |
| `tests/unit/test_gen_docs.py` | Create | Unit tests (fixture trees) + real-tree smoke assertions for load-bearing facts |
| `docs/FILE_MAP.md` | Replace | 100% generated module index (do-not-edit) |
| `docs/CODE_MAP.md` | Create | KEYSTONE: two-tree routing narrative + generated blocks |
| `AGENTS.md` | Modify | Constitution reword + START HERE + absorb PRD scope + generated summary block |
| `CLAUDE.md` | Create | 2-line pointer to AGENTS.md |
| `README.md` | Modify | Fix the "no live orders" safety bug |
| `docs/OPERATIONS_RUNBOOK.md` | Modify | Fix go-live (require both env vars) |
| `docs/BACKTESTING.md`, `docs/CHRONOS_INTEGRATION.md`, `docs/RESEARCH_LOOP.md` | Modify | Light factual fixes |
| `docs/ROADMAP.md` | Modify | Strip already-shipped items |
| `docs/ARCHITECTURE.md`, `PRD.md` | Modify | Replace body with deprecation stub pointing to CODE_MAP/AGENTS |
| `.github/workflows/ci.yml` | Modify | Add `docs-drift` job; extend lint to `btc_bot/` |
| `.claude/settings.json` | Create | SessionStart / PostToolUse / Stop hooks |

---

## Task 0: GitHub issue + feature branch

**Files:** none (repo setup)

- [ ] **Step 1: Confirm clean tree on develop**

Run: `cd /Users/zayankhan/projects/btc-5m-binary-fair-value && git status -sb && git branch --show-current`
Expected: branch `develop`, working tree clean except the two `tasks/2026-06-16-*.md` files (untracked — that's fine).

- [ ] **Step 2: Create the issue**

Run:
```bash
gh issue create --title "Self-updating AI-agent documentation system" \
  --body "Docs mislead agents: each describes only one of the two coupled code trees (btc_bot live loop vs btc_5m_fv execution/dashboard) and they contradict each other. Add a generated FILE_MAP + a hand-written CODE_MAP routing table, fix two real-money safety-doc bugs, and enforce freshness via a CI drift gate + .claude hooks. Spec: tasks/2026-06-16-self-updating-agent-docs-design.md" \
  --label documentation
```
Expected: prints the new issue URL. Note the issue number as `<id>`.

- [ ] **Step 3: Branch from develop**

Run: `git checkout develop && git pull --ff-only && git checkout -b feature/<id>-self-updating-agent-docs`
Expected: `Switched to a new branch 'feature/<id>-self-updating-agent-docs'`.

- [ ] **Step 4: Commit the spec + plan**

```bash
git add tasks/2026-06-16-self-updating-agent-docs-design.md tasks/2026-06-16-self-updating-agent-docs-plan.md
git commit -m "docs: spec + plan for self-updating agent docs (refs #<id>)"
```

---

## Task 1: Generator — module inventory

**Files:**
- Create: `tools/gen_docs.py`
- Test: `tests/unit/test_gen_docs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_gen_docs.py
from pathlib import Path
import tools.gen_docs as gd


def _make_tree(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""Pkg root."""\n')
    (pkg / "alpha.py").write_text('"""Computes alpha."""\nX = 1\n')
    (pkg / "beta.py").write_text("X = 2\n")  # no docstring
    return tmp_path


def test_inventory_reads_roles(tmp_path):
    root = _make_tree(tmp_path)
    mods = gd.collect_modules(root, source_roots=["pkg"], toplevel=[])
    by_path = {m.path: m for m in mods}
    assert by_path["pkg/alpha.py"].role == "Computes alpha."
    assert by_path["pkg/beta.py"].role == gd.NO_DOCSTRING
    assert by_path["pkg/__init__.py"].role == "Pkg root."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.gen_docs'` (or `AttributeError: collect_modules`).

- [ ] **Step 3: Write minimal implementation**

```python
# tools/gen_docs.py
"""Generate the machine-derived sections of the agent docs.

Owns the facts that rot: module inventory + roles, wired-vs-dead status,
env-knob table, test count. Writes docs/FILE_MAP.md in full and replaces
fenced GENERATED blocks in AGENTS.md and docs/CODE_MAP.md.

Deterministic: stable sort, no timestamps, so `git diff --exit-code` is meaningful.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NO_DOCSTRING = "(needs docstring)"

SOURCE_ROOTS = ["btc_5m_fv", "btc_bot", "tools"]
TOPLEVEL_MODULES = ["main.py", "config.py", "db.py", "logging_setup.py", "dashboard.py"]
# Entrypoints / foundation: never flagged DEAD even with zero importers.
WIRED_ALLOWLIST = {"main.py", "config.py", "db.py", "logging_setup.py"}


@dataclass
class Module:
    path: str           # repo-relative posix path
    role: str           # first line of top docstring, or NO_DOCSTRING
    importers: int = 0  # count of non-test modules importing this one

    @property
    def status(self) -> str:
        if self.path in WIRED_ALLOWLIST or self.importers > 0:
            return "WIRED"
        return "DEAD?"


def _role_from_source(text: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return NO_DOCSTRING
    doc = ast.get_docstring(tree)
    if not doc:
        return NO_DOCSTRING
    return doc.strip().splitlines()[0].strip()


def collect_modules(root: Path, source_roots=SOURCE_ROOTS, toplevel=TOPLEVEL_MODULES):
    mods: list[Module] = []
    for rel in toplevel:
        p = root / rel
        if p.exists():
            mods.append(Module(path=rel, role=_role_from_source(p.read_text())))
    for sr in source_roots:
        base = root / sr
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(root).as_posix()
            mods.append(Module(path=rel, role=_role_from_source(p.read_text())))
    return sorted(mods, key=lambda m: m.path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/gen_docs.py tests/unit/test_gen_docs.py
git commit -m "feat(docs): gen_docs module inventory with docstring roles (refs #<id>)"
```

---

## Task 2: Generator — wired-vs-dead import graph

**Files:**
- Modify: `tools/gen_docs.py`
- Test: `tests/unit/test_gen_docs.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_gen_docs.py
def test_import_graph_flags_dead(tmp_path):
    root = tmp_path
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "alpha.py").write_text('"""A."""\nX = 1\n')
    (pkg / "beta.py").write_text('"""B."""\nfrom pkg.alpha import X\n')  # imports alpha
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("from pkg.beta import X\n")  # test-only importer

    mods = gd.collect_modules(root, source_roots=["pkg"], toplevel=[])
    gd.annotate_importers(root, mods, test_dirs=["tests"])
    by = {m.path: m for m in mods}
    assert by["pkg/alpha.py"].status == "WIRED"   # imported by beta (non-test)
    assert by["pkg/beta.py"].status == "DEAD?"     # only a test imports it
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py::test_import_graph_flags_dead -q`
Expected: FAIL — `AttributeError: module 'tools.gen_docs' has no attribute 'annotate_importers'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/gen_docs.py

def _dotted_name(rel_path: str) -> str:
    parts = rel_path[:-3].split("/")  # strip .py
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_targets(text: str) -> set[str]:
    """Dotted names this module references via import / from-import."""
    targets: set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return targets
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                targets.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:  # relative import without module — skip
                continue
            targets.add(node.module)
            for a in node.names:  # `from pkg import mod` → pkg.mod is a candidate
                targets.add(f"{node.module}.{a.name}")
    return targets


def annotate_importers(root: Path, mods, test_dirs=("tests",)) -> None:
    known = {_dotted_name(m.path): m for m in mods}
    for src in sorted(root.rglob("*.py")):
        if "__pycache__" in src.parts:
            continue
        rel = src.relative_to(root).as_posix()
        if any(rel.startswith(td + "/") or rel == td for td in test_dirs):
            continue  # test importers don't count toward "wired"
        if rel in {m.path for m in mods} is False:
            pass
        for tgt in _imported_targets(src.read_text()):
            mod = known.get(tgt)
            if mod is not None and mod.path != rel:
                mod.importers += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py -q`
Expected: PASS (both tests).

- [ ] **Step 5: Add a real-tree smoke test (encodes the architecture truth)**

```python
# append to tests/unit/test_gen_docs.py
def test_real_tree_wiring_truth():
    """The load-bearing facts the docs must never get wrong."""
    mods = gd.collect_modules(gd.REPO)
    gd.annotate_importers(gd.REPO, mods)
    by = {m.path: m for m in mods}
    # The live loop and its signal math are WIRED.
    assert by["btc_bot/paper.py"].status == "WIRED"
    assert by["btc_bot/strategy.py"].status == "WIRED"
    # The live risk gate + executor are WIRED.
    assert by["btc_5m_fv/execution/gate.py"].status == "WIRED"
    assert by["btc_5m_fv/execution/live.py"].status == "WIRED"
    # Known dead-in-active-tree module is flagged.
    assert by["btc_5m_fv/ops/controller.py"].status == "DEAD?"
```

- [ ] **Step 6: Run the smoke test**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py::test_real_tree_wiring_truth -q`
Expected: PASS. If `ops/controller.py` shows WIRED, an importer appeared — re-verify with `grep -rn "ops.controller\|ops import controller" --include='*.py' . | grep -v tests` and update the spec's dead-list, not the assertion.

- [ ] **Step 7: Commit**

```bash
git add tools/gen_docs.py tests/unit/test_gen_docs.py
git commit -m "feat(docs): import-graph wired/dead detection + real-tree truth test (refs #<id>)"
```

---

## Task 3: Generator — env-knob table, test count, entrypoint assertion

**Files:**
- Modify: `tools/gen_docs.py`
- Test: `tests/unit/test_gen_docs.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_gen_docs.py
def test_test_count_is_positive_int():
    n = gd.count_tests(gd.REPO)
    assert isinstance(n, int) and n > 300  # currently ~488

def test_entrypoint_importable():
    assert gd.entrypoint_ok(gd.REPO) is True  # btc_5m_fv.ops.dashboard.app imports
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py::test_test_count_is_positive_int -q`
Expected: FAIL — `AttributeError: ... 'count_tests'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/gen_docs.py

def count_tests(root: Path) -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        cwd=root, capture_output=True, text=True,
    )
    # pytest prints a trailing summary line like "488 tests collected in 1.2s"
    for line in reversed(out.stdout.splitlines()):
        line = line.strip()
        if "test" in line and line.split()[0].isdigit():
            return int(line.split()[0])
    return 0


def entrypoint_ok(root: Path) -> bool:
    res = subprocess.run(
        [sys.executable, "-c", "import btc_5m_fv.ops.dashboard.app"],
        cwd=root, capture_output=True, text=True,
    )
    return res.returncode == 0


def collect_env_knobs(root: Path):
    """Parse config.py for BTC_* knob names + their deprecated aliases.

    Returns sorted list of (canonical, default, deprecated_alias|''). Best-effort:
    reads the literal os.environ.get / _trade_knob string args via AST.
    """
    cfg = (root / "config.py").read_text()
    tree = ast.parse(cfg)
    knobs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("BTC_") and v.isupper():
                knobs.setdefault(v, "")
    return sorted(knobs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py -q`
Expected: PASS (all). `count_tests` shells out to pytest collection on the real tree.

- [ ] **Step 5: Commit**

```bash
git add tools/gen_docs.py tests/unit/test_gen_docs.py
git commit -m "feat(docs): test-count, entrypoint assertion, env-knob scan (refs #<id>)"
```

---

## Task 4: Generator — rendering, fenced-block replacement, CLI

**Files:**
- Modify: `tools/gen_docs.py`
- Test: `tests/unit/test_gen_docs.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_gen_docs.py
def test_replace_block_is_idempotent():
    doc = "intro\n<!-- BEGIN GENERATED:x -->\nOLD\n<!-- END GENERATED:x -->\nrest\n"
    once = gd.replace_block(doc, "x", "NEW")
    twice = gd.replace_block(once, "x", "NEW")
    assert "NEW" in once and "OLD" not in once
    assert once == twice  # deterministic / idempotent

def test_render_file_map_deterministic():
    a = gd.render_file_map(gd.REPO)
    b = gd.render_file_map(gd.REPO)
    assert a == b and a.startswith("<!-- GENERATED by tools/gen_docs.py")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py::test_replace_block_is_idempotent -q`
Expected: FAIL — `AttributeError: ... 'replace_block'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/gen_docs.py
import re

GEN_HEADER = "<!-- GENERATED by tools/gen_docs.py — DO NOT EDIT BY HAND -->"


def replace_block(doc: str, name: str, new_body: str) -> str:
    begin = f"<!-- BEGIN GENERATED:{name} -->"
    end = f"<!-- END GENERATED:{name} -->"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    repl = f"{begin}\n{new_body.rstrip()}\n{end}"
    if not pattern.search(doc):
        raise ValueError(f"marker block '{name}' not found")
    return pattern.sub(repl, doc)


def _table(mods) -> str:
    rows = ["| Module | Status | Importers | Role |", "|---|---|---|---|"]
    for m in mods:
        rows.append(f"| `{m.path}` | {m.status} | {m.importers} | {m.role} |")
    return "\n".join(rows)


def render_file_map(root: Path) -> str:
    mods = collect_modules(root)
    annotate_importers(root, mods)
    return f"{GEN_HEADER}\n\n# File Map\n\n{_table(mods)}\n"


def render_summary(root: Path, with_test_count: bool = True) -> str:
    mods = collect_modules(root)
    annotate_importers(root, mods)
    dead = [m.path for m in mods if m.status == "DEAD?"]
    n = count_tests(root) if with_test_count else "(see FILE_MAP)"
    lines = [
        f"- **Trees:** `btc_bot/` = live loop + signal math; `btc_5m_fv/` = execution/connectors/dashboard/backtest; top-level `config.py`/`db.py`/`logging_setup.py` = foundation. Both ACTIVE, bidirectionally coupled.",
        f"- **Entry:** `python main.py` → FastAPI `btc_5m_fv/ops/dashboard/app.py`; loop starts on operator ▶ Start → `btc_bot/controller.py:request_start`.",
        f"- **Tests:** {n}.",
        f"- **Built-but-dead (do not edit expecting runtime effect):** {', '.join(f'`{d}`' for d in dead) or 'none'}.",
    ]
    return "\n".join(lines)


def _write_generated(root: Path, fast: bool) -> None:
    (root / "docs" / "FILE_MAP.md").write_text(render_file_map(root))
    summary = render_summary(root, with_test_count=not fast)
    inv = _table_section(root)
    for rel, blocks in (
        ("AGENTS.md", {"summary": summary}),
        ("docs/CODE_MAP.md", {"summary": summary, "inventory": inv}),
    ):
        p = root / rel
        if not p.exists():
            continue
        doc = p.read_text()
        for name, body in blocks.items():
            if fast and name == "summary":
                # don't clobber the test-count line on fast runs
                body = render_summary(root, with_test_count=False)
            try:
                doc = replace_block(doc, name, body)
            except ValueError:
                pass  # block not present in this doc
        p.write_text(doc)


def _table_section(root: Path) -> str:
    mods = collect_modules(root)
    annotate_importers(root, mods)
    return _table(mods)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate agent docs.")
    ap.add_argument("--check", action="store_true", help="fail if generation would change committed docs")
    ap.add_argument("--fast", action="store_true", help="skip test-count (for hooks)")
    ap.add_argument("--print-summary", action="store_true", help="print the orientation summary and exit")
    args = ap.parse_args(argv)

    if args.print_summary:
        print(render_summary(REPO, with_test_count=False))
        return 0

    if args.check:
        before = {p: (REPO / p).read_text() for p in ["docs/FILE_MAP.md", "AGENTS.md", "docs/CODE_MAP.md"] if (REPO / p).exists()}
        _write_generated(REPO, fast=False)
        changed = [p for p, txt in before.items() if (REPO / p).read_text() != txt]
        if changed:
            print("DOC DRIFT — regenerate with `python tools/gen_docs.py`:\n  " + "\n  ".join(changed), file=sys.stderr)
            return 1
        return 0

    _write_generated(REPO, fast=args.fast)
    if not entrypoint_ok(REPO):
        print("WARNING: btc_5m_fv.ops.dashboard.app failed to import — Gradio fallback would activate.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/unit/test_gen_docs.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add tools/gen_docs.py tests/unit/test_gen_docs.py
git commit -m "feat(docs): rendering, idempotent fenced-block replace, CLI (refs #<id>)"
```

---

## Task 5: Generate FILE_MAP.md (prove determinism)

**Files:**
- Replace: `docs/FILE_MAP.md`

- [ ] **Step 1: Generate**

Run: `./.venv/bin/python tools/gen_docs.py`
Expected: rewrites `docs/FILE_MAP.md` with the `GENERATED` header + module table; no warning printed (entrypoint imports OK).

- [ ] **Step 2: Verify determinism**

Run: `./.venv/bin/python tools/gen_docs.py && git diff --quiet -- docs/FILE_MAP.md && echo DETERMINISTIC`
Expected: prints `DETERMINISTIC` (second run produced no diff).

- [ ] **Step 3: Eyeball the dead-list**

Run: `grep -n "DEAD?" docs/FILE_MAP.md`
Expected: includes `btc_5m_fv/ops/controller.py`, `btc_5m_fv/execution/risk.py`, `dashboard.py`. If a clearly-live module shows DEAD?, add it to `WIRED_ALLOWLIST` (only for true entrypoints) — do not weaken the detector for ordinary modules.

- [ ] **Step 4: Commit**

```bash
git add docs/FILE_MAP.md
git commit -m "docs: regenerate FILE_MAP from source tree (refs #<id>)"
```

---

## Task 6: Write CODE_MAP.md (keystone) + fill generated blocks

**Files:**
- Create: `docs/CODE_MAP.md`

- [ ] **Step 1: Write the hand-authored doc with empty generated blocks**

Create `docs/CODE_MAP.md` with this exact skeleton (prose from spec §4–5.1):

````markdown
# Code Map — where to make what change

> Read this first. This repo is **two coupled code trees**, not one. Most doc confusion
> comes from treating one as "the codebase" and the other as legacy. Both are LIVE.

## The two trees

| Concern | Edit here |
|---|---|
| Trading loop, signal math, paper fills, self-improvement (calibration, adaptive pause, params) | **`btc_bot/`** — `paper.py:run_paper_loop` is *the* loop; `btc_bot/strategy.py` is the live signal math |
| Execution gates, live CLOB executor, connectors, dashboard, recorder, backtest harness | **`btc_5m_fv/`** — `execution/gate.py:RiskGate`, `execution/live.py:LiveExecutor` |
| Config, DB schema, logging (foundation; imported by both, imports neither) | top-level **`config.py` / `db.py` / `logging_setup.py`** |

They are **bidirectionally coupled**: the FastAPI dashboard imports `btc_bot.*`; `btc_bot` imports back into `btc_5m_fv.{execution,connectors}`.

## "I want to change X → edit Y"

| Change | File |
|---|---|
| The **live** trading signal / edge / confidence | `btc_bot/strategy.py` (NOT `btc_5m_fv/strategy/` — that only feeds backtests) |
| Risk limits / kill-switch / daily-loss halt | `btc_5m_fv/execution/gate.py` |
| Live order placement | `btc_5m_fv/execution/live.py` |
| Add/modify a market or price feed | `btc_5m_fv/connectors/` |
| Dashboard panel / UI | `btc_5m_fv/ops/dashboard/panels/` |
| An env knob / default | `config.py` + document in `.env.example` |
| A new DB column | `db.py` migration dict (NOT the `SCHEMA` literal) |
| Start/Stop behavior | `btc_bot/controller.py` |

## Runtime flow

```
main.py ─ singleton lock + init_db ─▶ uvicorn ─▶ btc_5m_fv/ops/dashboard/app.py (FastAPI :7860)
   operator ▶ Start ─▶ btc_bot/controller.py:request_start ─(daemon thread)─▶ btc_bot/paper.py:run_paper_loop
       DISCOVER market → FEED (chainlink_settlement) → SIGNAL (btc_bot/strategy.py)
       → RISK (btc_5m_fv/execution/gate.py:RiskGate) → EXECUTE (paper sim | live.py) → STORE (db.py)
   DASHBOARD reads DB read-only via btc_5m_fv/ops/dashboard/ems.py → 8 panels/
```

## Dual-fork warnings (same logic in both trees — change the LIVE one)

- `sigma_per_second` / `fair_up_probability` / `signal_from_edge` exist in BOTH `btc_bot/strategy.py` (live) and `btc_5m_fv/strategy/*` (backtest/tests).
- `RiskGate` (`btc_5m_fv/execution/gate.py`, LIVE) vs `RiskService` (`btc_5m_fv/execution/risk.py`, DEAD).

## Live trading is built and gated

Live exists (`execution/live.py`). It activates ONLY with `BTC_BOT_MODE=live` AND `BTC_LIVE_CONFIRM=YES_I_UNDERSTAND` AND a private key AND a coherent wallet AND a clean config parse. **Agents never flip the gate. The operator launches.**

## Module status (generated)

<!-- BEGIN GENERATED:summary -->
<!-- END GENERATED:summary -->

<!-- BEGIN GENERATED:inventory -->
<!-- END GENERATED:inventory -->

See `docs/FILE_MAP.md` for the full generated index.
````

- [ ] **Step 2: Fill the generated blocks**

Run: `./.venv/bin/python tools/gen_docs.py`
Expected: the two `GENERATED:summary` / `GENERATED:inventory` blocks in `docs/CODE_MAP.md` are now populated; rerun → no diff.

- [ ] **Step 3: Verify idempotence**

Run: `./.venv/bin/python tools/gen_docs.py && git diff --quiet -- docs/CODE_MAP.md && echo OK`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add docs/CODE_MAP.md
git commit -m "docs: add CODE_MAP keystone routing doc with generated blocks (refs #<id>)"
```

---

## Task 7: Rework AGENTS.md + add CLAUDE.md pointer

**Files:**
- Modify: `AGENTS.md`
- Create: `CLAUDE.md`

- [ ] **Step 1: Rewrite AGENTS.md**

Replace the file with: a `## START HERE` line pointing to `docs/CODE_MAP.md`; keep the load-bearing Absolute Rules (BTC 5m only, one position, 4.5pp edge floor, 60s cutoff, $1–$5 sizing, local bind, no key exposure, no silent failures); **reword** the live rule to: *"Live trading is BUILT and multi-gated (`BTC_BOT_MODE=live` + `BTC_LIVE_CONFIRM=YES_I_UNDERSTAND` + key + wallet). Never flip the gate or place orders — the operator launches."*; fix `gradio.Blocks()` → `FastAPI (uvicorn) dashboard at btc_5m_fv/ops/dashboard/app.py, Gradio dashboard.py is a dead fallback`; absorb PRD's MECE scope fence; add the generated block:

```markdown
## Live module status (generated)

<!-- BEGIN GENERATED:summary -->
<!-- END GENERATED:summary -->
```

- [ ] **Step 2: Fill block + verify**

Run: `./.venv/bin/python tools/gen_docs.py && git diff --quiet -- AGENTS.md && echo OK || ./.venv/bin/python tools/gen_docs.py`
Expected: block filled; second run clean.

- [ ] **Step 3: Create CLAUDE.md pointer**

```markdown
# CLAUDE.md

This project's agent instructions live in **[AGENTS.md](AGENTS.md)** and the routing
map in **[docs/CODE_MAP.md](docs/CODE_MAP.md)**. Read those first.

The machine-generated module status is kept fresh by `tools/gen_docs.py`
(CI `docs-drift` job + `.claude` hooks). Do not hand-edit `docs/FILE_MAP.md` or any
`<!-- GENERATED -->` block.
```

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md CLAUDE.md
git commit -m "docs: rework AGENTS constitution (live-gated reword, FastAPI, PRD scope) + CLAUDE pointer (refs #<id>)"
```

---

## Task 8: Fix the two real-money safety bugs

**Files:**
- Modify: `README.md`
- Modify: `docs/OPERATIONS_RUNBOOK.md`

- [ ] **Step 1: Fix README live-orders claim**

In `README.md`, replace the line `No live orders are placed. No private key required.` (and the "Safety Boundaries" claim that live is "intentionally absent") with:

```markdown
Live trading is **built** (`btc_5m_fv/execution/live.py`) but **off by default and multi-gated**:
it runs only with `BTC_BOT_MODE=live` AND `BTC_LIVE_CONFIRM=YES_I_UNDERSTAND` AND a private key
AND a coherent wallet AND a clean config parse. In the default paper mode no orders are placed and
no key is required. Agents must never flip the gate; the operator launches live.
```
Also fix `321 tests` → remove the hardcoded number (point to `docs/FILE_MAP.md` / CI), and fix the CLI block `python -m btc_5m_fv.tools.*` → the real repo-root `tools/` scripts.

- [ ] **Step 2: Fix RUNBOOK go-live**

In `docs/OPERATIONS_RUNBOOK.md` go-live section, ensure the env step sets **both**:

```bash
export BTC_BOT_MODE=live
export BTC_LIVE_CONFIRM=YES_I_UNDERSTAND
```
Add a callout: *"Setting only `BTC_LIVE_CONFIRM` leaves the bot paper-trading while the UI reads armed — `BTC_BOT_MODE=live` is required (`btc_bot/controller.py`, `btc_bot/paper.py`)."* Update the stale button label `Start BTC Paper Bot` → `▶ Start`.

- [ ] **Step 3: Verify no banned strings remain**

Run: `grep -rn "No live orders\|no private key required\|321 tests\|intentionally absent" README.md docs/ AGENTS.md`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/OPERATIONS_RUNBOOK.md
git commit -m "docs: fix real-money safety bugs (live-gating in README, dual env var in runbook) (refs #<id>)"
```

---

## Task 9: Light fixes + deprecation stubs

**Files:**
- Modify: `docs/BACKTESTING.md`, `docs/CHRONOS_INTEGRATION.md`, `docs/RESEARCH_LOOP.md`, `docs/ROADMAP.md`, `docs/ARCHITECTURE.md`, `PRD.md`

- [ ] **Step 1: BACKTESTING** — change "full-market harness is future work" to note `btc_5m_fv/backtest/harness.py` exists but is unwired on the live path (live backtest uses `btc_bot/backtest.py`; dashboard reads `data/backtests/latest.json`).

- [ ] **Step 2: CHRONOS_INTEGRATION** — remove the fictional `BTC_USE_CHRONOS_ENSEMBLE` env flag and the fictional `python -m btc_bot.chronos_signal --activate` CLI; correct `ChronosEnsemble.apply` → `apply_ensemble`. Keep the "what's NOT done" section.

- [ ] **Step 3: RESEARCH_LOOP** — keep the "Designed, not built" flag; add a line citing `btc_bot/params_propose.py` + `btc_bot/params_apply.py` as the shipped human-gated v0.

- [ ] **Step 4: ROADMAP** — delete items describing already-shipped modules (recorder, replay, harness, telemetry, incidents, live executor, CI). Keep only genuine future work.

- [ ] **Step 5: ARCHITECTURE + PRD deprecation stubs** — replace each body with:

```markdown
# (Deprecated)

This document was superseded on 2026-06-16. The accurate architecture/routing lives in
**[docs/CODE_MAP.md](CODE_MAP.md)**; agent rules + scope live in **[AGENTS.md](../AGENTS.md)**.
Kept as a stub to preserve inbound links.
```
(PRD stub path: `[AGENTS.md](AGENTS.md)`, `[docs/CODE_MAP.md](docs/CODE_MAP.md)`.)

- [ ] **Step 6: Verify + commit**

Run: `grep -rn "BTC_USE_CHRONOS_ENSEMBLE\|chronos_signal --activate" docs/`
Expected: no matches.
```bash
git add docs/BACKTESTING.md docs/CHRONOS_INTEGRATION.md docs/RESEARCH_LOOP.md docs/ROADMAP.md docs/ARCHITECTURE.md PRD.md
git commit -m "docs: light factual fixes + deprecate ARCHITECTURE/PRD into CODE_MAP/AGENTS (refs #<id>)"
```

---

## Task 10: CI docs-drift gate

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Read current CI**

Run: `cat .github/workflows/ci.yml`
Note the Python version, install step, and the existing lint scope (`btc_5m_fv/ tests/`).

- [ ] **Step 2: Add the docs-drift job** (mirror the existing job's setup/install steps):

```yaml
  docs-drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[test]"
      - name: Regenerate docs and fail on drift
        run: python tools/gen_docs.py --check
      - name: Fail on banned stale strings
        run: |
          ! grep -rn -e "No live orders" -e "no private key required" \
            -e "321 tests" -e "gradio.Blocks" -e "live trading is intentionally absent" \
            README.md AGENTS.md CLAUDE.md docs/ btc_bot/ btc_5m_fv/
```

- [ ] **Step 3: Extend lint to btc_bot** — if a lint/ruff step scopes `btc_5m_fv/ tests/`, add `btc_bot/`.

- [ ] **Step 4: Validate YAML locally**

Run: `./.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('YAML OK')"`
Expected: `YAML OK`.

- [ ] **Step 5: Dry-run the gate locally**

Run: `./.venv/bin/python tools/gen_docs.py --check && echo "GATE PASS"`
Expected: `GATE PASS` on a freshly generated tree.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add docs-drift gate (regenerate + diff + banned-string grep over both trees) (refs #<id>)"
```

---

## Task 11: .claude hooks (self-heal + session orientation)

**Files:**
- Create: `.claude/settings.json`

- [ ] **Step 1: Write the hooks file**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "./.venv/bin/python tools/gen_docs.py --print-summary 2>/dev/null || python3 tools/gen_docs.py --print-summary" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command", "command": "case \"$CLAUDE_TOOL_FILE_PATH\" in *config.py|*db.py|*__init__.py|*.py) ./.venv/bin/python tools/gen_docs.py --fast >/dev/null 2>&1 || true ;; esac" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "./.venv/bin/python tools/gen_docs.py --check 1>&2 || true" }
        ]
      }
    ]
  }
}
```
(Adjust the env-var name for the edited path to this Claude Code version's hook contract if `CLAUDE_TOOL_FILE_PATH` is unset — confirm via the `/hooks` docs; if unavailable, drop the `case` guard and always run `--fast`, which is still fast since it skips pytest collection.)

- [ ] **Step 2: Validate JSON**

Run: `./.venv/bin/python -c "import json; json.load(open('.claude/settings.json')); print('JSON OK')"`
Expected: `JSON OK`.

- [ ] **Step 3: Smoke-test the summary command**

Run: `./.venv/bin/python tools/gen_docs.py --print-summary`
Expected: prints the 4-bullet orientation summary (trees / entry / tests / dead-list).

- [ ] **Step 4: Confirm settings.local.json (permissions) is untouched**

Run: `cat .claude/settings.local.json`
Expected: still the permissions-only file; hooks live separately in `settings.json`.

- [ ] **Step 5: Commit**

```bash
git add .claude/settings.json
git commit -m "chore(claude): hooks — SessionStart inject map, PostToolUse regen, Stop drift-check (refs #<id>)"
```

---

## Task 12: Full verification + push

**Files:** none

- [ ] **Step 1: Full test suite green**

Run: `./.venv/bin/python -m pytest tests/ -q`
Expected: all pass (no runtime code was touched; `test_gen_docs.py` added).

- [ ] **Step 2: Generation is a no-op on the committed tree**

Run: `./.venv/bin/python tools/gen_docs.py && git status --porcelain`
Expected: empty output (docs already match source).

- [ ] **Step 3: Drift gate passes; banned-string grep clean**

Run: `./.venv/bin/python tools/gen_docs.py --check && grep -rn -e "No live orders" -e "321 tests" -e "intentionally absent" README.md AGENTS.md docs/ ; echo "exit=$?"`
Expected: `--check` returns 0; grep finds nothing.

- [ ] **Step 4: Negative test — drift is actually caught**

Run: `printf '\nstray 321 tests line\n' >> README.md && ! ./.venv/bin/python -c "import subprocess,sys; sys.exit(0 if subprocess.run(['grep','-rn','321 tests','README.md']).returncode==0 else 1)" ; git checkout README.md`
Expected: confirms the banned string would be caught, then reverts. (Or simply: add a stale string, run the CI grep, see it fail, revert.)

- [ ] **Step 5: Push and open PR to develop**

```bash
git push -u origin feature/<id>-self-updating-agent-docs
gh pr create --base develop --title "Self-updating AI-agent documentation system (closes #<id>)" \
  --body "Generated FILE_MAP + hand-written CODE_MAP routing table; fixed two real-money safety-doc bugs; CI docs-drift gate + .claude hooks. See tasks/2026-06-16-self-updating-agent-docs-design.md."
```
Expected: PR opened against `develop`. **Do not merge to main** — Zayan reviews on develop.

- [ ] **Step 6: Update the design spec status**

Set the spec's `Status:` to `Implemented (PR #<pr>)` and commit.

---

## Self-Review (completed during authoring)

**Spec coverage:** §4 facts → Tasks 2/6 (wired/dead test + CODE_MAP); §5 doc set → Tasks 5–9; §5.2 generator → Tasks 1–4; §5.3 CI → Task 10; §5.4 hooks → Task 11; §6 safety fixes → Task 8; §7 build order → Task ordering; §8 verification → Task 12; §11 workflow → Tasks 0/12. No gaps.

**Placeholder scan:** `<id>` is the GitHub issue number, resolved in Task 0 — the only intentional template. No TBD/TODO content steps.

**Type consistency:** `Module.status` returns `"WIRED"`/`"DEAD?"` consistently (asserted in tests, grepped in Task 5, rendered in `_table`). `replace_block(doc, name, body)`, `render_summary(root, with_test_count)`, `collect_modules`/`annotate_importers` signatures match across tasks. Generated marker names `summary`/`inventory` are consistent between Tasks 4/6/7.
