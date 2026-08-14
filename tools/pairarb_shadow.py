"""Live shadow runner for two-sided maker quoting on 5m Up/Down markets (#182).

**Places no orders.** Reads public books and the public trade tape, simulates
what resting bids on both legs would have done, and settles each window on its
real outcome. Nothing here can touch money — there is no signer, no key, and no
write path to the CLOB.

What it does each cycle:

1. Construct the current and next window slugs from the clock. The 5m slug is a
   pure function of time (``{asset}-updown-5m-{floor(now/300)*300}``), so it is
   built, never discovered — ``venue_recorder.discover()`` pages ``endDate``
   ascending over ``closed=false`` and surfaces zombie Dec-2025 windows instead
   of live ones (#182).
2. On first sight of a window, read both legs' books and ask
   :func:`btc_bot.pairarb.quoter.plan_quote` where it would rest. Record the
   depth already queued at those prices — we join the **back**.
3. Each cycle, pull the market's public trade tape and advance the simulated
   fills (:mod:`btc_bot.pairarb.fills`).
4. Once resolved, settle into hedged pairs plus any stranded leg. Stranded legs
   settle on the realized outcome, never at par.

Usage::

    python tools/pairarb_shadow.py --assets doge,btc --size 10
    python tools/pairarb_shadow.py --assets doge --once
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from btc_bot.pairarb.fills import settle_window, simulate_fill
from btc_bot.pairarb.quoter import DEFAULT_MIN_EDGE, plan_quote
from btc_bot.pairarb.types import BookSide, PairOutcome, QuotePlan, RestingOrder

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

WINDOW_SECONDS = 300
# Grace period after a window closes before we look for its resolution — the
# venue needs a moment to publish outcomePrices.
SETTLE_DELAY = 45


def window_slug(asset: str, start_ts: int) -> str:
    """Build the 5m window slug for ``asset`` starting at ``start_ts``."""
    return f"{asset}-updown-5m-{start_ts}"


def current_window_start(now: int | None = None) -> int:
    """Unix seconds at which the in-progress 5-minute window began."""
    now = int(time.time()) if now is None else now
    return (now // WINDOW_SECONDS) * WINDOW_SECONDS


@dataclass
class TrackedWindow:
    """One window we are shadow-quoting."""

    slug: str
    asset: str
    start_ts: int
    condition_id: str
    up_token: str
    down_token: str
    plan: QuotePlan | None = None
    up_order: RestingOrder | None = None
    down_order: RestingOrder | None = None
    outcome: PairOutcome | None = None
    note: str = ""
    _tape: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def end_ts(self) -> int:
        return self.start_ts + WINDOW_SECONDS

    @property
    def settled(self) -> bool:
        return self.outcome is not None


async def _get(client: httpx.AsyncClient, url: str, **params: Any) -> Any:
    r = await client.get(url, params=params or None, timeout=20.0)
    r.raise_for_status()
    return r.json()


async def fetch_market(client: httpx.AsyncClient, slug: str) -> dict[str, Any] | None:
    """Fetch one market by slug, or ``None`` when the venue does not list it."""
    try:
        rows = await _get(client, f"{GAMMA}/markets", slug=slug)
    except Exception:  # noqa: BLE001 — a missing window is normal, not fatal
        return None
    return rows[0] if isinstance(rows, list) and rows else None


async def fetch_side(
    client: httpx.AsyncClient, token_id: str, outcome: str
) -> BookSide | None:
    """Read one leg's top-of-book and the size resting at its best bid."""
    try:
        book = await _get(client, f"{CLOB}/book", token_id=token_id)
    except Exception:  # noqa: BLE001
        return None
    bids = sorted(
        ((float(x["price"]), float(x["size"])) for x in book.get("bids", [])),
        reverse=True,
    )
    asks = sorted((float(x["price"]), float(x["size"])) for x in book.get("asks", []))
    return BookSide(
        token_id=token_id,
        outcome=outcome,
        best_bid=bids[0][0] if bids else None,
        best_ask=asks[0][0] if asks else None,
        depth_at_bid=bids[0][1] if bids else 0.0,
    )


