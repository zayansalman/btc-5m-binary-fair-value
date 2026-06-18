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
from btc_bot.shadow.signals import cushion_favorite_v2, late_convergence_v3
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
# late_convergence_v3
# ---------------------------------------------------------------------------


class TestLateConvergenceV3:
    def test_none_before_time_band(self, params: strategy.StrategyParams) -> None:
        """remaining_seconds above late_max_s -> None."""
        view = _view(remaining_seconds=45, market_up_price=0.95, fair_up=0.95)
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
        """Book says near-certain Up, model does not -> None."""
        view = _view(
            remaining_seconds=20,
            market_up_price=0.95,
            up_ask=0.95,
            fair_up=0.70,  # model below near_certain for Up
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
        """The late band is inclusive at both ends (5 and 30 by default)."""
        edge_view = lambda rs: _view(  # noqa: E731 - terse, test-local
            remaining_seconds=rs, market_up_price=0.93, up_ask=0.93, fair_up=0.95
        )
        assert late_convergence_v3(edge_view(5), params) is not None
        assert late_convergence_v3(edge_view(30), params) is not None
        assert late_convergence_v3(edge_view(4), params) is None
        assert late_convergence_v3(edge_view(31), params) is None
