"""Two-sided quote placement for the 5m Up/Down pair strategy (#182).

Pure decision logic: given both legs' books, decide whether to rest a bid on
each and at what price. No I/O.

The strategy joins the existing best bid on both legs rather than improving it.
Improving by a tick would buy queue priority, but it costs a tick of edge — and
on a 1c gross edge that is the whole trade. Joining is also the conservative
assumption for a shadow test: we take the worst queue position available at the
best price, so any measured edge is a lower bound.

Sizing note: Polymarket enforces a **5-share minimum per order**, and this repo
has been bitten by that before — a dollar cap below the share floor silently
blocked 100% of entries (#85/#87 in ``tasks/lessons.md``). So the floor here is
denominated in *shares*, not dollars.
"""

from __future__ import annotations

from btc_bot.pairarb.types import BookSide, QuotePlan

# Polymarket rejects orders below 5 shares. Denominated in shares deliberately —
# a dollar-denominated floor inverts against price and produces unplaceable
# orders at favourites (lessons.md, #85).
MIN_ORDER_SHARES = 5.0

# Minimum dollars per completed pair before quoting is worth it. At the observed
# 1c book (Up bid 0.500 + Down bid 0.490 = 0.990) the gross edge is 0.010, so a
# 0.005 floor keeps roughly half the observed opportunity set while refusing to
# quote for scraps that adverse selection would eat.
DEFAULT_MIN_EDGE = 0.005


def plan_quote(
    window_slug: str,
    up: BookSide,
    down: BookSide,
    size: float,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> QuotePlan | None:
    """Decide where to rest bids on both legs, or ``None`` to stand aside.

    Returns ``None`` when either leg has no bid to join, when the requested size
    is below the venue's share minimum, or when the two best bids do not sum to
    meaningfully less than the 1.00 a completed pair redeems for.

    Note there is no fee term: maker fills are fee-free on Polymarket. That
    asymmetry against the 0.07·p·(1−p) taker fee is the entire reason a 1c gross
    edge is tradeable at all — the same pair crossed as a taker costs 1.0449.
    """
    if up.best_bid is None or down.best_bid is None:
        return None
    if size < MIN_ORDER_SHARES:
        return None

    edge = 1.0 - (up.best_bid + down.best_bid)
    if edge < min_edge:
        return None

    return QuotePlan(
        window_slug=window_slug,
        up_price=up.best_bid,
        down_price=down.best_bid,
        size=size,
        edge_per_pair=edge,
        up_depth_ahead=up.depth_at_bid,
        down_depth_ahead=down.depth_at_bid,
    )
