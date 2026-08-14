"""Shared data contracts for the two-sided pair quoter (#182).

The strategy this models is **market making**, not arbitrage. Measured on the
live venue 2026-08-14, the best-ask sum on BTC 5m windows is 1.0100 — crossing
both legs costs 1.0449 after fees for a $1.00 payout, so there is no taker
opportunity. The opportunity is on the *bid* side: with ``Up bid 0.500`` and
``Down bid 0.490`` the pair costs 0.990 and redeems at exactly 1.00, fee-free
because maker fills are not charged.

Four frozen value objects flow through the shadow loop:

* :class:`BookSide` — one leg's top-of-book plus the depth resting at our price.
* :class:`QuotePlan` — where we *would* post on both legs, and the edge that
  implies. ``None`` from the quoter means "not worth quoting this tick".
* :class:`RestingOrder` — a hypothetical resting bid, carrying the queue depth
  ahead of it at post time. This is the honesty-critical field: we always join
  the *back* of the queue and never assume priority we did not earn.
* :class:`PairOutcome` — the settled result of one window: a completed hedged
  pair, a stranded single leg, or nothing.

All frozen so a plan cannot drift between decision and ledger write.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BookSide:
    """Top-of-book for one leg, plus the size resting at our intended price.

    Attributes:
        token_id: CLOB token id for this outcome.
        outcome: ``'Up'`` or ``'Down'``.
        best_bid: Best bid price, or ``None`` when the bid side is empty.
        best_ask: Best ask price, or ``None`` when the ask side is empty.
        depth_at_bid: Size resting at ``best_bid``. This becomes the queue we
            must clear before a hypothetical join-the-bid order fills.
    """

    token_id: str
    outcome: str
    best_bid: float | None
    best_ask: float | None
    depth_at_bid: float


@dataclass(frozen=True)
class QuotePlan:
    """Where the strategy would post on both legs of one window.

    ``edge_per_pair`` is ``1.0 - (up_price + down_price)``. Maker fills are
    fee-free on Polymarket, so unlike the taker path there is no fee term to
    subtract here — that asymmetry is the entire reason the strategy exists.

    Attributes:
        window_slug: The 5-minute window this plan is for.
        up_price: Price we would post our Up bid at.
        down_price: Price we would post our Down bid at.
        size: Shares quoted on each leg.
        edge_per_pair: Dollars earned per completed pair.
        up_depth_ahead: Queue ahead of us on the Up leg at post time.
        down_depth_ahead: Queue ahead of us on the Down leg at post time.
    """

    window_slug: str
    up_price: float
    down_price: float
    size: float
    edge_per_pair: float
    up_depth_ahead: float
    down_depth_ahead: float

    @property
    def pair_cost(self) -> float:
        """Total cost of one completed pair, which redeems at exactly 1.00."""
        return self.up_price + self.down_price


@dataclass(frozen=True)
class RestingOrder:
    """A hypothetical resting bid on one leg.

    Attributes:
        outcome: ``'Up'`` or ``'Down'``.
        price: The price we are resting at.
        size: Shares we want filled.
        depth_ahead: Size that was already queued at ``price`` when we posted.
            We fill only after cumulative through-volume exceeds this.
        posted_ts: Unix seconds at which the order was posted.
        filled: Shares filled so far.
    """

    outcome: str
    price: float
    size: float
    depth_ahead: float
    posted_ts: int
    filled: float = 0.0

    @property
    def is_full(self) -> bool:
        """True once the order is completely filled."""
        return self.filled >= self.size - 1e-9


@dataclass(frozen=True)
class PairOutcome:
    """Settled result for one window.

    Attributes:
        window_slug: The window that settled.
        up_filled: Shares filled on the Up leg.
        down_filled: Shares filled on the Down leg.
        up_price: Price paid on the Up leg.
        down_price: Price paid on the Down leg.
        resolved_up: Whether the window resolved Up.
        pairs: Hedged share-pairs (``min`` of the two legs) — deterministic PnL.
        stranded: Unhedged shares — directional risk, settled on outcome.
        pnl: Net dollars, pairs and stranded legs combined.
    """

    window_slug: str
    up_filled: float
    down_filled: float
    up_price: float
    down_price: float
    resolved_up: bool
    pairs: float
    stranded: float
    pnl: float
