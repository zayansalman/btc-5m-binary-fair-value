"""Adaptive risk controller (#36): edge-decay auto-pause + calibration.

Complements the hard daily-loss halt. The hard halt bounds a single day's loss;
this layer detects when the STRATEGY's EDGE has decayed — rolling expectancy
over the last N closed trades turning negative — and pauses NEW entries before
losses pile up. The pause is STICKY: it stays until an operator reviews and
clears it (``tools/clear_auto_pause.py``), because auto-resuming into a losing
regime is how an edge bleeds out.

Reads only the existing journal — no schema change. For each closed position of
the active style the model's predicted P(win) is reconstructed as
``edge + entry_price`` (edge = model P(side wins) − executable ask) and the
outcome as ``realized_pnl_usd > 0``, giving a Brier calibration score.
"""

from __future__ import annotations

from typing import Any

import config as _config
from db import connect, get_config, notify, set_config
from logging_setup import get_logger

log = get_logger("adaptive")

_PAUSE_KEY = "btc_bot.auto_paused"
_PAUSE_REASON_KEY = "btc_bot.auto_pause_reason"
_SESSION_START_KEY = "btc_bot.session_start"


async def rolling_performance(
    window: int, style: str, since: str | None = None
) -> dict[str, Any]:
    """Metrics over the last ``window`` closed clob trades of ``style``.

    ``since`` (an ISO ``opened_at``) scopes to the current run session so the
    live safety layer judges THIS deployment's edge, not stale trades from an
    earlier strategy config in the same journal.
    """
    sql = (
        "SELECT realized_pnl_usd, notional_usd, edge, entry_price "
        "FROM btc_paper_positions "
        "WHERE state='closed' AND quote_source='clob' AND strategy_style=?"
    )
    params: list[Any] = [style]
    if since:
        sql += " AND opened_at >= ?"
        params.append(since)
    sql += " ORDER BY position_id DESC LIMIT ?"
    params.append(window)
    async with connect() as db:
        async with db.execute(sql, params) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    n = len(rows)
    if n == 0:
        return {"n": 0, "roi": 0.0, "win_rate": 0.0, "brier": None, "pnl": 0.0}

    pnl = sum(r["realized_pnl_usd"] or 0.0 for r in rows)
    notional = sum(r["notional_usd"] or 0.0 for r in rows) or 1.0
    wins = sum(1 for r in rows if (r["realized_pnl_usd"] or 0.0) > 0)

    briers: list[float] = []
    for r in rows:
        if r["edge"] is not None and r["entry_price"] is not None:
            p = max(0.0, min(1.0, r["edge"] + r["entry_price"]))
            outcome = 1.0 if (r["realized_pnl_usd"] or 0.0) > 0 else 0.0
            briers.append((p - outcome) ** 2)
    brier = sum(briers) / len(briers) if briers else None

    return {
        "n": n,
        "roi": pnl / notional,
        "win_rate": wins / n,
        "brier": brier,
        "pnl": pnl,
    }


def should_pause(perf: dict[str, Any], min_trades: int, min_roi: float) -> tuple[bool, str]:
    """Pure decision: pause when rolling ROI is below the floor after warm-up."""
    if perf["n"] < min_trades:
        return False, f"warming up ({perf['n']}/{min_trades} trades)"
    if perf["roi"] < min_roi:
        return True, (
            f"rolling ROI {perf['roi'] * 100:+.1f}% over last {perf['n']} trades "
            f"below floor {min_roi * 100:.0f}% (pnl ${perf['pnl']:+.2f})"
        )
    return False, f"rolling ROI {perf['roi'] * 100:+.1f}% over last {perf['n']} OK"


async def is_paused() -> tuple[bool, str]:
    """Current sticky pause state, without re-evaluating performance."""
    if (await get_config(_PAUSE_KEY, "0")) == "1":
        return True, (await get_config(_PAUSE_REASON_KEY, "")) or "auto-paused"
    return False, ""


async def evaluate_and_maybe_pause() -> tuple[bool, str]:
    """Entry-time gate. Returns (paused, reason). Sticky once tripped."""
    paused, reason = await is_paused()
    if paused:
        return True, reason
    if not _config.BTC_AUTO_PAUSE_ENABLED:
        return False, "auto-pause disabled"

    since = await get_config(_SESSION_START_KEY, None)
    perf = await rolling_performance(
        _config.BTC_AUTO_PAUSE_WINDOW, _config.BTC_EXIT_STYLE, since=since
    )
    pause, reason = should_pause(
        perf, _config.BTC_AUTO_PAUSE_MIN_TRADES, _config.BTC_AUTO_PAUSE_MIN_ROI
    )
    if pause:
        await set_config(_PAUSE_KEY, "1")
        await set_config(_PAUSE_REASON_KEY, reason)
        log.warning("adaptive.auto_paused", reason=reason)
        await notify(
            "btc_auto_paused",
            f"Auto-paused: {reason}. Review and clear to resume.",
            {"brier": perf.get("brier"), "win_rate": perf.get("win_rate")},
        )
    return pause, reason


async def clear_auto_pause() -> None:
    """Operator action: resume entries after reviewing the pause."""
    await set_config(_PAUSE_KEY, "0")
    await set_config(_PAUSE_REASON_KEY, "")
    log.info("adaptive.auto_pause_cleared")
    await notify("btc_auto_pause_cleared", "Auto-pause cleared; entries resume.")
