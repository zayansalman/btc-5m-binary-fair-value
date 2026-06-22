"""Unit tests for the shadow forward-tester candidate signals.

Covers the two candidate strategies in :mod:`btc_bot.shadow.signals`:

* ``cushion_favorite_v2`` — None when v0 declines, None when the cushion is
  below the floor, a signal when both pass.
* ``late_convergence_v3`` — None outside the time band, None below
  near-certain, None when book and model disagree, a signal when all pass.

The signals are pure, so every case is a hand-built
:class:`~btc_bot.shadow.types.SnapshotView` plus a small local
:class:`~btc_bot.strategy.StrategyParams`. We deliberately build our own
params (not the ``btc_5m_fv`` conftest fixture) because the candidates reuse
``btc_bot.strategy.signal_from_executable_edges``, which takes the
``btc_bot.strategy`` flavour of ``StrategyParams``.
"""

from __future__ import annotations

import pytest

from btc_bot import strategy
from btc_bot.shadow.signals import (
    cushion_drift_v5,
    cushion_favorite_v2,
    down_skeptic_drift_v6,
    down_skeptic_v4,
    late_convergence_v3,
)
from btc_bot.shadow.types import ShadowSignal, SnapshotView


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def params() -> strategy.StrategyParams:
    """Loose params so v0 fires on a modest edge in the happy-path cases."""
    return strategy.StrategyParams(
        min_trade_usd=1.0,
        max_trade_usd=5.0,
        entry_edge_min=0.05,
        min_confidence=0.55,
        entry_min_remaining_seconds=60,
        max_entry_price=0.95,
        min_entry_price=0.05,
        entry_edge_max=1.0,
    )