async def fetch_tape(
    client: httpx.AsyncClient, condition_id: str, limit: int = 500
) -> list[dict[str, Any]]:
    """Public trade tape for one market."""
    try:
        rows = await _get(client, f"{DATA}/trades", market=condition_id, limit=limit)
    except Exception:  # noqa: BLE001
        return []
    return rows if isinstance(rows, list) else []


def _tape_key(t: dict[str, Any]) -> str:
    """Stable-ish identity for a tape row, so repeated polls don't double-count."""
    return "|".join(
        str(t.get(k, "")) for k in ("transactionHash", "asset", "price", "size", "timestamp")
    )


async def discover_window(
    client: httpx.AsyncClient, asset: str, start_ts: int
) -> TrackedWindow | None:
    """Resolve one asset/window into tracked form, or ``None`` if not listed."""
    slug = window_slug(asset, start_ts)
    market = await fetch_market(client, slug)
    if not market:
        return None
    try:
        tokens = json.loads(market["clobTokenIds"])
        outcomes = json.loads(market["outcomes"])
    except (KeyError, ValueError, TypeError):
        return None
    if len(tokens) != 2 or len(outcomes) != 2:
        return None
    idx_up = 0 if str(outcomes[0]).lower() == "up" else 1
    return TrackedWindow(
        slug=slug,
        asset=asset,
        start_ts=start_ts,
        condition_id=str(market.get("conditionId", "")),
        up_token=str(tokens[idx_up]),
        down_token=str(tokens[1 - idx_up]),
    )


async def place_shadow_quote(
    client: httpx.AsyncClient, w: TrackedWindow, size: float, min_edge: float
) -> None:
    """Decide and record where we would have rested on both legs."""
    up = await fetch_side(client, w.up_token, "Up")
    down = await fetch_side(client, w.down_token, "Down")
    if up is None or down is None:
        w.note = "no book"
        return
    plan = plan_quote(w.slug, up, down, size=size, min_edge=min_edge)
    if plan is None:
        bid_sum = (
            f"{up.best_bid + down.best_bid:.3f}"
            if up.best_bid is not None and down.best_bid is not None
            else "n/a"
        )
        w.note = f"stood aside (bid sum {bid_sum})"
        return
    now = int(time.time())
    w.plan = plan
    w.up_order = RestingOrder("Up", plan.up_price, size, plan.up_depth_ahead, now)
    w.down_order = RestingOrder("Down", plan.down_price, size, plan.down_depth_ahead, now)
    w.note = f"quoted {plan.up_price:.3f}/{plan.down_price:.3f}"


async def advance_fills(client: httpx.AsyncClient, w: TrackedWindow) -> None:
    """Pull the tape and advance both simulated resting orders."""
    if w.up_order is None or w.down_order is None:
        return
    for row in await fetch_tape(client, w.condition_id):
        w._tape.setdefault(_tape_key(row), row)
    tape = list(w._tape.values())
    w.up_order = simulate_fill(w.up_order, tape)
    w.down_order = simulate_fill(w.down_order, tape)


async def try_settle(client: httpx.AsyncClient, w: TrackedWindow) -> bool:
    """Settle the window if the venue has published its outcome. True when done."""
    market = await fetch_market(client, w.slug)
    if not market:
        return False
    try:
        prices = [float(x) for x in json.loads(market.get("outcomePrices") or "[]")]
        outcomes = [str(x).lower() for x in json.loads(market.get("outcomes") or "[]")]
    except (ValueError, TypeError):
        return False
    if len(prices) != 2 or max(prices) < 0.99:
        return False  # not resolved yet
    idx_up = 0 if outcomes and outcomes[0] == "up" else 1
    resolved_up = prices[idx_up] >= 0.99

    up_f = w.up_order.filled if w.up_order else 0.0
    dn_f = w.down_order.filled if w.down_order else 0.0
    w.outcome = settle_window(
        window_slug=w.slug,
        up_filled=up_f,
        down_filled=dn_f,
        up_price=w.plan.up_price if w.plan else 0.0,
        down_price=w.plan.down_price if w.plan else 0.0,
        resolved_up=resolved_up,
    )
    return True


