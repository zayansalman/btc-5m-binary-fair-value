"""Read-only SQLite loaders for dashboard panels.

Each function returns plain dicts / scalars / dataclass-like dicts that the
panel renderers transform into HTML. Keeping data access here (and out of
panels/*) lets us swap the journal source or add caching in one place.
"""
from __future__ import annotations

from typing import Any

from db import connect


async def latest_tick() -> dict[str, Any] | None:
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM btc_paper_ticks ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def last_live_order_at() -> str | None:
    """ISO timestamp of the most recent action recorded in btc_live_orders.

    A live bot that has lost the ability to actually place orders looks fine
    in the tick journal (the loop still polls Chainlink and CLOB) — the only
    objective signal of "we are still trading for real" is this column.
    """
    async with connect() as db:
        async with db.execute(
            "SELECT MAX(created_at) AS last_at FROM btc_live_orders"
        ) as cur:
            row = await cur.fetchone()
    return (row["last_at"] if row else None) if row is not None else None


async def closed(
    style: str,
    since: str | None,
    limit: int | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """Closed clob trades of ``style`` in chronological order.

    ``limit`` returns the most recent N (still oldest-first) — a rolling
    window that reflects the CURRENT strategy regime rather than blending in
    older experimental configs.

    ``mode`` ('live'|'paper') scopes the window to one execution mode. The
    PERFORMANCE/ALPHA panel uses this so a mode with few trades (live) never
    gets pushed out of view by a chatty mode (paper) when both share the same
    recent-N window.
    """
    base = (
        "SELECT * FROM btc_paper_positions WHERE state='closed' "
        "AND quote_source='clob' AND strategy_style=?"
    )
    params: list[Any] = [style]
    if since:
        base += " AND opened_at >= ?"
        params.append(since)
    if mode is not None:
        base += " AND mode = ?"
        params.append(mode)
    if limit:
        base += " ORDER BY position_id DESC LIMIT ?"
        params.append(limit)
    else:
        base += " ORDER BY position_id ASC"
    async with connect() as db:
        async with db.execute(base, params) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return list(reversed(rows)) if limit else rows


async def open_positions(style: str) -> list[dict[str, Any]]:
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM btc_paper_positions WHERE state='open' "
            "AND strategy_style=? ORDER BY position_id DESC",
            (style,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def avg_spread() -> float | None:
    """Average quoted spread (Up+Down) over recent ticks — the taker cost."""
    async with connect() as db:
        async with db.execute(
            "SELECT up_best_bid, up_best_ask, down_best_bid, down_best_ask "
            "FROM btc_paper_ticks WHERE up_best_ask IS NOT NULL "
            "ORDER BY id DESC LIMIT 60"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    spreads = []
    for r in rows:
        for b, a in (("up_best_bid", "up_best_ask"), ("down_best_bid", "down_best_ask")):
            if r[b] is not None and r[a] is not None and r[a] > r[b]:
                spreads.append(r[a] - r[b])
    return sum(spreads) / len(spreads) if spreads else None


async def recent_decisions(limit: int = 10) -> list[dict[str, Any]]:
    """Last N tick decisions, newest first — feeds the decision-stream tail."""
    async with connect() as db:
        async with db.execute(
            "SELECT created_at, spot_price, reference_price, fair_up_prob, edge, "
            "up_best_ask, down_best_ask, signal_side, notional_usd, confidence, "
            "reason, remaining_seconds, feed_source "
            "FROM btc_paper_ticks ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def recent_blocked(limit: int = 5) -> list[dict[str, Any]]:
    """Last N BLOCKED order intents from TODAY, newest first.

    Surfaces silent stop conditions in the dashboard — any time a risk gate
    rejects an entry, the operator sees the reason without grepping logs.
    Paper-mode blocks (#64) appear here too so the operator can study what
    live would have rejected, tagged with mode='paper' so they are visually
    distinguishable from real live rejections.
    """
    async with connect() as db:
        async with db.execute(
            "SELECT created_at, intent, notional_usd, error, mode "
            "FROM btc_live_orders "
            "WHERE status='BLOCKED' "
            "AND date(created_at)=date('now') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def today_submitted_summary() -> tuple[int, float]:
    """Count + summed notional of ENTRY orders that reached the network today.

    'SUBMITTED' is the journal status for orders that passed every risk gate
    and were posted to the CLOB (whether or not they fully filled). Returned
    so the guardrails panel can show 'N entries · $X submitted' alongside the
    persisted daily_buy_notional spend counter (which only counts matched fills).
    """
    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(notional_usd), 0.0) AS total "
            "FROM btc_live_orders "
            "WHERE intent='ENTRY' AND status='SUBMITTED' "
            "AND date(created_at)=date('now')"
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return 0, 0.0
    return int(row["n"] or 0), float(row["total"] or 0.0)


def performance(closed: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(closed)
    if n == 0:
        return {"n": 0}
    pnl = [c["realized_pnl_usd"] or 0.0 for c in closed]
    notional = sum(c["notional_usd"] or 0.0 for c in closed) or 1.0
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    equity, run = [], 0.0
    for p in pnl:
        run += p
        equity.append(run)
    # calibration (Brier + reliability buckets)
    cal_pts, briers = [], []
    bucket: dict[int, list[float]] = {}
    for c in closed:
        if c["edge"] is not None and c["entry_price"] is not None:
            mp = max(0.0, min(1.0, c["edge"] + c["entry_price"]))
            outcome = 1.0 if (c["realized_pnl_usd"] or 0.0) > 0 else 0.0
            briers.append((mp - outcome) ** 2)
            bucket.setdefault(int(mp * 5), []).append((mp, outcome))
    cal_buckets = []
    for vals in bucket.values():
        pred = sum(v[0] for v in vals) / len(vals)
        real = sum(v[1] for v in vals) / len(vals)
        cal_buckets.append((pred, real, len(vals)))
    signaled = [c["edge"] for c in closed if c["edge"] is not None]
    return {
        "n": n,
        "pnl": sum(pnl),
        "roi": sum(pnl) / notional,
        "win_rate": len(wins) / n,
        "wins": len(wins),
        "losses": len(losses),
        "expectancy": sum(pnl) / n,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else None,
        "equity": equity,
        "max_dd": min(equity + [0.0]) if equity else 0.0,
        "brier": (sum(briers) / len(briers)) if briers else None,
        "cal_buckets": cal_buckets,
        "signaled_edge": (sum(signaled) / len(signaled)) if signaled else None,
        "best": max(pnl),
        "worst": min(pnl),
    }
