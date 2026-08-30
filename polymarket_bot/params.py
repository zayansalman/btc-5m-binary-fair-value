"""Active strategy parameters (Layer 2 — operator-gated auto-tune).

The live bot's filter thresholds are ENV-defaults overridable by an
operator-applied file. Flow:

* ``params_propose`` (CLI) runs the existing backtest grid, writes the
  recommended params to ``$DATA_DIR/params_proposed.json`` — never live.
* The dashboard surfaces the proposed set vs the active set with backtest
  delta so the operator can compare.
* ``params_apply`` (CLI, operator-run) promotes proposed -> active by writing
  ``$DATA_DIR/params_active.json``. Atomic write.
* The live bot calls ``load_active()`` once per window roll. If the file is
  absent or unreadable, it falls back to the env defaults — fully no-op.

This preserves the project policy: AI proposes, human disposes; no live
parameter change is ever applied without explicit operator action.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config as _config


@dataclass(frozen=True)
class ActiveParams:
    """Tunable strategy parameters. None means "use env default"."""

    entry_edge_min: float
    entry_edge_max: float
    min_confidence: float
    min_remaining_seconds: int
    max_entry_price: float
    min_entry_price: float
    source: str = "env"
    proposed_at: str = ""
    applied_at: str = ""
    backtest_meta: dict = field(default_factory=dict)


def _default_path(filename: str) -> Path:
    data_dir = os.environ.get("DATA_DIR", "./data")
    return Path(data_dir) / filename


ACTIVE_FILE = "params_active.json"
PROPOSED_FILE = "params_proposed.json"


def _from_env() -> ActiveParams:
    return ActiveParams(
        entry_edge_min=_config.BTC_PAPER_ENTRY_EDGE_MIN,
        entry_edge_max=_config.BTC_PAPER_ENTRY_EDGE_MAX,
        min_confidence=_config.BTC_PAPER_MIN_CONFIDENCE,
        min_remaining_seconds=_config.BTC_PAPER_ENTRY_MIN_REMAINING_SECONDS,
        max_entry_price=0.95,
        min_entry_price=_config.BTC_PAPER_MIN_ENTRY_PRICE,
        source="env",
    )


def load_active() -> ActiveParams:
    """Return the active params. Env default when no applied file exists."""
    p = _default_path(ACTIVE_FILE)
    try:
        with open(p) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _from_env()
    env = _from_env()
    return ActiveParams(
        entry_edge_min=float(d.get("entry_edge_min", env.entry_edge_min)),
        entry_edge_max=float(d.get("entry_edge_max", env.entry_edge_max)),
        min_confidence=float(d.get("min_confidence", env.min_confidence)),
        min_remaining_seconds=int(
            d.get("min_remaining_seconds", env.min_remaining_seconds)
        ),
        max_entry_price=float(d.get("max_entry_price", env.max_entry_price)),
        min_entry_price=float(d.get("min_entry_price", env.min_entry_price)),
        source=str(d.get("source", "applied")),
        proposed_at=str(d.get("proposed_at", "")),
        applied_at=str(d.get("applied_at", "")),
        backtest_meta=dict(d.get("backtest_meta") or {}),
    )


def load_proposed() -> ActiveParams | None:
    """Return the pending operator-review proposal, or None if absent."""
    p = _default_path(PROPOSED_FILE)
    try:
        with open(p) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    env = _from_env()
    return ActiveParams(
        entry_edge_min=float(d.get("entry_edge_min", env.entry_edge_min)),
        entry_edge_max=float(d.get("entry_edge_max", env.entry_edge_max)),
        min_confidence=float(d.get("min_confidence", env.min_confidence)),
        min_remaining_seconds=int(
            d.get("min_remaining_seconds", env.min_remaining_seconds)
        ),
        max_entry_price=float(d.get("max_entry_price", env.max_entry_price)),
        min_entry_price=float(d.get("min_entry_price", env.min_entry_price)),
        source="proposed",
        proposed_at=str(d.get("proposed_at", "")),
        backtest_meta=dict(d.get("backtest_meta") or {}),
    )


def save_proposed(params: ActiveParams) -> Path:
    return _atomic_write(_default_path(PROPOSED_FILE), asdict(params))


def save_active(params: ActiveParams) -> Path:
    return _atomic_write(_default_path(ACTIVE_FILE), asdict(params))


def _atomic_write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path
