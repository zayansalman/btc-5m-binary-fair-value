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
        for tgt in _imported_targets(src.read_text()):
            mod = known.get(tgt)
            if mod is not None and mod.path != rel:
                mod.importers += 1
