"""Candidate strategies for the shadow forward-tester.

Each function is a **pure** decision: given an immutable
:class:`~btc_bot.shadow.types.SnapshotView` and the active
:class:`~btc_bot.strategy.StrategyParams`, it returns the would-be trade as
a :class:`~btc_bot.shadow.types.ShadowSignal`, or ``None`` for "no trade
this tick". No database, no clock, no I/O — the loop wiring (built
separately) owns persistence and settlement, so these functions can be
unit-tested with hand-built fixtures and reasoned about in isolation.

The candidates here layer an extra gate on top of the live v0 signal
(:func:`btc_bot.strategy.signal_from_executable_edges`), which is reused
verbatim rather than reimplemented:

* :func:`cushion_favorite_v2` — take v0's pick only when spot sits a minimum
  number of basis points on the favourable side of the reference print, so
  a hair-thin lead near a pinned price does not count as a real cushion.
* :func:`late_convergence_v3` — late in the window, buy the favoured side
  only when the book *and* the model both call it near-certain and the ask
  still leaves room to profit.
"""
from __future__ import annotations

from btc_bot import strategy
from btc_bot.shadow.types import ShadowSignal, SnapshotView


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def cushion_favorite_v2(
    view: SnapshotView,
    params: strategy.StrategyParams,
    cushion_min_bps: float = 1.5,
) -> ShadowSignal | None:
    """Reuse v0's pick, but require a minimum spot-vs-reference cushion.

    v0 (:func:`btc_bot.strategy.signal_from_executable_edges`) decides the
    side, confidence, and reason from the executable edges. This wrapper
    then demands that the chosen side has at least ``cushion_min_bps`` basis
    points of distance between spot and the reference print *in its favour*
    — Up needs ``spot`` above ``reference``, Down needs ``spot`` below it.
    A lead thinner than the cushion is treated as noise around a pinned
    price and skipped, even if the edge cleared v0's threshold.

    Args:
        view: Immutable per-tick market view.
        params: Active strategy parameters (v0 thresholds).
        cushion_min_bps: Minimum favourable spot-vs-reference gap, in bps.

    Returns:
        A :class:`ShadowSignal` when v0 wants the trade *and* the cushion
        clears the floor, otherwise ``None``.
    """
    edge_up = view.fair_up - view.up_ask if view.up_ask is not None else None
    edge_down = (1.0 - view.fair_up) - view.down_ask if view.down_ask is not None else None

    side, confidence, _notional, reason = strategy.signal_from_executable_edges(
        edge_up,
        edge_down,
        view.remaining_seconds,
        view.up_ask,
        view.down_ask,
        params,
    )
    if side is None:
        return None

    if side == "Up":
        entry_price = view.up_ask
        edge = edge_up
        fair_prob = view.fair_up
    else:
        entry_price = view.down_ask
        edge = edge_down
        fair_prob = 1.0 - view.fair_up

    # mypy/ruff: the chosen side's ask cannot be None — v0 only selects a
    # side whose ask was a usable candidate — but assert intent explicitly.
    if entry_price is None or edge is None:
        return None

    favourable_gap = (view.spot - view.reference) if side == "Up" else (view.reference - view.spot)
    cushion_bps = favourable_gap / view.spot * 1e4
    if cushion_bps < cushion_min_bps:
        return None

    return ShadowSignal(
        side=side,
        entry_price=entry_price,
        fair_prob=fair_prob,
        edge=edge,
        confidence=confidence,
        reason=f"cushion {cushion_bps:.1f}bps; {reason}",
    )


