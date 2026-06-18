"""Candidate strategies for the shadow forward-tester.

Each function is a **pure** decision: given an immutable
:class:`~btc_bot.shadow.types.SnapshotView` and the active
:class:`~btc_bot.strategy.StrategyParams`, it returns the would-be trade as
a :class:`~btc_bot.shadow.types.ShadowSignal`, or ``None`` for "no trade
this tick". No database, no clock, no I/O — the loop wiring (built
separately) owns persistence and settlement, so these functions can be
unit-tested with hand-built fixtures and reasoned about in isolation.

The two candidates here layer an extra gate on top of the live v0 signal
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
    near_certain: float = 0.88,
    late_min_s: int = 5,
    late_max_s: int = 30,
) -> ShadowSignal | None:
    """Buy the favoured side late, when book and model both call it near-certain.

    Inside the late time band ``[late_min_s, late_max_s]`` this candidate
    sides with the book's favourite (Up when ``market_up_price >= 0.5``,
    else Down) and takes it only when **both** the book's favoured price and
    the model's fair probability for that side are at or above
    ``near_certain``. Requiring agreement avoids buying a side the model
    likes but the book doesn't (or vice versa). A final guard requires the
    executable ask below ``0.99`` so there is still room to profit net of
    cost. ``params`` is accepted for signature parity with the other
    candidates; this gate is independent of v0's edge thresholds.

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

    if fav_price < near_certain or fair_fav < near_certain:
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
