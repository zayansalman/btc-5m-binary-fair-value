"""Unit tests for the Chainlink-vs-Binance lead-lag tool (issue #57).

Synthetic frames with a known offset — asserts the stat functions recover
the planted lag / gap / agreement rate. The HF loader is NOT exercised
here; it's hit by running the script.
"""
from __future__ import annotations

import polars as pl

from tools.chainlink_lead_lag import (
    next_print_predictability,
    reaction_lag,
    static_gap_bps,
)


def _paired(prices_per_second: list[tuple[int, float, float]]) -> pl.DataFrame:
    """Build a paired DataFrame from (ts_s, chainlink, binance) tuples."""
    return pl.DataFrame(
        [{"ts_s": t, "chainlink": c, "binance": b} for t, c, b in prices_per_second]
    )


def test_static_gap_zero_when_prices_match() -> None:
    """Two feeds always at the same price → all gaps are zero."""
    df = _paired([(i, 60_000.0, 60_000.0) for i in range(10)])
    g = static_gap_bps(df)
    assert g["n"] == 10
    assert g["p50"] == 0.0
    assert g["p99"] == 0.0


def test_static_gap_recovers_known_bps() -> None:
    """Constant +6 bps gap: every row should report 6 bps."""
    df = _paired([(i, 60_036.0, 60_000.0) for i in range(20)])  # 6 bps over
    g = static_gap_bps(df)
    assert abs(g["p50"] - 6.0) < 0.01
    assert abs(g["p99"] - 6.0) < 0.01


def test_next_print_predictability_monotonic_trend() -> None:
    """Monotonically rising series: every 1-second Chainlink delta is +, and
    every multi-second Binance delta is + too → 100% directional agreement."""
    df = _paired([(t, 60_000.0 + t * 5, 60_000.0 + t * 5) for t in range(20)])
    p = next_print_predictability(df, lookback_s=5)
    assert p["n_pairs"] > 0
    assert p["hit_rate"] == 1.0


def test_next_print_predictability_anticorrelated() -> None:
    """If Chainlink rises while Binance falls over the lookback → 0% agreement."""
    df = _paired([(t, 60_000.0 + t * 5, 60_000.0 - t * 5) for t in range(20)])
    p = next_print_predictability(df, lookback_s=5)
    assert p["n_pairs"] > 0
    assert p["hit_rate"] == 0.0


def test_next_print_predictability_perfect_lag() -> None:
    """Chainlink mirrors Binance with a 1-second delay → near-perfect hits."""
    # Build a rising series; chainlink lags binance by exactly 1 second.
    bn = [60_000.0 + i * 5 for i in range(20)]
    cl = [60_000.0] + bn[:-1]  # 1-step lag
    df = _paired([(i, cl[i], bn[i]) for i in range(20)])
    p = next_print_predictability(df, lookback_s=3)
    assert p["hit_rate"] >= 0.95


def test_reaction_lag_recovers_planted_delay() -> None:
    """A 10-second Chainlink delay on a single Binance jump → median lag ≈ 10s."""
    # Binance jumps +20 bps at t=10; Chainlink catches up at t=20.
    rows: list[tuple[int, float, float]] = []
    for t in range(30):
        bn = 60_000.0 if t < 10 else 60_120.0  # +20 bps
        if t < 20:
            cl = 60_000.0
        else:
            cl = 60_060.0  # catches half (+10 bps)
        rows.append((t, cl, bn))
    df = _paired(rows)
    r = reaction_lag(df, move_threshold_bps=10.0, move_window_s=5)
    assert r["n_matched"] >= 1
    assert r["p50_lag_s"] is not None
    assert 5 <= r["p50_lag_s"] <= 15


def test_reaction_lag_counts_misses() -> None:
    """If Chainlink never moves after Binance jumps, all triggers miss."""
    rows = [(t, 60_000.0, 60_000.0) for t in range(10)] + [
        (t, 60_000.0, 60_120.0) for t in range(10, 80)
    ]
    df = _paired(rows)
    r = reaction_lag(df, move_threshold_bps=10.0, move_window_s=5)
    assert r["n_triggers"] > 0
    assert r["miss_rate"] == 1.0
    assert r["n_matched"] == 0
