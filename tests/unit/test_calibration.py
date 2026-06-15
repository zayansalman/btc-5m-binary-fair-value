"""Unit tests for the side-relative probability calibrator (#37)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from btc_bot.calibration import (
    IdentityCalibrator,
    IsotonicCalibrator,
    _pav,
    apply_to_pair,
    load,
    save,
)


def test_pav_already_monotonic_returns_pointwise() -> None:
    block_x, block_y = _pav([0.1, 0.4, 0.7, 0.9], [0.0, 0.2, 0.8, 1.0])
    assert block_x == [0.1, 0.4, 0.7, 0.9]
    assert block_y == [0.0, 0.2, 0.8, 1.0]


def test_pav_pools_violators() -> None:
    block_x, block_y = _pav([0.1, 0.4, 0.7], [1.0, 0.0, 0.5])
    assert block_x == [0.7]
    assert block_y == [pytest.approx(0.5)]


def test_pav_pools_partial_violators() -> None:
    block_x, block_y = _pav([0.1, 0.2, 0.5, 0.8], [0.0, 0.3, 0.1, 1.0])
    assert block_x == [0.1, 0.5, 0.8]
    assert block_y == [pytest.approx(0.0), pytest.approx(0.2), pytest.approx(1.0)]


def test_isotonic_transform_returns_block_means() -> None:
    cal = IsotonicCalibrator(block_x=[0.3, 0.6, 0.9], block_y=[0.1, 0.5, 0.95])
    assert cal.transform(0.0) == pytest.approx(0.1)
    assert cal.transform(0.3) == pytest.approx(0.1)
    assert cal.transform(0.31) == pytest.approx(0.5)
    assert cal.transform(0.6) == pytest.approx(0.5)
    assert cal.transform(0.61) == pytest.approx(0.95)
    assert cal.transform(0.9) == pytest.approx(0.95)
    assert cal.transform(1.0) == pytest.approx(0.95)


def test_transform_is_monotonic_on_dense_grid() -> None:
    probs = [i / 100 for i in range(101)]
    outcomes = [1.0 if p > 0.55 else 0.0 for p in probs]
    cal = IsotonicCalibrator.fit(probs, outcomes)
    prev = -1.0
    for p in probs:
        v = cal.transform(p)
        assert v >= prev - 1e-9
        prev = v


def test_brier_does_not_get_worse_after_fit() -> None:
    probs = [0.1, 0.4, 0.4, 0.7, 0.9, 0.9]
    outcomes = [0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
    cal = IsotonicCalibrator.fit(probs, outcomes)
    assert cal.brier_raw is not None and cal.brier_cal is not None
    assert cal.brier_cal <= cal.brier_raw + 1e-9


def test_identity_calibrator_passes_through() -> None:
    ident = IdentityCalibrator()
    for p in [0.0, 0.25, 0.5, 0.75, 1.0]:
        assert ident.transform(p) == p


def test_apply_to_pair_renormalises_to_one() -> None:
    cal = IsotonicCalibrator(block_x=[0.3, 0.7], block_y=[0.1, 0.8])
    p_up, p_down = apply_to_pair(cal, 0.6)
    assert p_up + p_down == pytest.approx(1.0)
    assert 0.0 <= p_up <= 1.0 and 0.0 <= p_down <= 1.0


def test_apply_to_pair_identity_returns_input_and_complement() -> None:
    p_up, p_down = apply_to_pair(IdentityCalibrator(), 0.63)
    assert p_up == pytest.approx(0.63)
    assert p_down == pytest.approx(0.37)


def test_json_roundtrip(tmp_path: Path) -> None:
    cal = IsotonicCalibrator.fit(
        [0.1, 0.4, 0.7, 0.9],
        [0.0, 0.3, 0.8, 1.0],
        fit_at="2026-06-15T00:00:00+00:00",
        meta={"style": "settle"},
    )
    p = tmp_path / "calibration.json"
    save(cal, p)

    loaded = load(p)
    assert isinstance(loaded, IsotonicCalibrator)
    for x in [0.05, 0.1, 0.5, 0.9, 0.95]:
        assert loaded.transform(x) == pytest.approx(cal.transform(x))
    assert loaded.n_samples == cal.n_samples
    assert loaded.fit_at == cal.fit_at


def test_missing_file_returns_identity(tmp_path: Path) -> None:
    loaded = load(tmp_path / "nope.json")
    assert isinstance(loaded, IdentityCalibrator)


def test_unknown_kind_returns_identity(tmp_path: Path) -> None:
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({"kind": "platt_v9000", "a": 1, "b": 0}))
    loaded = load(p)
    assert isinstance(loaded, IdentityCalibrator)


def test_corrupt_json_returns_identity(tmp_path: Path) -> None:
    p = tmp_path / "calibration.json"
    p.write_text("{not json")
    loaded = load(p)
    assert isinstance(loaded, IdentityCalibrator)


def test_fit_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        IsotonicCalibrator.fit([0.1, 0.2], [1.0])