def late_convergence_v3(
    view: SnapshotView,
    params: strategy.StrategyParams,
    near_certain: float = 0.85,
    late_min_s: int = 5,
    late_max_s: int = 45,
) -> ShadowSignal | None:
    """Buy the favoured side late, when the BOOK calls it near-certain.

    Inside the late time band ``[late_min_s, late_max_s]`` this candidate
    sides with the book's favourite (Up when ``market_up_price >= 0.5``,
    else Down) and takes it when the book's favoured price is at or above
    ``near_certain`` and the fair-value model at least *weakly* agrees
    (fair ≥ 0.5). It keys off the BOOK on purpose: the price is what predicts
    the outcome, while the fair-value model is anti-predictive here, so the
    original gate (requiring the model to *also* be near-certain) was
    self-defeating and never fired. A final guard requires the executable ask
    below ``0.99`` so there is still room to profit net of cost. ``params`` is
    accepted for signature parity; this gate is independent of v0's edge
    thresholds.

    Args:
        view: Immutable per-tick market view.
        params: Active strategy parameters (unused here; kept for parity).
        near_certain: Minimum price/fair probability for the favoured side.
        late_min_s: Inclusive lower bound of the late time band, seconds.
        late_max_s: Inclusive upper bound of the late time band, seconds.

    Returns:
        A :class:`ShadowSignal` when the time band, the book-and-model
        near-certainty, and the room-to-profit guard all pass, else ``None``.
    """
    del params  # signature parity with the other candidates; not used here

    if not (late_min_s <= view.remaining_seconds <= late_max_s):
        return None

    favored = "Up" if view.market_up_price >= 0.5 else "Down"
    entry = view.up_ask if favored == "Up" else view.down_ask
    if entry is None:
        return None

    fair_fav = view.fair_up if favored == "Up" else 1.0 - view.fair_up
    fav_price = view.market_up_price if favored == "Up" else 1.0 - view.market_up_price

    # Book must be near-certain; the (anti-predictive) model need only weakly
    # agree, not also be near-certain — the old AND gate kept this from firing.
    if fav_price < near_certain or fair_fav < 0.5:
        return None

    if entry >= 0.99:
        return None

    return ShadowSignal(
        side=favored,
        entry_price=entry,
        fair_prob=fair_fav,
        edge=fair_fav - entry,
        confidence=fair_fav,
        reason=(
            f"late-convergence rs={view.remaining_seconds}; "
            f"book {fav_price:.2f} fair {fair_fav:.2f}"
        ),
    )


def down_skeptic_v4(
    view: SnapshotView,
    params: strategy.StrategyParams,
    down_edge_premium: float = 0.02,
) -> ShadowSignal | None:
    """Reuse v0's pick, but make DOWN entries clear a higher edge bar.

    The settlement rule resolves ties (``spot >= reference``) to Up, so Up
    wins ~52% of windows structurally and Down fights a ~2pp headwind the
    fair-value model does not fully price — leaving v0 prone to over-betting
    Down on thin edges that then lose. This candidate takes v0's pick
    unchanged when it is **Up**, but vetoes a **Down** pick whose edge does
    not exceed ``entry_edge_min + down_edge_premium``: Down must earn extra
    margin to be worth taking against the structural Up edge. (With v0's
    default edge cap this leaves Down a narrow high-edge band, so the model
    becomes strongly Up-leaning — which is exactly the hypothesis under test:
    that over-betting the disfavoured Down side is what bleeds.)

    Args:
        view: Immutable per-tick market view.
        params: Active strategy parameters (v0 thresholds).
        down_edge_premium: Extra edge a Down pick must clear above the v0
            floor, in probability units.

    Returns:
        A :class:`ShadowSignal` when v0 wants the trade *and* (if Down) the
        elevated edge bar is cleared, otherwise ``None``.
    """
    edge_up = view.fair_up - view.up_ask if view.up_ask is not None else None
    edge_down = (1.0 - view.fair_up) - view.down_ask if view.down_ask is not None else None

    side, confidence, _notional, reason = strategy.signal_from_executable_edges(
        edge_up,
        edge_down,
        view.remaining_seconds,
        view.up_ask,
        view.down_ask,
        params,
    )
    if side is None:
        return None

    if side == "Up":
        entry_price = view.up_ask
        edge = edge_up
        fair_prob = view.fair_up
    else:
        entry_price = view.down_ask
        edge = edge_down
        fair_prob = 1.0 - view.fair_up

    if entry_price is None or edge is None:
        return None

    # Down skeptic: a Down pick must clear a HIGHER edge bar than v0's floor.
    if side == "Down" and edge < params.entry_edge_min + down_edge_premium:
        return None

    note = f"down-skeptic +{down_edge_premium:.02f} on Down; " if side == "Down" else ""
    return ShadowSignal(
        side=side,
        entry_price=entry_price,
        fair_prob=fair_prob,
        edge=edge,
        confidence=confidence,
        reason=f"{note}{reason}",
    )


