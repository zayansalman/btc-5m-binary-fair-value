"""EMS-style analytics + rendering for the dashboard (#37).

Turns the SQLite journal into an execution-management view: status ribbon,
strategy, live market, performance/alpha, TCA, and the trade blotter. Pure
read-over-SQLite with inline SVG charts (no JS charting dependency). Does not
touch the trading loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

import config as _config
from db import connect, get_config

# Bloomberg-EMS palette: amber accent, convention green/red, dim slate.
ACCENT = "#ffa53c"
GREEN = "#34d399"
RED = "#ff5d6c"
DIM = "#6b7689"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _money(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "—"
    p = "+" if signed and v > 0 else ""
    return f"{p}${v:,.2f}"


def _pct(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "—"
    p = "+" if signed and v > 0 else ""
    return f"{p}{v * 100:.1f}%"


def _cls(v: float | None) -> str:
    if v is None or abs(v) < 1e-9:
        return "flat"
    return "up" if v > 0 else "down"


def _ago(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        p = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if p.tzinfo is None:
            p = p.replace(tzinfo=UTC)
    except ValueError:
        return ts
    a = max(0, int((datetime.now(UTC) - p).total_seconds()))
    if a < 60:
        return f"{a}s"
    if a < 3600:
        return f"{a // 60}m"
    return f"{a // 3600}h{(a % 3600) // 60}m"


# ---------------------------------------------------------------------------
# Inline SVG charts
# ---------------------------------------------------------------------------


def _svg_equity(curve: list[float], w: int = 320, h: int = 84) -> str:
    """Cumulative-PnL equity curve as an SVG area+line; zero baseline marked."""
    if len(curve) < 2:
        return f"<div class='chart-empty' style='height:{h}px'>awaiting trades</div>"
    lo, hi = min(curve + [0.0]), max(curve + [0.0])
    span = (hi - lo) or 1.0
    pad = 6
    n = len(curve)

    def x(i: int) -> float:
        return pad + i * (w - 2 * pad) / (n - 1)

    def y(v: float) -> float:
        return pad + (hi - v) * (h - 2 * pad) / span

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(curve))
    zero_y = y(0.0)
    last = curve[-1]
    stroke = GREEN if last >= 0 else RED
    area = f"{pad},{y(0.0):.1f} " + pts + f" {x(n - 1):.1f},{y(0.0):.1f}"
    return (
        f"<svg viewBox='0 0 {w} {h}' class='spark' preserveAspectRatio='none'>"
        f"<polygon points='{area}' fill='{stroke}' opacity='0.10'/>"
        f"<line x1='{pad}' y1='{zero_y:.1f}' x2='{w - pad}' y2='{zero_y:.1f}' "
        f"stroke='{DIM}' stroke-width='0.5' stroke-dasharray='2 3'/>"
        f"<polyline points='{pts}' fill='none' stroke='{stroke}' stroke-width='1.6'/>"
        f"<circle cx='{x(n - 1):.1f}' cy='{y(last):.1f}' r='2.4' fill='{stroke}'/>"
        "</svg>"
    )


def _svg_calibration(buckets: list[tuple[float, float, int]], s: int = 120) -> str:
    """Reliability: predicted (x) vs realized win-rate (y) vs the diagonal."""
    pad = 10
    dots = []
    for pred, real, n in buckets:
        cx = pad + pred * (s - 2 * pad)
        cy = s - pad - real * (s - 2 * pad)
        r = 1.8 + min(n, 12) * 0.35
        dots.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{r:.1f}' fill='{ACCENT}' opacity='0.85'/>")
    if not dots:
        return f"<div class='chart-empty' style='height:{s}px'>no settled trades</div>"
    return (
        f"<svg viewBox='0 0 {s} {s}' class='calib'>"
        f"<rect x='{pad}' y='{pad}' width='{s - 2 * pad}' height='{s - 2 * pad}' "
        f"fill='none' stroke='{DIM}' stroke-width='0.5' opacity='0.4'/>"
        f"<line x1='{pad}' y1='{s - pad}' x2='{s - pad}' y2='{pad}' "
        f"stroke='{DIM}' stroke-width='0.6' stroke-dasharray='3 3'/>"
        + "".join(dots)
        + "</svg>"
    )


def _gauge(prob: float | None) -> str:
    """Horizontal fair-up probability bar (Up green / Down red split)."""
    if prob is None:
        return "<div class='gauge'><div class='gauge-empty'>no signal</div></div>"
    up = max(0.0, min(1.0, prob)) * 100
    return (
        "<div class='gauge'>"
        f"<div class='gauge-fill' style='width:{up:.0f}%'></div>"
        f"<div class='gauge-tick' style='left:50%'></div>"
        f"<span class='gauge-lbl'>fair Up {up:.0f}%</span>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


async def _latest_tick() -> dict[str, Any] | None:
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM btc_paper_ticks ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _closed(
    style: str, since: str | None, limit: int | None = None
) -> list[dict[str, Any]]:
    """Closed clob trades of ``style`` in chronological order.

    ``limit`` returns the most recent N (still oldest-first) — a rolling
    window that reflects the CURRENT strategy regime rather than blending in
    older experimental configs.
    """
    base = (
        "SELECT * FROM btc_paper_positions WHERE state='closed' "
        "AND quote_source='clob' AND strategy_style=?"
    )
    params: list[Any] = [style]
    if since:
        base += " AND opened_at >= ?"
        params.append(since)
    if limit:
        base += " ORDER BY position_id DESC LIMIT ?"
        params.append(limit)
    else:
        base += " ORDER BY position_id ASC"
    async with connect() as db:
        async with db.execute(base, params) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return list(reversed(rows)) if limit else rows


async def _open_positions(style: str) -> list[dict[str, Any]]:
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM btc_paper_positions WHERE state='open' "
            "AND strategy_style=? ORDER BY position_id DESC",
            (style,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _avg_spread() -> float | None:
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


def _performance(closed: list[dict[str, Any]]) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _stat(label: str, value: str, cls: str = "", sub: str = "") -> str:
    return (
        f"<div class='stat'><div class='stat-l'>{escape(label)}</div>"
        f"<div class='stat-v {cls}'>{value}</div>"
        + (f"<div class='stat-s'>{escape(sub)}</div>" if sub else "")
        + "</div>"
    )


async def ems_html() -> str:
    style = _config.BTC_EXIT_STYLE
    mode = await get_config("btc_bot.requested_mode", _config.BTC_BOT_MODE) or "paper"
    state = await get_config("btc_bot.state", "stopped") or "stopped"
    session_start = await get_config("btc_bot.session_start", None)
    paused = (await get_config("btc_bot.auto_paused", "0")) == "1"
    pause_reason = await get_config("btc_bot.auto_pause_reason", "") or ""
    day_pnl = float(await get_config("btc_live.daily_realized_pnl", "0") or 0)
    day_notional = float(await get_config("btc_live.daily_buy_notional", "0") or 0)

    tick = await _latest_tick()
    # Rolling recent window = the CURRENT regime (not blended with older
    # experimental configs that lived in the same journal).
    closed = await _closed(style, None, limit=40)
    closed_session = await _closed(style, session_start)  # this run, for the ribbon
    open_pos = await _open_positions(style)
    perf = _performance(closed)
    session_pnl = sum(c["realized_pnl_usd"] or 0.0 for c in closed_session)
    spread = await _avg_spread()
    is_live = mode == "live"

    # ---- ribbon ----
    halt = _config.BTC_LIVE_DAILY_LOSS_HALT_USD
    headroom = halt + min(0.0, day_pnl)  # remaining loss budget
    kill_armed = Path(str(_config.KILL_SWITCH_PATH)).exists()
    mode_pill = (
        f"<span class='pill {'live' if is_live else 'paper'}'>{'● LIVE' if is_live else 'PAPER'}</span>"
    )
    run_pill = (
        f"<span class='pill {'on' if state == 'running' else 'off'}'>{state.upper()}</span>"
    )
    feeds = ""
    if tick:
        fs = tick.get("feed_source", "") or ""
        ok = "chainlink" in fs
        feeds = (
            f"<span class='feed {'on' if ok else 'warn'}'>CHAINLINK</span>"
            f"<span class='feed {'on' if 'clob' in fs else 'warn'}'>CLOB</span>"
        )
    pause_chip = (
        f"<span class='pill warn' title='{escape(pause_reason)}'>⏸ AUTO-PAUSED</span>"
        if paused
        else ""
    )
    kill_chip = "<span class='pill live'>KILL ARMED</span>" if kill_armed else ""

    ribbon = (
        "<div class='ribbon'>"
        f"<div class='ribbon-id'>BTC·5M FAIR-VALUE <b>EMS</b>{mode_pill}{run_pill}{pause_chip}{kill_chip}</div>"
        "<div class='ribbon-stats'>"
        f"{_stat('Equity Δ (session)', _money(session_pnl, True) if closed_session else '—', _cls(session_pnl))}"
        f"{_stat('Day P&L', _money(day_pnl, True), _cls(day_pnl))}"
        f"{_stat('Open Risk', _money(sum(p['notional_usd'] or 0 for p in open_pos)), '', f'{len(open_pos)} pos')}"
        f"{_stat('Halt Headroom', _money(headroom), 'down' if headroom < halt * 0.4 else '')}"
        f"<div class='feeds'>{feeds}</div>"
        f"{_stat('Uptime', _ago(session_start))}"
        "</div></div>"
    )

    # ---- strategy panel ----
    strat = (
        "<section class='card'><div class='card-h'>STRATEGY</div>"
        "<div class='kv'>"
        f"<div><span>Model</span><b>Fair-Value · Settle</b></div>"
        f"<div><span>Style</span><b>{escape(style)} (1 entry/window, hold→resolution)</b></div>"
        f"<div><span>Edge band</span><b>{_config.BTC_PAPER_ENTRY_EDGE_MIN:.3f} – {_config.BTC_PAPER_ENTRY_EDGE_MAX:.3f}</b></div>"
        f"<div><span>Entry floor</span><b>≥ {_config.BTC_PAPER_MIN_ENTRY_PRICE:.2f} (favorites)</b></div>"
        f"<div><span>Sizing</span><b>${_config.BTC_LIVE_MAX_TRADE_USD if is_live else _config.BTC_PAPER_MAX_TRADE_USD:.0f}/clip · 1 pos max</b></div>"
        f"<div><span>Settlement</span><b>Chainlink BTC/USD · ≥ ⇒ Up</b></div>"
        f"<div><span>Auto-pause</span><b class='{'down' if paused else 'up'}'>{'PAUSED — ' + escape(pause_reason[:40]) if paused else 'armed (edge-decay)'}</b></div>"
        "</div></section>"
    )

    # ---- live market panel ----
    if tick:
        spot = tick.get("spot_price") or 0
        ref = tick.get("reference_price") or 0
        basis = spot - ref
        rem = tick.get("remaining_seconds") or 0
        edge = tick.get("edge")
        market = (
            "<section class='card'><div class='card-h'>LIVE MARKET"
            f"<span class='win'>{escape((tick.get('window_slug') or '').replace('btc-updown-5m-', '#'))} · {rem}s</span></div>"
            f"{_gauge(tick.get('fair_up_prob'))}"
            "<div class='book'>"
            "<div class='book-side up'><div class='bk-l'>UP</div>"
            f"<div class='bk-px'>{_bk(tick.get('up_best_bid'))} / {_bk(tick.get('up_best_ask'))}</div></div>"
            "<div class='book-side down'><div class='bk-l'>DOWN</div>"
            f"<div class='bk-px'>{_bk(tick.get('down_best_bid'))} / {_bk(tick.get('down_best_ask'))}</div></div>"
            "</div>"
            "<div class='kv tight'>"
            f"<div><span>BTC spot</span><b>${spot:,.2f}</b></div>"
            f"<div><span>Window ref</span><b>${ref:,.2f}</b></div>"
            f"<div><span>Basis</span><b class='{_cls(basis)}'>{basis:+.2f}</b></div>"
            f"<div><span>Edge</span><b class='{_cls(edge)}'>{('%+.3f' % edge) if edge is not None else '—'}</b></div>"
            "</div>"
            f"<div class='decision'>{escape((tick.get('reason') or 'idle'))}</div>"
            "</section>"
        )
    else:
        market = "<section class='card'><div class='card-h'>LIVE MARKET</div><div class='chart-empty'>no ticks yet</div></section>"

    # ---- performance / alpha panel ----
    if perf["n"]:
        wl = f"{perf['wins']}W / {perf['losses']}L"
        pf_s = f"{perf['profit_factor']:.2f}" if perf["profit_factor"] else "∞"
        perf_panel = (
            "<section class='card'><div class='card-h'>PERFORMANCE / ALPHA"
            f"<span class='win'>recent {perf['n']} · {style}</span></div>"
            f"<div class='equity'>{_svg_equity(perf['equity'])}</div>"
            "<div class='statrow'>"
            f"{_stat('Net P&L', _money(perf['pnl'], True), _cls(perf['pnl']))}"
            f"{_stat('ROI', _pct(perf['roi'], True), _cls(perf['roi']))}"
            f"{_stat('Win rate', _pct(perf['win_rate']), '', wl)}"
            f"{_stat('Expectancy', _money(perf['expectancy'], True), _cls(perf['expectancy']), 'per trade')}"
            f"{_stat('Profit factor', pf_s, _cls((perf['profit_factor'] or 2) - 1))}"
            f"{_stat('Max DD', _money(perf['max_dd']), 'down' if perf['max_dd'] < 0 else '')}"
            "</div></section>"
        )
    else:
        perf_panel = "<section class='card'><div class='card-h'>PERFORMANCE / ALPHA</div><div class='chart-empty'>awaiting first settled trade this session</div></section>"

    # ---- TCA panel ----
    half = (spread / 2) if spread else None
    capture = None
    if perf.get("signaled_edge") and perf["n"]:
        capture = perf["roi"] / perf["signaled_edge"] if perf["signaled_edge"] else None
    spread_s = f"{spread * 100:.1f}¢" if spread else "—"
    half_s = f"-{half * 100:.1f}¢" if half else "—"
    capture_s = f"{capture * 100:.0f}%" if capture is not None else "—"
    brier_s = f"{perf['brier']:.3f}" if perf.get("brier") is not None else "—"
    tca = (
        "<section class='card'><div class='card-h'>TCA · COST &amp; CAPTURE</div>"
        "<div class='kv tight'>"
        f"<div><span>Quoted spread</span><b>{spread_s}</b></div>"
        f"<div><span>Taker half-spread</span><b class='down'>{half_s}</b></div>"
        f"<div><span>Signaled edge</span><b class='up'>{_pct(perf.get('signaled_edge'))}</b></div>"
        f"<div><span>Realized ROI</span><b class='{_cls(perf.get('roi'))}'>{_pct(perf.get('roi'), True)}</b></div>"
        f"<div><span>Edge capture</span><b>{capture_s}</b></div>"
        f"<div><span>Brier (calib.)</span><b>{brier_s}</b></div>"
        "</div>"
        f"<div class='calib-wrap'><div class='calib-cap'>calibration · predicted → realized</div>{_svg_calibration(perf.get('cal_buckets', []))}</div>"
        "</section>"
    )

    # ---- blotter ----
    rows = ""
    recent = list(reversed(closed))[:12]
    for c in recent:
        p = c["realized_pnl_usd"] or 0.0
        rows += (
            "<tr>"
            f"<td class='mono dim'>{_ago(c.get('closed_at'))}</td>"
            f"<td><span class='tag {c['side'].lower()}'>{escape(c['side'])}</span></td>"
            f"<td class='mono'>{(c['entry_price'] or 0):.3f}</td>"
            f"<td class='mono'>{(c['exit_price'] if c['exit_price'] is not None else 0):.2f}</td>"
            f"<td class='mono'>{_money(c['notional_usd'])}</td>"
            f"<td class='mono {_cls(p)}'>{_money(p, True)}</td>"
            f"<td class='dim'>{escape(str(c.get('exit_reason') or ''))}</td>"
            "</tr>"
        )
    for c in open_pos:
        rows = (
            "<tr class='live-row'>"
            f"<td class='mono dim'>now</td>"
            f"<td><span class='tag {c['side'].lower()}'>{escape(c['side'])}</span></td>"
            f"<td class='mono'>{(c['entry_price'] or 0):.3f}</td>"
            f"<td class='mono'>—</td>"
            f"<td class='mono'>{_money(c['notional_usd'])}</td>"
            f"<td class='mono flat'>OPEN</td>"
            f"<td class='dim'>holding→resolution</td>"
            "</tr>"
        ) + rows
    blotter = (
        "<section class='card wide'><div class='card-h'>TRADE BLOTTER</div>"
        "<table class='blotter'><thead><tr>"
        "<th>age</th><th>side</th><th>entry</th><th>exit</th><th>size</th><th>P&L</th><th>reason</th>"
        "</tr></thead><tbody>"
        + (rows or "<tr><td colspan='7' class='dim' style='text-align:center;padding:18px'>no trades yet — the strategy is selective</td></tr>")
        + "</tbody></table></section>"
    )

    return (
        "<div class='ems'>"
        + ribbon
        + "<div class='ems-grid'>"
        + strat
        + market
        + perf_panel
        + tca
        + blotter
        + "</div></div>"
    )


def _bk(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "—"