def render(tracked: dict[str, TrackedWindow], settled: list[PairOutcome]) -> str:
    """Compact console view: live windows on top, running totals below."""
    lines = ["", "=" * 96]
    lines.append(
        f"{'window':<30} {'quote':<14} {'depth ahead':<13} {'filled U/D':<13} {'status'}"
    )
    lines.append("-" * 96)
    for w in sorted(tracked.values(), key=lambda x: x.start_ts):
        if w.settled:
            continue
        quote = f"{w.plan.up_price:.3f}/{w.plan.down_price:.3f}" if w.plan else "-"
        depth = (
            f"{w.plan.up_depth_ahead:.0f}/{w.plan.down_depth_ahead:.0f}" if w.plan else "-"
        )
        fills = (
            f"{w.up_order.filled:.1f}/{w.down_order.filled:.1f}"
            if w.up_order and w.down_order
            else "-"
        )
        left = w.end_ts - int(time.time())
        status = f"{left:+d}s  {w.note}"
        lines.append(f"{w.slug:<30} {quote:<14} {depth:<13} {fills:<13} {status}")

    if settled:
        pnl = sum(o.pnl for o in settled)
        pairs = sum(o.pairs for o in settled)
        stranded = sum(o.stranded for o in settled)
        filled = [o for o in settled if o.up_filled or o.down_filled]
        both = [o for o in settled if o.up_filled > 0 and o.down_filled > 0]
        lines.append("-" * 96)
        lines.append(
            f"SETTLED {len(settled):>4}  |  any-fill {len(filled):>3}  both-legs {len(both):>3}"
            f"  |  hedged pairs {pairs:>8.1f}  stranded {stranded:>8.1f}"
            f"  |  PnL ${pnl:+.2f}"
        )
        if pairs + stranded > 0:
            lines.append(
                f"{'':8}  hedged share of filled volume: "
                f"{100 * 2 * pairs / (2 * pairs + stranded):.0f}%"
                f"   (stranded legs settle on outcome, never at par)"
            )
    lines.append("=" * 96)
    return "\n".join(lines)


async def run(assets: list[str], size: float, min_edge: float, once: bool) -> int:
    tracked: dict[str, TrackedWindow] = {}
    settled: list[PairOutcome] = []
    print(
        f"pairarb shadow | assets={','.join(assets)} size={size} min_edge={min_edge}"
        f"\nSHADOW ONLY — no orders are placed.\n"
    )
    async with httpx.AsyncClient(headers={"User-Agent": "pairarb-shadow/0.1"}) as client:
        while True:
            now = int(time.time())
            cur = current_window_start(now)
            # Track the in-progress window and the next one.
            for asset in assets:
                for start in (cur, cur + WINDOW_SECONDS):
                    slug = window_slug(asset, start)
                    if slug in tracked:
                        continue
                    w = await discover_window(client, asset, start)
                    if w is None:
                        continue
                    tracked[slug] = w
                    await place_shadow_quote(client, w, size, min_edge)

            for w in list(tracked.values()):
                if w.settled:
                    continue
                if now < w.end_ts:
                    await advance_fills(client, w)
                elif now >= w.end_ts + SETTLE_DELAY:
                    await advance_fills(client, w)
                    if await try_settle(client, w):
                        settled.append(w.outcome)  # type: ignore[arg-type]
                        o = w.outcome
                        assert o is not None
                        print(
                            f"  SETTLED {o.window_slug:<28} "
                            f"{'Up' if o.resolved_up else 'Down':<5} "
                            f"pairs={o.pairs:.1f} stranded={o.stranded:.1f} "
                            f"pnl=${o.pnl:+.2f}"
                        )

            print(render(tracked, settled), flush=True)

            # Drop settled windows older than 30 min so memory stays flat.
            cutoff = now - 1800
            for slug, w in list(tracked.items()):
                if w.settled and w.end_ts < cutoff:
                    del tracked[slug]

            if once:
                return 0
            await asyncio.sleep(5)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", default="doge,btc", help="comma-separated 5m assets")
    p.add_argument("--size", type=float, default=10.0, help="shares quoted per leg")
    p.add_argument(
        "--min-edge",
        type=float,
        default=DEFAULT_MIN_EDGE,
        help="minimum dollars per completed pair before quoting",
    )
    p.add_argument("--once", action="store_true", help="single cycle then exit")
    a = p.parse_args()
    assets = [x.strip().lower() for x in a.assets.split(",") if x.strip()]
    try:
        return asyncio.run(run(assets, a.size, a.min_edge, a.once))
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