def cushion_drift_v5(
    view: SnapshotView,
    params: strategy.StrategyParams,
    cushion_min_bps: float = 1.5,
    k_bps: float = 1.0,
    regime_full_scale: float = 0.3,
    floor_bps: float = 0.5,
) -> ShadowSignal | None:
    """v0's pick, but the cushion bar is regime-adaptive and two-sided.

    Like :func:`cushion_favorite_v2` this reuses v0's side selection and then
    demands a minimum favourable spot-vs-reference cushion — but instead of a
    fixed bar it shifts the bar by the current price *regime*, asymmetrically:

    * ``regime`` is **standardised momentum**: the recent directional drift
      divided by volatility, ``drift_per_second / sigma_per_second``, scaled by
      ``regime_full_scale`` and clamped to ``[-1, +1]`` (bullish positive). It
      is dimensionless and self-calibrating across volatility regimes, so no
      hand-picked price scale is fitted to one market.
    * In a bull regime the **Up** bar drops by ``k_bps * regime`` (Up needs less
      cushion) while the **Down** bar rises by the same (Down must earn more);
      a bear regime is the mirror image. Each bar is clamped to ``floor_bps`` so
      a strong regime can never drop it to zero — a thin lead near a pinned
      price is still rejected as noise.

    Regime never *forces* a side: v0 still chooses from the executable edges,
    and a large enough cushion still clears a raised bar. When the drift feed is
    unavailable (``drift_per_second`` is ``None``) or volatility is missing, the
    regime is ``0`` and this candidate is **identical to** ``cushion_favorite_v2``
    — which is therefore its exact control (the ``k_bps = 0`` case).

    Args:
        view: Immutable per-tick market view.
        params: Active strategy parameters (v0 thresholds).
        cushion_min_bps: Base (regime-neutral) cushion floor, in bps.
        k_bps: Bar shift per unit regime, in bps.
        regime_full_scale: drift/sigma ratio mapped to full (``±1``) regime.
        floor_bps: Hard lower bound on either side's bar, in bps.

    Returns:
        A :class:`ShadowSignal` when v0 wants the trade *and* the chosen side's
        regime-adjusted cushion bar is cleared, otherwise ``None``.
    """
    edge_up = view.fair_up - view.up_ask if view.up_ask is not None else None
    edge_down = (1.0 - view.fair_up) - view.down_ask if view.down_ask is not None else None

    side, confidence, _notional, reason = strategy.signal_from_executable_edges(
        edge_up,
        edge_down,
        view.remaining_seconds,
        view.up_ask,
        view.down_ask,
        params,
    )
    if side is None:
        return None

    if side == "Up":
        entry_price = view.up_ask
        edge = edge_up
        fair_prob = view.fair_up
    else:
        entry_price = view.down_ask
        edge = edge_down
        fair_prob = 1.0 - view.fair_up

    if entry_price is None or edge is None:
        return None

    # Standardised-momentum regime in [-1, +1]; 0 when the drift feed or
    # volatility is unavailable -> bars collapse to the v2 fixed cushion.
    drift = view.drift_per_second
    sigma = view.sigma_per_second
    if drift is None or not sigma or sigma <= 0:
        regime = 0.0
    else:
        regime = _clamp((drift / sigma) / regime_full_scale, -1.0, 1.0)

    ceil_bps = cushion_min_bps + k_bps
    if side == "Up":
        bar_bps = _clamp(cushion_min_bps - k_bps * regime, floor_bps, ceil_bps)
    else:
        bar_bps = _clamp(cushion_min_bps + k_bps * regime, floor_bps, ceil_bps)

    favourable_gap = (view.spot - view.reference) if side == "Up" else (view.reference - view.spot)
    cushion_bps = favourable_gap / view.spot * 1e4
    if cushion_bps < bar_bps:
        return None

    return ShadowSignal(
        side=side,
        entry_price=entry_price,
        fair_prob=fair_prob,
        edge=edge,
        confidence=confidence,
        reason=f"cushion {cushion_bps:.1f}bps vs bar {bar_bps:.1f} (regime {regime:+.2f}); {reason}",
    )


