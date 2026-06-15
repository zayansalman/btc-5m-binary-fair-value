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
from btc_bot import calibration as _calibration
from btc_bot import params as _params
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


async def _last_live_order_at() -> str | None:
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


def _parse_feed_source(raw: Any) -> dict[str, str]:
    """Parse 'spot=...;ref=...;vol=...;quotes=...' into a dict.

    The trading loop writes this string to every tick; parsing it lets the
    ribbon show per-source liveness instead of a blunt 'CHAINLINK' chip that
    is on whenever the substring matches.
    """
    if not isinstance(raw, str):
        return {}
    out: dict[str, str] = {}
    for chunk in raw.split(";"):
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _tick_age_seconds(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - parsed).total_seconds()))


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


async def _recent_decisions(limit: int = 10) -> list[dict[str, Any]]:
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


async def _recent_blocked(limit: int = 5) -> list[dict[str, Any]]:
    """Last N BLOCKED order intents from TODAY, newest first.

    Surfaces silent stop conditions in the dashboard — any time a risk gate
    rejects an entry, the operator sees the reason without grepping logs.
    """
    async with connect() as db:
        async with db.execute(
            "SELECT created_at, intent, notional_usd, error "
            "FROM btc_live_orders "
            "WHERE status='BLOCKED' "
            "AND date(created_at)=date('now') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _today_submitted_summary() -> tuple[int, float]:
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


def _guardrails_panel(
    *,
    day_spend: float,
    bankroll_cap: float | None,
    submitted_count: int,
    submitted_notional: float,
    day_pnl: float,
    loss_halt_usd: float,
    state: str,
    bot_detail: str,
    session_start: str | None,
    paused: bool,
    pause_reason: str,
    blocked: list[dict[str, Any]],
) -> str:
    """Surface every silent stop condition: daily spend, loss-halt headroom,
    bot state + last loop error, and the tail of recent BLOCKED entries.

    The ribbon shows aggregate session metrics; this panel shows the things
    that can silently freeze trading without changing the run state. If a
    risk gate rejects every entry — or the loop crashed and is stuck — the
    operator sees it here instead of grepping logs.
    """
    # ── DAILY SPEND ─────────────────────────────────────────────────────
    if bankroll_cap is not None and bankroll_cap > 0:
        pct = day_spend / bankroll_cap
        cap_str = f"${bankroll_cap:,.2f}"
        cap_cls = "down" if pct >= 0.95 else ("up" if pct < 0.6 else "")
        headroom_line = (
            f"<div><span>Cap headroom</span>"
            f"<b class='mono {cap_cls}'>${max(0.0, bankroll_cap - day_spend):,.2f}</b></div>"
        )
    else:
        cap_str = "disabled"
        cap_cls = "dim"
        headroom_line = ""
    spend_col = (
        "<div class='de-col'>"
        "<div class='de-h'>DAILY SPEND (today, UTC)</div>"
        "<div class='de-kv'>"
        f"<div><span>Filled notional</span><b class='mono'>${day_spend:,.2f}</b></div>"
        f"<div><span>Cap</span><b class='mono {cap_cls}'>{escape(cap_str)}</b></div>"
        + headroom_line
        + f"<div><span>Submitted today</span><b class='mono'>{submitted_count} entries · ${submitted_notional:,.2f}</b></div>"
        "</div></div>"
    )

    # ── LOSS HALT ───────────────────────────────────────────────────────
    halted = day_pnl <= -loss_halt_usd
    headroom = loss_halt_usd + min(0.0, day_pnl)
    halt_pill = (
        "<span class='pill warn'>HALTED</span>"
        if halted
        else "<span class='pill on'>OK</span>"
    )
    headroom_cls = "down" if headroom < loss_halt_usd * 0.4 else ""
    halt_col = (
        "<div class='de-col'>"
        "<div class='de-h'>LOSS HALT</div>"
        "<div class='de-kv'>"
        f"<div><span>Realized P&amp;L</span><b class='mono {_cls(day_pnl)}'>{_money(day_pnl, True)}</b></div>"
        f"<div><span>Halt threshold</span><b class='mono'>−${loss_halt_usd:,.2f}</b></div>"
        f"<div><span>Headroom</span><b class='mono {headroom_cls}'>${headroom:,.2f}</b></div>"
        f"<div><span>Status</span>{halt_pill}</div>"
        "</div></div>"
    )

    # ── BOT STATE + last error ──────────────────────────────────────────
    state_pill_cls = "on" if state == "running" else "off"
    detail_first = bot_detail.split("\n", 1)[0].strip()
    detail_lower = detail_first.lower()
    if any(k in detail_lower for k in ("error", "fail", "refused", "typeerror", "attributeerror", "exception", "crash")):
        detail_cls = "down"
    elif any(k in detail_lower for k in ("running", "starting", "started")):
        detail_cls = "up"
    else:
        detail_cls = "dim"
    detail_display = (
        (detail_first[:90] + "…") if len(detail_first) > 90 else (detail_first or "—")
    )
    if paused:
        pause_html = (
            f"<span class='pill warn' title='{escape(pause_reason)}'>⏸ PAUSED</span>"
        )
    else:
        pause_html = "<b class='mono dim'>—</b>"
    state_col = (
        "<div class='de-col'>"
        "<div class='de-h'>BOT STATE</div>"
        "<div class='de-kv'>"
        f"<div><span>State</span><span class='pill {state_pill_cls}'>{escape(state.upper())}</span></div>"
        f"<div><span>Uptime</span><b class='mono'>{escape(_ago(session_start))}</b></div>"
        f"<div><span>Last detail</span><b class='mono {detail_cls}' title='{escape(bot_detail)}'>{escape(detail_display)}</b></div>"
        f"<div><span>Auto-pause</span>{pause_html}</div>"
        "</div></div>"
    )

    # ── LAST 5 BLOCKED ──────────────────────────────────────────────────
    if blocked:
        rows = ""
        for r in blocked:
            ts = r.get("created_at") or ""
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=UTC)
                hhmm = t.strftime("%H:%M")
            except ValueError:
                hhmm = ts[-8:-3] if len(ts) >= 8 else ts
            reason = (r.get("error") or "").strip() or "—"
            short = reason[:64] + "…" if len(reason) > 64 else reason
            rows += (
                "<tr>"
                f"<td class='mono dim'>{escape(hhmm)}</td>"
                f"<td class='mono down' title='{escape(reason)}' style='white-space:normal'>{escape(short)}</td>"
                "</tr>"
            )
        blocked_html = f"<table class='gr-tail'><tbody>{rows}</tbody></table>"
    else:
        blocked_html = (
            "<div class='gr-tail-empty'>no blocked entries today</div>"
        )
    blocked_col = (
        "<div class='de-col'>"
        "<div class='de-h'>BLOCKED (LAST 5 TODAY)</div>"
        f"{blocked_html}"
        "</div>"
    )

    return (
        "<section class='card wide'>"
        "<div class='card-h'>RISK GUARDRAILS"
        "<span class='win'>silent-stop surface</span></div>"
        "<div class='gr-grid'>"
        + spend_col
        + halt_col
        + state_col
        + blocked_col
        + "</div></section>"
    )


