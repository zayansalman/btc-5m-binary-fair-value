"""Maker-mode backtest: fill simulation + per-opportunity EV accounting (#130).

The realism rules under test (see docs/superpowers/specs/2026-06-24-maker-strategy-design.md):
forward-only fills, a conservative mid-cross fill rule, a settlement cutoff, and a
per-OPPORTUNITY EV (unfilled = $0) with adverse-selection accounting.
"""

from __future__ import annotations

import pytest

from tools.maker_backtest import compute_limit, run_backtest, simulate_fill


class TestComputeLimit:
    _OPP = {"signal_bid": 0.40, "signal_ask": 0.50, "fair_prob": 0.46}

    def test_join_bid(self) -> None:
        assert compute_limit("join_bid", self._OPP) == 0.40

    def test_mid(self) -> None:
        assert compute_limit("mid", self._OPP) == pytest.approx(0.45)

    def test_fair(self) -> None:
        assert compute_limit("fair", self._OPP) == 0.46


class TestSimulateFill:
    def test_fills_when_mid_crosses_below_limit(self) -> None:
        # buy limit 0.45; a later tick prints mid 0.44 with time left → filled.
        assert simulate_fill(0.45, [(180, 0.48), (120, 0.44)], cutoff=30) is True

    def test_no_fill_when_mid_never_reaches_limit(self) -> None:
        assert simulate_fill(0.45, [(180, 0.48), (120, 0.46)], cutoff=30) is False

    def test_no_fill_when_only_cross_is_inside_cutoff(self) -> None:
        # the only sub-limit tick is at 20s left (< 30s cutoff) → a maker wouldn't
        # be resting into settlement; no fill.
        assert simulate_fill(0.45, [(180, 0.50), (20, 0.40)], cutoff=30) is False

    def test_no_fill_on_empty_forward(self) -> None:
        assert simulate_fill(0.45, [], cutoff=30) is False

    def test_exact_touch_fills(self) -> None:
        assert simulate_fill(0.45, [(100, 0.45)], cutoff=30) is True


def _opp(**over):
    o = dict(
        window="w", side="Up", taker_ask=0.50, shares=10.0, fair_prob=0.46,
        signal_bid=0.40, signal_ask=0.50, won=True, taker_pnl=0.0,
        forward_mids=[(120, 0.44)],  # crosses 0.45 → mid policy fills
    )
    o.update(over)
    return o


class TestRunBacktest:
    def test_unfilled_counts_as_zero_in_per_opportunity(self) -> None:
        # opp A fills and wins; opp B never fills (a missed winner).
        a = _opp(window="a", won=True, forward_mids=[(120, 0.40)])
        b = _opp(window="b", won=True, forward_mids=[(120, 0.49)])  # never reaches 0.45
        r = run_backtest([a, b], policy="mid", cutoff=30, maker_fee_rate=0.07, haircut=1.0)
        assert r["n_opportunities"] == 2
        assert r["n_filled"] == 1
        assert r["fill_rate"] == pytest.approx(0.5)
        # per-opportunity expectancy divides by 2 (the unfilled B contributes $0).
        assert r["exp_per_opp_maker"] == pytest.approx(r["maker_total"] / 2)

    def test_missed_winner_is_counted(self) -> None:
        b = _opp(window="b", won=True, forward_mids=[(120, 0.49)])
        r = run_backtest([b], policy="mid", cutoff=30, maker_fee_rate=0.07, haircut=1.0)
        assert r["missed_winners_n"] == 1

    def test_adverse_selection_fill_rates_split_by_outcome(self) -> None:
        win = _opp(window="w1", won=True, forward_mids=[(120, 0.40)])    # fills
        lose = _opp(window="l1", won=False, forward_mids=[(120, 0.40)])  # fills
        nofill_win = _opp(window="w2", won=True, forward_mids=[(120, 0.49)])
        r = run_backtest([win, lose, nofill_win], policy="mid", cutoff=30,
                         maker_fee_rate=0.07, haircut=1.0)
        # winners: 2 total, 1 filled → 0.5 ; losers: 1 total, 1 filled → 1.0
        assert r["fill_rate_winners"] == pytest.approx(0.5)
        assert r["fill_rate_losers"] == pytest.approx(1.0)

    def test_zero_fee_beats_taker_fee_on_pnl(self) -> None:
        a = _opp(forward_mids=[(120, 0.40)], won=True)
        full = run_backtest([a], policy="mid", cutoff=30, maker_fee_rate=0.07, haircut=1.0)
        free = run_backtest([a], policy="mid", cutoff=30, maker_fee_rate=0.0, haircut=1.0)
        assert free["maker_total"] > full["maker_total"]

    def test_haircut_drops_fills(self) -> None:
        opps = [_opp(window=f"w{i}", forward_mids=[(120, 0.40)]) for i in range(10)]
        full = run_backtest(opps, policy="mid", cutoff=30, maker_fee_rate=0.07, haircut=1.0)
        half = run_backtest(opps, policy="mid", cutoff=30, maker_fee_rate=0.07, haircut=0.5)
        assert full["n_filled"] == 10
        assert half["n_filled"] < full["n_filled"]
