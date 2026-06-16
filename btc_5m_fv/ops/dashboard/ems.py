"""EMS view orchestrator (#37).

Loads journal data once and dispatches to the panel renderers in
``btc_5m_fv/ops/dashboard/panels/``. Each panel is a pure (data, context) →
HTML function — see the panels package for the panel-specific logic.

This file deliberately stays small: it's the wiring layer between the
SQLite read layer (``panels/_data.py``) and the per-panel renderers.
"""
from __future__ import annotations

import config as _config
from db import get_config

from btc_5m_fv.ops.dashboard.panels import _data as data
from btc_5m_fv.ops.dashboard.panels import (
    blotter,
    controls,
    decision_engine,
    guardrails,
    market,
    performance,
    ribbon,
    strategy,
    tca,
)


async def ems_html() -> str:
    """Render the full EMS view as one HTML string.

    Same public signature and output contract as before the panel split —
    ``app.py`` consumes this directly.
    """
    style = _config.BTC_EXIT_STYLE
    mode = await get_config("btc_bot.requested_mode", _config.BTC_BOT_MODE) or "paper"
    state = await get_config("btc_bot.state", "stopped") or "stopped"
    session_start = await get_config("btc_bot.session_start", None)
    paused = (await get_config("btc_bot.auto_paused", "0")) == "1"
    pause_reason = await get_config("btc_bot.auto_pause_reason", "") or ""

    # Split counters (issue #67): show LIVE vs PAPER P&L distinctly so the
    # ribbon and LOSS HALT panel never blend real-money and study results.
    # Falls back through the pre-split #64 key, then the legacy #20 keys.
    live_pnl = float(
        await get_config("btc_risk.live_realized_pnl")
        or await get_config("btc_risk.daily_realized_pnl")
        or await get_config("btc_live.daily_realized_pnl")
        or 0
    )
    paper_pnl = float(await get_config("btc_risk.paper_realized_pnl") or 0)
    # Combined PnL for the ribbon's headline number. The loss-halt decision uses
    # the per-mode leg (live in live, paper in paper) — see RiskGate.halt_pnl (#76).
    day_pnl = live_pnl + paper_pnl
    day_notional = float(
        await get_config("btc_risk.daily_buy_notional")
        or await get_config("btc_live.daily_buy_notional")
        or 0
    )
    bot_detail = await get_config("btc_bot.detail", "") or ""

    # ---- one-shot data load ----
    tick = await data.latest_tick()
    recent_ticks = await data.recent_decisions(limit=10)
    closed = await data.closed(style, None, limit=40)
    closed_session = await data.closed(style, session_start)  # this run, for the ribbon
    open_pos = await data.open_positions(style)
    # Per-mode mini-cards query each mode's own recent-40 window so a low-volume
    # mode (live) is never crowded out by a high-volume mode (paper).
    closed_live = await data.closed(style, None, limit=40, mode="live")
    closed_paper = await data.closed(style, None, limit=40, mode="paper")
    last_live_at = await data.last_live_order_at()
    spread = await data.avg_spread()
    blocked_today = await data.recent_blocked(limit=5)
    submitted_count, submitted_notional = await data.today_submitted_summary()

    perf = data.performance(closed)
    perf_live = data.performance(closed_live)
    perf_paper = data.performance(closed_paper)
    is_live = mode == "live"

    # ---- panels ----
    from btc_5m_fv.execution.gate import (
        get_loss_halt_bypass,
        get_runtime_max_trade_usd,
    )
    bypass_loss_halt = await get_loss_halt_bypass()
    # Operator runtime per-trade cap (#50): None when unset → bot uses the env
    # default. Used by the CONTROLS card and the STRATEGY sizing line so the UI
    # reflects the value the loop is actually enforcing this tick.
    max_trade_current = await get_runtime_max_trade_usd()
    max_trade_env = (
        _config.BTC_LIVE_MAX_TRADE_USD if is_live else _config.BTC_PAPER_MAX_TRADE_USD
    )
    max_trade_effective = (
        max_trade_current if max_trade_current is not None else max_trade_env
    )

    # Active params shape the decision-engine gate eval — same thresholds the
    # live loop uses, so the gate column never lies.
    from btc_bot import params as _params
    active = _params.load_active()

    class _GateParams:
        entry_edge_min = active.entry_edge_min
        entry_edge_max = active.entry_edge_max
        min_confidence = active.min_confidence
        entry_min_remaining_seconds = active.min_remaining_seconds
        min_entry_price = active.min_entry_price
        max_entry_price = active.max_entry_price

    ribbon_html = ribbon.render(
        mode=mode,
        state=state,
        session_start=session_start,
        paused=paused,
        pause_reason=pause_reason,
        live_pnl=live_pnl,
        paper_pnl=paper_pnl,
        day_pnl=day_pnl,
        open_pos=open_pos,
        closed_session=closed_session,
        tick=tick,
        last_live_at=last_live_at,
    )
    guardrails_html = guardrails.render(
        day_spend=day_notional,
        bankroll_cap=_config.BTC_TRADE_BANKROLL_CAP_USD,
        submitted_count=submitted_count,
        submitted_notional=submitted_notional,
        day_pnl=day_pnl,
        live_pnl=live_pnl,
        paper_pnl=paper_pnl,
        loss_halt_usd=_config.BTC_TRADE_DAILY_LOSS_HALT_USD,
        state=state,
        bot_detail=bot_detail,
        session_start=session_start,
        paused=paused,
        pause_reason=pause_reason,
        blocked=blocked_today,
        mode=mode,
        bypass_loss_halt=bypass_loss_halt,
    )
    controls_html = controls.render(
        max_trade_current=max_trade_current,
        max_trade_env=max_trade_env,
        min_trade=_config.BTC_PAPER_MIN_TRADE_USD,
    )
    strategy_html = strategy.render(
        style=style,
        is_live=is_live,
        paused=paused,
        pause_reason=pause_reason,
        max_trade=max_trade_effective,
    )
    market_html = market.render(tick)
    decision_html = decision_engine.render(
        tick, _GateParams(), recent_ticks, paused, pause_reason
    )
    performance_html = performance.render(
        style=style, perf=perf, perf_live=perf_live, perf_paper=perf_paper
    )
    tca_html = tca.render(perf=perf, spread=spread)
    blotter_html = blotter.render(closed=closed, open_pos=open_pos)

    return (
        "<div class='ems'>"
        + ribbon_html
        + "<div class='ems-grid'>"
        + guardrails_html
        + controls_html
        + strategy_html
        + market_html
        + decision_html
        + performance_html
        + tca_html
        + blotter_html
        + "</div></div>"
    )
