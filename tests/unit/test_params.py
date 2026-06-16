"""Unit tests for Layer 2 strategy-params proposer / apply path (#37)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from btc_bot import params as p


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def test_load_active_falls_back_to_env_when_missing(isolated_data_dir: Path) -> None:
    a = p.load_active()
    assert a.source == "env"
    assert a.entry_edge_min > 0


def test_load_proposed_returns_none_when_missing(isolated_data_dir: Path) -> None:
    assert p.load_proposed() is None


def test_save_and_load_proposed_roundtrip(isolated_data_dir: Path) -> None:
    pr = p.ActiveParams(
        entry_edge_min=0.06,
        entry_edge_max=0.08,
        min_confidence=0.62,
        min_remaining_seconds=120,
        max_entry_price=0.85,
        min_entry_price=0.40,
        source="proposed",
        proposed_at="2026-06-15T07:00:00+00:00",
        backtest_meta={"recommended_pnl": 12.34},
    )
    path = p.save_proposed(pr)
    assert path.exists()
    loaded = p.load_proposed()
    assert loaded is not None
    assert loaded.entry_edge_min == pytest.approx(0.06)
    assert loaded.min_confidence == pytest.approx(0.62)
    assert loaded.backtest_meta == {"recommended_pnl": 12.34}


def test_save_and_load_active_marks_source_applied(isolated_data_dir: Path) -> None:
    pr = p.ActiveParams(
        entry_edge_min=0.05,
        entry_edge_max=0.07,
        min_confidence=0.55,
        min_remaining_seconds=90,
        max_entry_price=0.90,
        min_entry_price=0.50,
        source="applied",
        applied_at="2026-06-15T07:00:00+00:00",
    )
    p.save_active(pr)
    loaded = p.load_active()
    assert loaded.source == "applied"
    assert loaded.entry_edge_min == pytest.approx(0.05)


def test_corrupt_active_file_falls_back_to_env(isolated_data_dir: Path) -> None:
    (isolated_data_dir / p.ACTIVE_FILE).write_text("{not json")
    a = p.load_active()
    assert a.source == "env"


def test_active_file_with_missing_keys_uses_env_for_those(
    isolated_data_dir: Path,
) -> None:
    (isolated_data_dir / p.ACTIVE_FILE).write_text(
        json.dumps({"entry_edge_min": 0.123})
    )
    a = p.load_active()
    assert a.entry_edge_min == pytest.approx(0.123)
    # the rest fall back, including non-default ones
    assert a.min_confidence > 0