def _view(**overrides: object) -> SnapshotView:
    """Build a SnapshotView from sane defaults overridden per test."""
    base: dict[str, object] = dict(
        window_slug="btc-5m-2026-06-18T16:00",
        remaining_seconds=120,
        spot=50000.0,
        reference=50000.0,
        up_ask=0.55,
        down_ask=0.46,
        market_up_price=0.55,
        fair_up=0.70,
        sigma_per_second=0.0003,
        feed_source="chainlink",
        quote_source="clob",
    )
    base.update(overrides)
    return SnapshotView(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# cushion_favorite_v2
# ---------------------------------------------------------------------------


class TestCushionFavoriteV2:
    def test_none_when_v0_declines(self, params: strategy.StrategyParams) -> None:
        """No executable quotes -> v0 returns side None -> wrapper returns None."""
        view = _view(up_ask=None, down_ask=None, reference=49980.0)
        assert cushion_favorite_v2(view, params) is None

    def test_none_when_v0_declines_thin_edge(self, params: strategy.StrategyParams) -> None:
        """Edge below v0's threshold -> v0 declines even with a big cushion."""
        # fair_up 0.70 vs up_ask 0.69 -> edge 0.01 < entry_edge_min 0.05.
        view = _view(up_ask=0.69, down_ask=0.60, fair_up=0.70, reference=49900.0)
        assert cushion_favorite_v2(view, params) is None

    def test_none_when_cushion_below_threshold(
        self, params: strategy.StrategyParams
    ) -> None:
        """v0 wants Up, but spot leads reference by only ~0.2 bps -> None."""
        # edge_up = 0.70 - 0.55 = 0.15 (v0 enters Up), but cushion is tiny.
        view = _view(spot=50000.0, reference=49999.0, up_ask=0.55, fair_up=0.70)
        cushion_bps = (50000.0 - 49999.0) / 50000.0 * 1e4
        assert cushion_bps < 1.5  # sanity: this is the gate we are exercising
        assert cushion_favorite_v2(view, params) is None

    def test_signal_when_both_pass_up(self, params: strategy.StrategyParams) -> None:
        """v0 enters Up and the cushion clears the floor -> Up signal."""
        view = _view(spot=50000.0, reference=49980.0, up_ask=0.55, fair_up=0.70)
        sig = cushion_favorite_v2(view, params)
        assert isinstance(sig, ShadowSignal)
        assert sig.side == "Up"
        assert sig.entry_price == pytest.approx(0.55)
        assert sig.fair_prob == pytest.approx(0.70)
        assert sig.edge == pytest.approx(0.70 - 0.55)
        # cushion = (50000 - 49980) / 50000 * 1e4 = 4.0 bps
        assert sig.reason.startswith("cushion 4.0bps;")

    def test_signal_when_both_pass_down(self, params: strategy.StrategyParams) -> None:
        """v0 enters Down (spot below reference) and the cushion clears -> Down."""
        # fair_up 0.30 -> fair_down 0.70 vs down_ask 0.55 -> edge_down 0.15.
        view = _view(
            spot=50000.0,
            reference=50020.0,
            up_ask=0.72,
            down_ask=0.55,
            market_up_price=0.30,
            fair_up=0.30,
        )
        sig = cushion_favorite_v2(view, params)
        assert isinstance(sig, ShadowSignal)
        assert sig.side == "Down"
        assert sig.entry_price == pytest.approx(0.55)
        assert sig.fair_prob == pytest.approx(0.70)
        assert sig.edge == pytest.approx(0.70 - 0.55)
        # cushion = (50020 - 50000) / 50000 * 1e4 = 4.0 bps
        assert sig.reason.startswith("cushion 4.0bps;")

    def test_cushion_uses_correct_side_sign(
        self, params: strategy.StrategyParams
    ) -> None:
        """A long spot lead is the WRONG sign for a Down pick -> None.

        v0 enters Down, but spot is far ABOVE reference, so the favourable
        gap (reference - spot) is negative and cannot clear the floor.
        """
        view = _view(
            spot=50050.0,
            reference=50000.0,
            up_ask=0.72,
            down_ask=0.55,
            market_up_price=0.30,
            fair_up=0.30,
        )
        assert cushion_favorite_v2(view, params) is None


# ---------------------------------------------------------------------------
# drift_per_second  (directional twin of sigma_per_second)
# ---------------------------------------------------------------------------


class TestDriftPerSecond:
    def test_insufficient_data_returns_zero(self) -> None:
        """Fewer than 2 valid returns -> no drift estimate -> 0.0."""
        assert strategy.drift_per_second([]) == pytest.approx(0.0)
        assert strategy.drift_per_second([50000.0]) == pytest.approx(0.0)

    def test_flat_prices_zero_drift(self) -> None:
        """Constant prices have zero log-returns -> zero mean drift."""
        assert strategy.drift_per_second([50000.0] * 10) == pytest.approx(0.0)

    def test_monotone_up_positive_drift(self) -> None:
        """A rising series has positive mean log-return."""
        assert strategy.drift_per_second([100.0, 101.0, 102.0, 103.0]) > 0.0

    def test_monotone_down_negative_drift(self) -> None:
        """A falling series has negative mean log-return."""
        assert strategy.drift_per_second([103.0, 102.0, 101.0, 100.0]) < 0.0

    def test_value_is_mean_log_return(self) -> None:
        """Drift equals the arithmetic mean of consecutive 1s log-returns."""
        import math

        closes = [100.0, 102.0, 101.0]
        expected = (math.log(102.0 / 100.0) + math.log(101.0 / 102.0)) / 2
        assert strategy.drift_per_second(closes) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# cushion_drift_v5  (regime-adaptive asymmetric cushion)
# ---------------------------------------------------------------------------


class TestCushionDriftV5:
    def test_neutral_regime_equals_v2(self, params: strategy.StrategyParams) -> None:
        """drift=0 -> regime 0 -> identical decision to cushion_favorite_v2."""
        view = _view(
            spot=50000.0, reference=49980.0, up_ask=0.55, fair_up=0.70,
            sigma_per_second=0.0003, drift_per_second=0.0,
        )
        v5 = cushion_drift_v5(view, params)
        v2 = cushion_favorite_v2(view, params)
        assert isinstance(v5, ShadowSignal) and isinstance(v2, ShadowSignal)
        assert (v5.side, v5.entry_price, v5.edge) == (v2.side, v2.entry_price, v2.edge)

    def test_drift_none_equals_v2(self, params: strategy.StrategyParams) -> None:
        """Missing drift feed -> regime 0 -> behaves as v2 (never raises)."""
        view = _view(
            spot=50000.0, reference=49980.0, up_ask=0.55, fair_up=0.70,
            drift_per_second=None,
        )
        assert cushion_drift_v5(view, params) is not None

    def test_up_regime_vetoes_a_down_v2_took(
        self, params: strategy.StrategyParams
    ) -> None:
        """Strong bull regime raises the Down bar above a 2bps cushion v2 accepted."""
        # v0 -> Down; Down cushion = (50010-50000)/50000*1e4 = 2.0bps (> base 1.5).
        common = dict(
            spot=50000.0, reference=50010.0, up_ask=0.72, down_ask=0.55,
            market_up_price=0.30, fair_up=0.30, sigma_per_second=0.0003,
        )
        assert cushion_favorite_v2(_view(**common), params) is not None  # v2 takes it
        bull = _view(**common, drift_per_second=0.0003)  # drift==sigma -> regime +1
        assert cushion_drift_v5(bull, params) is None  # Down bar 2.5 > 2.0 -> veto

    def test_up_regime_takes_an_up_v2_skipped(
        self, params: strategy.StrategyParams
    ) -> None:
        """Strong bull regime lowers the Up bar below a 1bps cushion v2 rejected."""
        # v0 -> Up; Up cushion = (50005-50000)/50000*1e4 = 1.0bps (< base 1.5).
        common = dict(
            spot=50005.0, reference=50000.0, up_ask=0.55, fair_up=0.70,
            sigma_per_second=0.0003,
        )
        assert cushion_favorite_v2(_view(**common), params) is None  # v2 skips it
        bull = _view(**common, drift_per_second=0.0003)  # regime +1 -> Up bar 0.5
        sig = cushion_drift_v5(bull, params)
        assert isinstance(sig, ShadowSignal) and sig.side == "Up"
        assert "regime" in sig.reason

    def test_down_regime_takes_a_down_v2_skipped(
        self, params: strategy.StrategyParams
    ) -> None:
        """Strong bear regime lowers the Down bar below a 1bps cushion v2 rejected."""
        # v0 -> Down; Down cushion = (50005-50000)/50000*1e4 = 1.0bps (< base 1.5).
        common = dict(
            spot=50000.0, reference=50005.0, up_ask=0.72, down_ask=0.55,
            market_up_price=0.30, fair_up=0.30, sigma_per_second=0.0003,
        )
        assert cushion_favorite_v2(_view(**common), params) is None  # v2 skips it
        bear = _view(**common, drift_per_second=-0.0003)  # regime -1 -> Down bar 0.5
        sig = cushion_drift_v5(bear, params)
        assert isinstance(sig, ShadowSignal) and sig.side == "Down"

    def test_floor_caps_a_strong_regime(self, params: strategy.StrategyParams) -> None:
        """Even max bull regime cannot drop the Up bar below the 0.5bps floor."""
        # Up cushion = 0.4bps, below the 0.5 floor -> vetoed despite full bull regime.
        view = _view(
            spot=50002.0, reference=50000.0, up_ask=0.55, fair_up=0.70,
            sigma_per_second=0.0003, drift_per_second=0.0003,
        )
        assert (50002.0 - 50000.0) / 50000.0 * 1e4 < 0.5  # sanity: below the floor
        assert cushion_drift_v5(view, params) is None


# ---------------------------------------------------------------------------
# late_convergence_v3
# ---------------------------------------------------------------------------


class TestLateConvergenceV3:
    def test_none_before_time_band(self, params: strategy.StrategyParams) -> None:
        """remaining_seconds above late_max_s (45) -> None."""
        view = _view(remaining_seconds=46, market_up_price=0.95, fair_up=0.95)
        assert late_convergence_v3(view, params) is None

    def test_none_after_time_band(self, params: strategy.StrategyParams) -> None:
        """remaining_seconds below late_min_s -> None."""
        view = _view(remaining_seconds=3, market_up_price=0.95, fair_up=0.95)
        assert late_convergence_v3(view, params) is None

    def test_none_below_near_certain(self, params: strategy.StrategyParams) -> None:
        """In band, but neither book nor model is near-certain -> None."""
        view = _view(
            remaining_seconds=20,
            market_up_price=0.60,
            up_ask=0.60,
            fair_up=0.60,
        )
        assert late_convergence_v3(view, params) is None

    def test_none_when_book_and_model_disagree(
        self, params: strategy.StrategyParams
    ) -> None:
        """Book says near-certain Up, but the model leans Down (fair<0.5) -> None."""
        view = _view(
            remaining_seconds=20,
            market_up_price=0.95,
            up_ask=0.95,
            fair_up=0.40,  # model leans the other way -> weak-agreement gate fails
        )
        assert late_convergence_v3(view, params) is None

    def test_none_when_model_certain_book_not(
        self, params: strategy.StrategyParams
    ) -> None:
        """Model says near-certain Up, book does not -> None (symmetric guard)."""
        view = _view(
            remaining_seconds=20,
            market_up_price=0.80,  # book below near_certain for Up
            up_ask=0.80,
            fair_up=0.95,
        )
        assert late_convergence_v3(view, params) is None

    def test_none_when_no_executable_quote(
        self, params: strategy.StrategyParams
    ) -> None:
        """Favoured side has no ask -> None."""
        view = _view(
            remaining_seconds=20,
            market_up_price=0.95,
            up_ask=None,
            fair_up=0.95,
        )
        assert late_convergence_v3(view, params) is None

    def test_none_when_no_room_to_profit(
        self, params: strategy.StrategyParams
    ) -> None:
        """Ask at/above 0.99 leaves no room -> None even if near-certain."""
        view = _view(
            remaining_seconds=20,
            market_up_price=0.99,
            up_ask=0.99,
            fair_up=0.99,
        )
        assert late_convergence_v3(view, params) is None

    def test_signal_when_all_pass_up(self, params: strategy.StrategyParams) -> None:
        """In band, book and model both near-certain Up, room to profit -> Up."""
        view = _view(
            remaining_seconds=20,
            market_up_price=0.93,
            up_ask=0.93,
            fair_up=0.95,
        )
        sig = late_convergence_v3(view, params)
        assert isinstance(sig, ShadowSignal)
        assert sig.side == "Up"
        assert sig.entry_price == pytest.approx(0.93)
        assert sig.fair_prob == pytest.approx(0.95)
        assert sig.edge == pytest.approx(0.95 - 0.93)
        assert sig.confidence == pytest.approx(0.95)
        assert sig.reason == "late-convergence rs=20; book 0.93 fair 0.95"

    def test_signal_when_all_pass_down(self, params: strategy.StrategyParams) -> None:
        """Book favours Down (market_up_price < 0.5); model agrees -> Down."""
        view = _view(
            remaining_seconds=10,
            market_up_price=0.06,  # favoured price for Down = 0.94
            down_ask=0.93,
            fair_up=0.04,  # fair_down = 0.96
        )
        sig = late_convergence_v3(view, params)
        assert isinstance(sig, ShadowSignal)
        assert sig.side == "Down"
        assert sig.entry_price == pytest.approx(0.93)
        assert sig.fair_prob == pytest.approx(0.96)
        assert sig.edge == pytest.approx(0.96 - 0.93)
        assert sig.reason == "late-convergence rs=10; book 0.94 fair 0.96"

    def test_time_band_boundaries_inclusive(
        self, params: strategy.StrategyParams
    ) -> None:
        """The late band is inclusive at both ends (5 and 45 by default)."""
        edge_view = lambda rs: _view(  # noqa: E731 - terse, test-local
            remaining_seconds=rs, market_up_price=0.93, up_ask=0.93, fair_up=0.95
        )
        assert late_convergence_v3(edge_view(5), params) is not None
        assert late_convergence_v3(edge_view(45), params) is not None
        assert late_convergence_v3(edge_view(4), params) is None
        assert late_convergence_v3(edge_view(46), params) is None


# ---------------------------------------------------------------------------
# down_skeptic_v4
# ---------------------------------------------------------------------------


class TestDownSkepticV4:
    def test_none_when_v0_declines(self, params: strategy.StrategyParams) -> None:
        """No executable quotes -> v0 returns None -> wrapper returns None."""
        assert down_skeptic_v4(_view(up_ask=None, down_ask=None), params) is None

    def test_up_pick_passes_through_unchanged(
        self, params: strategy.StrategyParams
    ) -> None:
        """An Up pick is never penalised — v0's Up signal flows straight through."""
        view = _view(up_ask=0.55, down_ask=0.46, fair_up=0.70)  # edge_up 0.15
        sig = down_skeptic_v4(view, params)
        assert isinstance(sig, ShadowSignal)
        assert sig.side == "Up"
        assert sig.edge == pytest.approx(0.15)
        assert "down-skeptic" not in sig.reason

    def test_marginal_down_pick_vetoed(
        self, params: strategy.StrategyParams
    ) -> None:
        """v0 enters Down at edge 0.06, below the 0.05+0.02 bar -> vetoed."""
        # 1-fair_up = 0.58 vs down_ask 0.52 -> edge_down 0.06; up edge negative.
        view = _view(fair_up=0.42, up_ask=0.50, down_ask=0.52)
        # sanity: v0 itself takes this Down (edge 0.06 >= entry_edge_min 0.05)
        side, *_ = strategy.signal_from_executable_edges(
            0.42 - 0.50, (1 - 0.42) - 0.52, 120, 0.50, 0.52, params
        )
        assert side == "Down"
        assert down_skeptic_v4(view, params) is None

    def test_strong_down_pick_kept(self, params: strategy.StrategyParams) -> None:
        """v0 enters Down at edge 0.08, clears the 0.07 bar -> kept and tagged."""
        # 1-fair_up = 0.62 vs down_ask 0.54 -> edge_down 0.08.
        view = _view(fair_up=0.38, up_ask=0.50, down_ask=0.54)
        sig = down_skeptic_v4(view, params)
        assert isinstance(sig, ShadowSignal)
        assert sig.side == "Down"
        assert sig.edge == pytest.approx(0.08)
        assert "down-skeptic" in sig.reason

    def test_premium_is_tunable(self, params: strategy.StrategyParams) -> None:
        """A zero premium reduces it to v0's Down behaviour (no extra bar)."""
        view = _view(fair_up=0.42, up_ask=0.50, down_ask=0.52)  # Down edge 0.06
        assert down_skeptic_v4(view, params, down_edge_premium=0.0) is not None
        assert down_skeptic_v4(view, params, down_edge_premium=0.02) is None


# ---------------------------------------------------------------------------
# down_skeptic_drift_v6  (regime-aware two-sided edge toll)
# ---------------------------------------------------------------------------


class TestDownSkepticDriftV6:
    def test_neutral_regime_equals_v4_up(self, params: strategy.StrategyParams) -> None:
        """drift=0 -> regime 0 -> same Up decision as down_skeptic_v4."""
        view = _view(
            up_ask=0.55, down_ask=0.46, fair_up=0.70,
            sigma_per_second=0.0003, drift_per_second=0.0,
        )
        v6 = down_skeptic_drift_v6(view, params)
        v4 = down_skeptic_v4(view, params)
        assert isinstance(v6, ShadowSignal) and isinstance(v4, ShadowSignal)
        assert (v6.side, v6.entry_price, v6.edge) == (v4.side, v4.entry_price, v4.edge)

    def test_neutral_regime_equals_v4_down_marginal(
        self, params: strategy.StrategyParams
    ) -> None:
        """drift=0 -> a marginal Down (edge 0.06 < 0.07) is vetoed, like v4."""
        view = _view(
            fair_up=0.42, up_ask=0.50, down_ask=0.52,
            sigma_per_second=0.0003, drift_per_second=0.0,
        )
        assert down_skeptic_v4(view, params) is None
        assert down_skeptic_drift_v6(view, params) is None

    def test_drift_none_equals_v4(self, params: strategy.StrategyParams) -> None:
        """Missing drift feed -> regime 0 -> Up passes through like v4."""
        view = _view(up_ask=0.55, down_ask=0.46, fair_up=0.70, drift_per_second=None)
        v6 = down_skeptic_drift_v6(view, params)
        assert isinstance(v6, ShadowSignal) and v6.side == "Up"

    def test_bear_regime_vetoes_thin_up_that_v4_takes(
        self, params: strategy.StrategyParams
    ) -> None:
        """Full bear regime tolls Up by +0.02; a 0.06-edge Up v4 takes is vetoed."""
        # edge_up = 0.62 - 0.56 = 0.06 (in [0.05, 0.07)); Down not executable.
        common = dict(fair_up=0.62, up_ask=0.56, down_ask=0.46, sigma_per_second=0.0003)
        v4_sig = down_skeptic_v4(_view(**common), params)
        assert isinstance(v4_sig, ShadowSignal) and v4_sig.side == "Up"  # v4 takes it
        bear = _view(**common, drift_per_second=-0.0003)  # drift/sigma=-1 -> regime -1
        assert down_skeptic_drift_v6(bear, params) is None  # Up bar 0.07 > 0.06 -> veto

    def test_bull_regime_vetoes_thin_down_that_v4_takes(
        self, params: strategy.StrategyParams
    ) -> None:
        """Full bull regime tolls Down by +0.04; a 0.08-edge Down v4 keeps is vetoed."""
        # edge_down = (1-0.38) - 0.54 = 0.08 (>= v4 bar 0.07, < v6 bull bar 0.09).
        common = dict(fair_up=0.38, up_ask=0.50, down_ask=0.54, sigma_per_second=0.0003)
        v4_sig = down_skeptic_v4(_view(**common), params)
        assert isinstance(v4_sig, ShadowSignal) and v4_sig.side == "Down"  # v4 keeps it
        bull = _view(**common, drift_per_second=0.0003)  # drift/sigma=+1 -> regime +1
        assert down_skeptic_drift_v6(bull, params) is None  # Down bar 0.09 > 0.08 -> veto

    def test_none_when_v0_declines(self, params: strategy.StrategyParams) -> None:
        """No executable quotes -> v0 picks no side -> None (v6 never forces a side)."""
        view = _view(up_ask=None, down_ask=None, drift_per_second=-0.0003)
        assert down_skeptic_drift_v6(view, params) is None
