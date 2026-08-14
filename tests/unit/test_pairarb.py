"""Unit tests for the two-sided pair quoter and its maker fill model (#182).

The fill model is the honesty-critical part: a maker simulation that is too
generous manufactures profit that does not exist. These tests pin the
pessimistic assumptions in place — back of queue, displayed size only, tape
attribution — so a future change that loosens them fails loudly.
"""

from __future__ import annotations

from btc_bot.pairarb.fills import hits_resting_bid, settle_window, simulate_fill
from btc_bot.pairarb.quoter import MIN_ORDER_SHARES, plan_quote
from btc_bot.pairarb.types import BookSide, RestingOrder


def _order(price=0.50, size=10.0, depth_ahead=0.0, outcome="Up", posted_ts=1000):
    return RestingOrder(
        outcome=outcome,
        price=price,
        size=size,
        depth_ahead=depth_ahead,
        posted_ts=posted_ts,
    )


def _trade(outcome, price, size, ts=1000, side="BUY"):
    return {"outcome": outcome, "price": price, "size": size, "timestamp": ts, "side": side}


# --------------------------------------------------------------------------- #
# Tape attribution
# --------------------------------------------------------------------------- #


def test_complementary_buy_hits_our_bid():
    """A BUY of Down at 0.50 is a SELL of Up at 0.50 — it lifts an Up bid at 0.50."""
    assert hits_resting_bid(_trade("Down", 0.50, 10), "Up", 0.50)


def test_complementary_buy_too_cheap_does_not_reach_us():
    """A BUY of Down at 0.45 sells Up at 0.55, above our 0.50 bid — never reaches us."""
    assert not hits_resting_bid(_trade("Down", 0.45, 10), "Up", 0.50)


def test_buy_of_our_own_outcome_does_not_hit_our_bid():
    """A BUY of Up lifts the Up *ask*. It must never count as filling our Up bid."""
    assert not hits_resting_bid(_trade("Up", 0.51, 10), "Up", 0.50)


def test_explicit_sell_of_our_outcome_hits_us():
    assert hits_resting_bid(_trade("Up", 0.49, 10, side="SELL"), "Up", 0.50)


def test_seller_who_stopped_above_us_still_counts_as_through_volume():
    """A BUY of Down at 0.55 sells Up at 0.45 — through our 0.50 level."""
    assert hits_resting_bid(_trade("Down", 0.55, 10), "Up", 0.50)


def test_malformed_trade_is_ignored_not_crashed():
    assert not hits_resting_bid({"outcome": "Down", "price": "nope"}, "Up", 0.50)
    assert not hits_resting_bid({}, "Up", 0.50)


# --------------------------------------------------------------------------- #
# Queue mechanics — the pessimistic assumptions
# --------------------------------------------------------------------------- #


def test_queue_ahead_absorbs_volume_before_we_fill():
    """With 100 ahead of us, 60 shares of flow fills nothing of ours."""
    order = _order(size=10.0, depth_ahead=100.0)
    assert simulate_fill(order, [_trade("Down", 0.50, 60)]).filled == 0.0


def test_we_fill_only_the_excess_over_the_queue():
    """105 through, 100 ahead -> exactly 5 for us, not 105."""
    order = _order(size=10.0, depth_ahead=100.0)
    assert simulate_fill(order, [_trade("Down", 0.50, 105)]).filled == 5.0


def test_fill_is_capped_at_our_own_size():
    order = _order(size=10.0, depth_ahead=0.0)
    assert simulate_fill(order, [_trade("Down", 0.50, 999)]).filled == 10.0


def test_trades_before_we_posted_do_not_fill_us():
    """We were not in the book yet; prior volume must not count."""
    order = _order(size=10.0, depth_ahead=0.0, posted_ts=1000)
    assert simulate_fill(order, [_trade("Down", 0.50, 50, ts=999)]).filled == 0.0


def test_volume_accumulates_across_multiple_trades():
    order = _order(size=10.0, depth_ahead=20.0)
    trades = [_trade("Down", 0.50, 12), _trade("Down", 0.50, 13)]
    assert simulate_fill(order, trades).filled == 5.0