def _decision_engine_panel(
    tick: dict[str, Any] | None,
    params: Any,
    recent: list[dict[str, Any]],
    paused: bool,
    pause_reason: str,
) -> str:
    """Render a transparency view of what the bot is digesting → deciding.

    Three columns — INPUTS (the tick), COMPUTATION (probabilities + edges),
    GATES (pass/fail per filter) — plus the final decision banner and a
    short tail of recent ticks so the operator can see decisions evolve.
    """
    if not tick:
        return (
            "<section class='card wide'><div class='card-h'>DECISION ENGINE"
            "<span class='win'>thinking…</span></div>"
            "<div class='chart-empty'>no ticks yet — start the bot to see decisions stream in</div>"
            "</section>"
        )

    # ── inputs being digested ──────────────────────────────────────────
    spot = tick.get("spot_price") or 0.0
    ref = tick.get("reference_price") or 0.0
    sigma = tick.get("sigma_per_second") or 0.0
    rem = tick.get("remaining_seconds") or 0
    up_bid = tick.get("up_best_bid")
    up_ask = tick.get("up_best_ask")
    down_bid = tick.get("down_best_bid")
    down_ask = tick.get("down_best_ask")
    feed = _parse_feed_source(tick.get("feed_source"))

    inputs_html = (
        "<div class='de-col'>"
        "<div class='de-h'>DIGESTING</div>"
        "<div class='de-kv'>"
        f"<div><span>BTC spot</span><b class='mono'>${spot:,.2f}</b></div>"
        f"<div><span>Window ref</span><b class='mono'>${ref:,.2f}</b></div>"
        f"<div><span>Basis</span><b class='mono {_cls(spot - ref)}'>{(spot - ref):+.2f}</b></div>"
        f"<div><span>Remaining</span><b class='mono'>{rem}s</b></div>"
        f"<div><span>σ / sec</span><b class='mono'>{sigma:.6f}</b></div>"
        f"<div><span>UP book (bid/ask)</span><b class='mono'>{_bk(up_bid)} / {_bk(up_ask)}</b></div>"
        f"<div><span>DOWN book (bid/ask)</span><b class='mono'>{_bk(down_bid)} / {_bk(down_ask)}</b></div>"
        f"<div><span>Spot feed</span><b class='mono dim'>{escape(feed.get('spot', '—'))}</b></div>"
        f"<div><span>Vol feed</span><b class='mono dim'>{escape(feed.get('vol', '—'))}</b></div>"
        "</div></div>"
    )

    # ── computation step (model output) ────────────────────────────────
    fair_up = tick.get("fair_up_prob")
    edge_up = (fair_up - up_ask) if (fair_up is not None and up_ask is not None) else None
    edge_dn = ((1 - fair_up) - down_ask) if (fair_up is not None and down_ask is not None) else None
    cands: list[tuple[str, float, float]] = []
    if edge_up is not None and up_ask is not None:
        cands.append(("Up", edge_up, up_ask))
    if edge_dn is not None and down_ask is not None:
        cands.append(("Down", edge_dn, down_ask))
    cand_side: str | None = None
    cand_edge: float | None = None
    cand_price: float | None = None
    if cands:
        cand_side, cand_edge, cand_price = max(cands, key=lambda c: c[1])
    cand_conf = (
        min(0.99, max(0.0, 0.50 + max(cand_edge, 0.0) * 2.8))
        if cand_edge is not None
        else None
    )

    def _edge_html(v: float | None) -> str:
        if v is None:
            return "<b class='mono dim'>—</b>"
        return f"<b class='mono {_cls(v)}'>{v:+.4f}</b>"

    fair_up_s = f"{fair_up * 100:.1f}%" if fair_up is not None else "—"
    fair_dn_s = f"{(1 - fair_up) * 100:.1f}%" if fair_up is not None else "—"
    cand_html = (
        f"{escape(cand_side)} @ {cand_price:.3f}"
        if cand_side is not None and cand_price is not None
        else "—"
    )
    conf_html = f"{cand_conf * 100:.1f}%" if cand_conf is not None else "—"

    compute_html = (
        "<div class='de-col'>"
        "<div class='de-h'>COMPUTING</div>"
        "<div class='de-kv'>"
        f"<div><span>fair Up (cal.)</span><b class='mono'>{fair_up_s}</b></div>"
        f"<div><span>fair Down</span><b class='mono'>{fair_dn_s}</b></div>"
        f"<div><span>edge Up = fair − ask</span>{_edge_html(edge_up)}</div>"
        f"<div><span>edge Down = (1−fair) − ask</span>{_edge_html(edge_dn)}</div>"
        f"<div><span>Candidate side</span><b class='mono'>{cand_html}</b></div>"
        f"<div><span>Candidate edge</span>{_edge_html(cand_edge)}</div>"
        f"<div><span>Confidence (model)</span><b class='mono'>{conf_html}</b></div>"
        "</div></div>"
    )

    # ── gate checks (re-derived from inputs + active params) ───────────
    def _gate(label: str, ok: bool | None, detail: str = "") -> str:
        if ok is None:
            mark, cls = "·", "dim"
        elif ok:
            mark, cls = "✓", "up"
        else:
            mark, cls = "✗", "down"
        d = f" <em class='dim'>{escape(detail)}</em>" if detail else ""
        return f"<div class='de-gate {cls}'><span>{mark}</span>{escape(label)}{d}</div>"

    feed_ok = not bool(tick.get("reason", "").startswith("skip: settlement feed degraded"))
    book_ok = (up_ask is not None) or (down_ask is not None)
    time_ok = rem > params.entry_min_remaining_seconds
    edge_ok = (cand_edge is not None) and (cand_edge >= params.entry_edge_min)
    conf_ok = (cand_conf is not None) and (cand_conf >= params.min_confidence)
    cap_ok = (cand_edge is None) or (cand_edge <= params.entry_edge_max)
    price_ok = (
        cand_price is None
        or (params.min_entry_price <= cand_price <= params.max_entry_price)
    )

    gates_html = (
        "<div class='de-col'>"
        "<div class='de-h'>GATES</div>"
        "<div class='de-gates'>"
        + _gate("settlement feed healthy", feed_ok)
        + _gate("book quote available", book_ok)
        + _gate(
            f"remaining > {params.entry_min_remaining_seconds}s",
            time_ok,
            f"now {rem}s",
        )
        + _gate(
            f"edge ≥ {params.entry_edge_min:.3f}",
            edge_ok,
            (f"{cand_edge:+.3f}" if cand_edge is not None else ""),
        )
        + _gate(
            f"confidence ≥ {params.min_confidence:.2f}",
            conf_ok,
            (f"{cand_conf:.2f}" if cand_conf is not None else ""),
        )
        + _gate(
            f"edge ≤ {params.entry_edge_max:.2f} (stale-model)",
            cap_ok,
        )
        + _gate(
            f"price in [{params.min_entry_price:.2f}, {params.max_entry_price:.2f}]",
            price_ok,
            (f"@{cand_price:.3f}" if cand_price is not None else ""),
        )
        + "</div></div>"
    )

    # ── final decision banner ──────────────────────────────────────────
    reason = (tick.get("reason") or "idle").strip()
    side = tick.get("signal_side")
    notional = tick.get("notional_usd") or 0.0
    if paused:
        d_cls, d_lbl, d_body = (
            "down",
            "AUTO-PAUSED",
            pause_reason or "edge-decay guard tripped",
        )
    elif side in ("Up", "Down"):
        d_cls, d_lbl = "up", f"ENTER {side.upper()}"
        d_body = f"size ${notional:.0f} · {reason}"
    elif reason.startswith("enter"):
        d_cls, d_lbl, d_body = "up", "ENTER (queued)", reason
    else:
        d_cls, d_lbl, d_body = "dim", "SKIP", reason

    decision_banner = (
        "<div class='de-decision'>"
        f"<span class='de-arrow'>▸</span><b class='{d_cls}'>{escape(d_lbl)}</b>"
        f"<span class='de-reason'>{escape(d_body)}</span>"
        "</div>"
    )

    # ── recent decision tail ───────────────────────────────────────────
    tail_rows = ""
    for r in recent:
        ts_ago = _ago(r.get("created_at"))
        sp = r.get("spot_price") or 0.0
        fu = r.get("fair_up_prob")
        rs = (r.get("reason") or "").strip()
        is_entry = rs.startswith("enter")
        rcls = "up" if is_entry else "dim"
        cand = r.get("signal_side") or "—"
        ask_html = (
            f"ask {(r.get('up_best_ask') if cand == 'Up' else r.get('down_best_ask') or 0):.3f}"
            if cand in ("Up", "Down")
            else "—"
        )
        fair_t = f"{fu * 100:.1f}%" if fu is not None else "—"
        tail_rows += (
            "<tr>"
            f"<td class='mono dim'>{escape(ts_ago)}</td>"
            f"<td class='mono'>${sp:,.0f}</td>"
            f"<td class='mono'>{fair_t}</td>"
            f"<td class='mono'>{escape(cand)} {ask_html}</td>"
            f"<td class='mono {rcls}' style='white-space:normal'>{escape(rs)}</td>"
            "</tr>"
        )
    tail_html = (
        "<div class='de-tail'>"
        "<div class='de-h'>RECENT TICKS — what the bot saw &amp; decided</div>"
        "<table class='de-tail-tbl'><thead><tr>"
        "<th>age</th><th>spot</th><th>fair Up</th><th>side @ ask</th><th>decision</th>"
        "</tr></thead><tbody>"
        + (
            tail_rows
            or "<tr><td colspan='5' class='dim' style='text-align:center;padding:10px'>no recent ticks</td></tr>"
        )
        + "</tbody></table></div>"
    )

    return (
        "<section class='card wide'><div class='card-h'>DECISION ENGINE"
        f"<span class='win'>active params · edge≥{params.entry_edge_min:.3f} · conf≥{params.min_confidence:.2f} · rem≥{params.entry_min_remaining_seconds}s</span>"
        "</div>"
        "<div class='de-grid'>"
        + inputs_html
        + compute_html
        + gates_html
        + "</div>"
        + decision_banner
        + tail_html
        + "</section>"
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
    bot_detail = await get_config("btc_bot.detail", "") or ""
    blocked_today = await _recent_blocked(limit=5)
    submitted_count, submitted_notional = await _today_submitted_summary()

    tick = await _latest_tick()
    recent_ticks = await _recent_decisions(limit=10)
    # Rolling recent window = the CURRENT regime (not blended with older
    # experimental configs that lived in the same journal).
    closed = await _closed(style, None, limit=40)
    closed_session = await _closed(style, session_start)  # this run, for the ribbon
    open_pos = await _open_positions(style)
    perf = _performance(closed)
    perf_live = _performance([c for c in closed if c.get("mode") == "live"])
    perf_paper = _performance([c for c in closed if c.get("mode") == "paper"])
    last_live_at = await _last_live_order_at()
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
    # ---- connectivity ----
    # Real liveness comes from (a) when the loop last journaled a tick, and
    # (b) what feed_source that tick recorded for each upstream. The old
    # 'CHAINLINK / CLOB' chips merely string-matched feed_source and would
    # stay green for hours after the loop hung. Now each chip flips off when
    # its source goes degraded, and a TICK chip shows the loop's own age.
    tick_age = _tick_age_seconds(tick.get("created_at") if tick else None)
    stale_after = int(max(_config.BTC_PAPER_TICK_SECONDS * 3, 20))
    parts = _parse_feed_source(tick.get("feed_source") if tick else None)
    book_ok = bool(tick) and (
        tick.get("up_best_ask") is not None
        or tick.get("down_best_ask") is not None
        or tick.get("up_best_bid") is not None
        or tick.get("down_best_bid") is not None
    )
    if tick_age is None:
        tick_chip = "<span class='feed warn'>TICK ∅</span>"
    elif tick_age <= stale_after:
        tick_chip = f"<span class='feed on'>TICK {tick_age}s</span>"
    else:
        tick_chip = f"<span class='feed warn'>TICK {tick_age}s STALE</span>"

    def _chip(label: str, ok: bool) -> str:
        return f"<span class='feed {'on' if ok else 'warn'}'>{label}</span>"

    chips = [
        tick_chip,
        _chip("SPOT", (parts.get("spot") or "").startswith("chainlink")),
        _chip("REF", (parts.get("ref") or "").startswith("chainlink")),
        _chip("VOL", parts.get("vol") == "chainlink_ws"),
        _chip("BOOK", book_ok),
    ]
    if is_live:
        live_age = _tick_age_seconds(last_live_at)
        if live_age is None:
            chips.append("<span class='feed warn'>EXEC ∅</span>")
        else:
            # "Real trade is X minutes ago" was the exact diagnostic the
            # operator needed when no entries are firing — surface it here.
            label = (
                f"EXEC {live_age}s"
                if live_age < 60
                else f"EXEC {live_age // 60}m{live_age % 60:02d}s"
            )
            # Treat >5min without ANY live-order action as warn-worthy when
            # the bot is supposed to be live. A bot that lost CLOB write
            # access often keeps reading and journaling skips.
            chips.append(_chip(label, live_age <= 300))
    feeds = "".join(chips)
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
    active = _params.load_active()
    proposed = _params.load_proposed()
    if active.source == "applied":
        params_html = (
            f"<b class='up'>applied · edge≥{active.entry_edge_min:.3f} · "
            f"conf≥{active.min_confidence:.2f} · rem≥{active.min_remaining_seconds}s</b>"
        )
    else:
        params_html = (
            f"<b>env defaults · edge≥{active.entry_edge_min:.3f} · "
            f"conf≥{active.min_confidence:.2f}</b>"
        )
    if proposed is not None:
        m = proposed.backtest_meta or {}
        cur_pnl = m.get("current_pnl") or 0.0
        rec_pnl = m.get("recommended_pnl") or 0.0
        delta_pnl = rec_pnl - cur_pnl
        proposed_html = (
            f"<div><span>Proposed</span><b class='{'up' if delta_pnl > 0 else 'dim'}'>"
            f"edge≥{proposed.entry_edge_min:.3f} · conf≥{proposed.min_confidence:.2f} · "
            f"backtest Δ ${delta_pnl:+.2f} · run <code>params_apply --confirm</code></b></div>"
        )
    else:
        proposed_html = ""

    cal = _calibration.load()
    if isinstance(cal, _calibration.IsotonicCalibrator) and cal.n_samples > 0:
        if cal.brier_raw is not None and cal.brier_cal is not None:
            delta = cal.brier_raw - cal.brier_cal
            cal_html = (
                f"<b class='up'>isotonic · n={cal.n_samples} · "
                f"Brier {cal.brier_raw:.3f}→{cal.brier_cal:.3f} "
                f"({delta:+.3f})</b>"
            )
        else:
            cal_html = f"<b class='up'>isotonic · n={cal.n_samples}</b>"
    else:
        cal_html = "<b class='dim'>identity (no fit yet)</b>"

    strat = (
        "<section class='card'><div class='card-h'>STRATEGY</div>"
        "<div class='kv'>"
        f"<div><span>Model</span><b>Fair-Value · Settle</b></div>"
        f"<div><span>Style</span><b>{escape(style)} (1 entry/window, hold→resolution)</b></div>"
        f"<div><span>Edge band</span><b>{_config.BTC_PAPER_ENTRY_EDGE_MIN:.3f} – {_config.BTC_PAPER_ENTRY_EDGE_MAX:.3f}</b></div>"
        f"<div><span>Entry floor</span><b>≥ {_config.BTC_PAPER_MIN_ENTRY_PRICE:.2f} (favorites)</b></div>"
        f"<div><span>Sizing</span><b>${_config.BTC_LIVE_MAX_TRADE_USD if is_live else _config.BTC_PAPER_MAX_TRADE_USD:.0f}/clip · 1 pos max</b></div>"
        f"<div><span>Settlement</span><b>Chainlink BTC/USD · ≥ ⇒ Up</b></div>"
        f"<div><span>Params</span>{params_html}</div>"
        f"{proposed_html}"
        f"<div><span>Calibration</span>{cal_html}</div>"
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
    # Combined view stays on top (equity curve, recent-N pnl); the LIVE and
    # PAPER mini-blocks below it keep real and simulated alpha separate so a
    # winning live tape never gets dragged down by sim-only paper losses (or
    # vice versa) in the operator's headline number.
    if perf["n"]:
        wl = f"{perf['wins']}W / {perf['losses']}L"
        pf_s = f"{perf['profit_factor']:.2f}" if perf["profit_factor"] else "∞"

        def _mini(label_html: str, p: dict[str, Any]) -> str:
            if not p.get("n"):
                return (
                    "<div class='perf-mini'>"
                    f"<div class='perf-mini-h'>{label_html}</div>"
                    "<div class='perf-mini-empty'>no closed trades yet</div>"
                    "</div>"
                )
            wl_s = f"{p['wins']}W / {p['losses']}L"
            return (
                "<div class='perf-mini'>"
                f"<div class='perf-mini-h'>{label_html}</div>"
                "<div class='perf-mini-row'>"
                f"<span>P&amp;L</span><b class='{_cls(p['pnl'])}'>{_money(p['pnl'], True)}</b>"
                "</div>"
                "<div class='perf-mini-row'>"
                f"<span>ROI</span><b class='{_cls(p['roi'])}'>{_pct(p['roi'], True)}</b>"
                "</div>"
                "<div class='perf-mini-row'>"
                f"<span>Win rate</span><b>{_pct(p['win_rate'])} <em>({wl_s})</em></b>"
                "</div>"
                "<div class='perf-mini-row'>"
                f"<span>Expectancy</span><b class='{_cls(p['expectancy'])}'>{_money(p['expectancy'], True)}</b>"
                "</div>"
                "</div>"
            )

        live_label = "<span class='pill live'>● LIVE</span>"
        paper_label = "<span class='pill paper'>PAPER</span>"
        perf_panel = (
            "<section class='card'><div class='card-h'>PERFORMANCE / ALPHA"
            f"<span class='win'>recent {perf['n']} · {style} · live+paper</span></div>"
            f"<div class='equity'>{_svg_equity(perf['equity'])}</div>"
            "<div class='statrow'>"
            f"{_stat('Net P&L', _money(perf['pnl'], True), _cls(perf['pnl']))}"
            f"{_stat('ROI', _pct(perf['roi'], True), _cls(perf['roi']))}"
            f"{_stat('Win rate', _pct(perf['win_rate']), '', wl)}"
            f"{_stat('Expectancy', _money(perf['expectancy'], True), _cls(perf['expectancy']), 'per trade')}"
            f"{_stat('Profit factor', pf_s, _cls((perf['profit_factor'] or 2) - 1))}"
            f"{_stat('Max DD', _money(perf['max_dd']), 'down' if perf['max_dd'] < 0 else '')}"
            "</div>"
            "<div class='perf-split'>"
            f"{_mini(live_label, perf_live)}"
            f"{_mini(paper_label, perf_paper)}"
            "</div>"
            "</section>"
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
    # Mode chip per row is the operator's at-a-glance answer to "was that one
    # real money?" — without it, a row from yesterday's paper run looks
    # identical to a real fill, and the LIVE PnL number above can't be
    # tracked back to the trades that produced it.
    def _mode_chip(m: Any) -> str:
        label = str(m or "?").upper()
        cls = "live" if label == "LIVE" else "paper" if label == "PAPER" else "warn"
        return f"<span class='pill {cls}'>{escape(label)}</span>"

    rows = ""
    recent = list(reversed(closed))[:12]
    for c in recent:
        p = c["realized_pnl_usd"] or 0.0
        rows += (
            "<tr>"
            f"<td>{_mode_chip(c.get('mode'))}</td>"
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
            f"<td>{_mode_chip(c.get('mode'))}</td>"
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
        "<th>mode</th><th>age</th><th>side</th><th>entry</th><th>exit</th><th>size</th><th>P&L</th><th>reason</th>"
        "</tr></thead><tbody>"
        + (rows or "<tr><td colspan='8' class='dim' style='text-align:center;padding:18px'>no trades yet — the strategy is selective</td></tr>")
        + "</tbody></table></section>"
    )

    # Build a StrategyParams-shaped view from the active params so the
    # decision-engine panel can re-evaluate gates against the same
    # thresholds the live loop uses.
    class _GateParams:
        entry_edge_min = active.entry_edge_min
        entry_edge_max = active.entry_edge_max
        min_confidence = active.min_confidence
        entry_min_remaining_seconds = active.min_remaining_seconds
        min_entry_price = active.min_entry_price
        max_entry_price = active.max_entry_price

    decision_panel = _decision_engine_panel(
        tick, _GateParams(), recent_ticks, paused, pause_reason
    )

    guardrails = _guardrails_panel(
        day_spend=day_notional,
        bankroll_cap=_config.BTC_LIVE_BANKROLL_CAP_USD,
        submitted_count=submitted_count,
        submitted_notional=submitted_notional,
        day_pnl=day_pnl,
        loss_halt_usd=_config.BTC_LIVE_DAILY_LOSS_HALT_USD,
        state=state,
        bot_detail=bot_detail,
        session_start=session_start,
        paused=paused,
        pause_reason=pause_reason,
        blocked=blocked_today,
    )

    return (
        "<div class='ems'>"
        + ribbon
        + "<div class='ems-grid'>"
        + guardrails
        + strat
        + market
        + decision_panel
        + perf_panel
        + tca
        + blotter
        + "</div></div>"
    )


def _bk(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "—"