def down_skeptic_drift_v6(
    view: SnapshotView,
    params: strategy.StrategyParams,
    down_edge_premium: float = 0.02,
    regime_full_scale: float = 0.3,
) -> ShadowSignal | None:
    """v4's down-skeptic edge toll, made regime-aware and two-sided.

    Reuses v0's side selection (:func:`strategy.signal_from_executable_edges`)
    exactly like :func:`down_skeptic_v4`, then gates by an edge toll. v4 charges
    a fixed ``down_edge_premium`` on every Down pick — correct against the
    structural ``spot >= reference`` Up bias in a flat/up market, but backwards
    in a bearish regime, where it leans the book into the losing Up side. Here
    the toll flexes with the same standardised-momentum regime as
    :func:`cushion_drift_v5`:

    * ``regime = clamp((drift/sigma)/regime_full_scale, -1, +1)`` (bullish
      positive); ``0`` when the drift feed or volatility is unavailable.
    * ``down_extra = down_edge_premium * clamp(1 + regime, 0, 2)`` — Down's toll
      grows in a bull regime and shrinks to ``0`` in a full bear regime.
    * ``up_extra = down_edge_premium * clamp(-regime, 0, 1)`` — Up earns a toll
      only in a bear regime.

    At ``regime == 0`` (or no drift feed) ``up_extra == 0`` and
    ``down_extra == down_edge_premium`` -> the decision is **identical to**
    :func:`down_skeptic_v4`, which is therefore its exact control.

    Args:
        view: Immutable per-tick market view.
        params: Active strategy parameters (v0 thresholds).
        down_edge_premium: Base toll a disfavoured side must clear above the v0
            floor, in probability units (v4's fixed value).
        regime_full_scale: drift/sigma ratio mapped to full (``±1``) regime.

    Returns:
        A :class:`ShadowSignal` when v0 wants the trade *and* the chosen side
        clears its regime-adjusted edge bar, otherwise ``None``.
    """
    edge_up = view.fair_up - view.up_ask if view.up_ask is not None else None
    edge_down = (1.0 - view.fair_up) - view.down_ask if view.down_ask is not None else None

    side, confidence, _notional, reason = strategy.signal_from_executable_edges(
        edge_up,
        edge_down,
        view.remaining_seconds,
        view.up_ask,
        view.down_ask,
        params,
    )
    if side is None:
        return None

    if side == "Up":
        entry_price = view.up_ask
        edge = edge_up
        fair_prob = view.fair_up
    else:
        entry_price = view.down_ask
        edge = edge_down
        fair_prob = 1.0 - view.fair_up

    if entry_price is None or edge is None:
        return None

    drift = view.drift_per_second
    sigma = view.sigma_per_second
    if drift is None or not sigma or sigma <= 0:
        regime = 0.0
    else:
        regime = _clamp((drift / sigma) / regime_full_scale, -1.0, 1.0)

    up_extra = down_edge_premium * _clamp(-regime, 0.0, 1.0)
    down_extra = down_edge_premium * _clamp(1.0 + regime, 0.0, 2.0)

    if side == "Up" and edge < params.entry_edge_min + up_extra:
        return None
    if side == "Down" and edge < params.entry_edge_min + down_extra:
        return None

    extra = up_extra if side == "Up" else down_extra
    note = f"down-skeptic-drift regime={regime:+.2f} +{extra:.03f} on {side}; "
    return ShadowSignal(
        side=side,
        entry_price=entry_price,
        fair_prob=fair_prob,
        edge=edge,
        confidence=confidence,
        reason=f"{note}{reason}",
    )