def test_empty_tape_leaves_order_untouched():
    order = _order(size=10.0, depth_ahead=0.0)
    assert simulate_fill(order, []) is order


# --------------------------------------------------------------------------- #
# Settlement — hedged pairs vs stranded legs
# --------------------------------------------------------------------------- #


def test_completed_pair_pnl_is_outcome_independent():
    """The whole point: a hedged pair pays the same whichever way it resolves."""
    up = settle_window("w", 10.0, 10.0, 0.50, 0.49, resolved_up=True)
    down = settle_window("w", 10.0, 10.0, 0.50, 0.49, resolved_up=False)
    assert up.pnl == down.pnl
    assert abs(up.pnl - 10.0 * 0.01) < 1e-9
    assert up.stranded == 0.0


def test_stranded_leg_settles_on_outcome_never_at_par():
    """A naked Up leg that loses must book its full cost as a loss."""
    out = settle_window("w", 10.0, 0.0, 0.50, 0.49, resolved_up=False)
    assert out.pairs == 0.0
    assert out.stranded == 10.0
    assert abs(out.pnl - (-10.0 * 0.50)) < 1e-9


def test_stranded_winning_leg_books_its_gain():
    out = settle_window("w", 10.0, 0.0, 0.50, 0.49, resolved_up=True)
    assert abs(out.pnl - (10.0 * 0.50)) < 1e-9


def test_partial_hedge_splits_into_pair_plus_stranded():
    """10 Up / 4 Down = 4 hedged pairs + 6 stranded Up, resolving Down."""
    out = settle_window("w", 10.0, 4.0, 0.50, 0.49, resolved_up=False)
    assert out.pairs == 4.0
    assert out.stranded == 6.0
    expected = 4.0 * 0.01 + 6.0 * (0.0 - 0.50)
    assert abs(out.pnl - expected) < 1e-9


def test_stranded_down_leg_wins_when_market_resolves_down():
    out = settle_window("w", 0.0, 10.0, 0.50, 0.49, resolved_up=False)
    assert abs(out.pnl - (10.0 * (1.0 - 0.49))) < 1e-9


# --------------------------------------------------------------------------- #
# Quote planning
# --------------------------------------------------------------------------- #


def _side(outcome, bid, depth=100.0, ask=None):
    return BookSide(
        token_id=f"tok-{outcome}",
        outcome=outcome,
        best_bid=bid,
        best_ask=ask,
        depth_at_bid=depth,
    )


def test_quotes_when_bid_sum_leaves_edge():
    """The live 2026-08-14 book: 0.500 + 0.490 = 0.990 -> 1c per pair."""
    plan = plan_quote("w", _side("Up", 0.50), _side("Down", 0.49), size=10.0)
    assert plan is not None
    assert abs(plan.edge_per_pair - 0.01) < 1e-9
    assert abs(plan.pair_cost - 0.99) < 1e-9


def test_stands_aside_when_bids_sum_to_par():
    """No edge at 0.50/0.50 — quoting here earns nothing and risks stranding."""
    assert plan_quote("w", _side("Up", 0.50), _side("Down", 0.50), size=10.0) is None


def test_stands_aside_when_edge_below_floor():
    plan = plan_quote("w", _side("Up", 0.50), _side("Down", 0.498), size=10.0)
    assert plan is None


def test_stands_aside_when_a_leg_has_no_bid():
    assert plan_quote("w", _side("Up", None), _side("Down", 0.49), size=10.0) is None
    assert plan_quote("w", _side("Up", 0.50), _side("Down", None), size=10.0) is None


def test_rejects_size_below_venue_share_minimum():
    """lessons.md #85: a sub-minimum order is unplaceable, not merely small."""
    assert plan_quote("w", _side("Up", 0.50), _side("Down", 0.49), size=1.0) is None
    assert plan_quote(
        "w", _side("Up", 0.50), _side("Down", 0.49), size=MIN_ORDER_SHARES
    ) is not None


def test_plan_carries_queue_depth_for_both_legs():
    plan = plan_quote(
        "w", _side("Up", 0.50, depth=313.0), _side("Down", 0.49, depth=224.0), size=10.0
    )
    assert plan is not None
    assert plan.up_depth_ahead == 313.0
    assert plan.down_depth_ahead == 224.0
