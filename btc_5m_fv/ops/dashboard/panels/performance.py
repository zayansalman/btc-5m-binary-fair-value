"""Performance / alpha panel: combined equity curve + LIVE/PAPER mini-cards.

The combined view (equity, net P&L, ROI, win rate, expectancy, profit
factor, max DD) sits on top. The two mini-cards below split LIVE vs PAPER
using each mode's own recent-N window — a low-volume mode never gets crowded
out by a high-volume mode (the bug that left LIVE blank when paper trades
dominated the recent slice).
"""
from __future__ import annotations

from html import escape
from typing import Any

from . import _shared as s


def render(
    *,
    style: str,
    perf: dict[str, Any],
    perf_live: dict[str, Any],
    perf_paper: dict[str, Any],
) -> str:
    if not perf.get("n"):
        return (
            "<section class='card'><div class='card-h'>PERFORMANCE / ALPHA</div>"
            "<div class='chart-empty'>awaiting first settled trade this session</div></section>"
        )
    wl = f"{perf['wins']}W / {perf['losses']}L"
    pf_s = f"{perf['profit_factor']:.2f}" if perf["profit_factor"] else "∞"

    def _mini(label_html: str, p: dict[str, Any]) -> str:
        # Always render the same row layout for LIVE and PAPER so the two
        # mini-cards stay visually parallel. Empty state shows zeros + a
        # 'n=0' hint instead of a different layout that visually breaks
        # symmetry with the populated mode.
        n = p.get("n") or 0
        if n:
            pnl_v = p["pnl"]
            roi_v = p["roi"]
            win_v = p["win_rate"]
            exp_v = p["expectancy"]
            wl_s = f"{p['wins']}W / {p['losses']}L"
        else:
            pnl_v = roi_v = win_v = exp_v = 0.0
            wl_s = "0W / 0L"
        sub = f"recent {n}" if n else "no closed trades yet"
        return (
            "<div class='perf-mini'>"
            f"<div class='perf-mini-h'>{label_html}<span class='perf-mini-sub'>{escape(sub)}</span></div>"
            "<div class='perf-mini-row'>"
            f"<span>P&amp;L</span><b class='{s.cls(pnl_v)}'>{s.money(pnl_v, True)}</b>"
            "</div>"
            "<div class='perf-mini-row'>"
            f"<span>ROI</span><b class='{s.cls(roi_v)}'>{s.pct(roi_v, True)}</b>"
            "</div>"
            "<div class='perf-mini-row'>"
            f"<span>Win rate</span><b>{s.pct(win_v)} <em>({wl_s})</em></b>"
            "</div>"
            "<div class='perf-mini-row'>"
            f"<span>Expectancy</span><b class='{s.cls(exp_v)}'>{s.money(exp_v, True)}</b>"
            "</div>"
            "</div>"
        )

    live_label = "<span class='pill live'>● LIVE</span>"
    paper_label = "<span class='pill paper'>PAPER</span>"
    return (
        "<section class='card'><div class='card-h'>PERFORMANCE / ALPHA"
        f"<span class='win'>recent {perf['n']} · {style} · live+paper</span></div>"
        f"<div class='equity'>{s.svg_equity(perf['equity'])}</div>"
        "<div class='statrow'>"
        f"{s.stat('Net P&L', s.money(perf['pnl'], True), s.cls(perf['pnl']))}"
        f"{s.stat('ROI', s.pct(perf['roi'], True), s.cls(perf['roi']))}"
        f"{s.stat('Win rate', s.pct(perf['win_rate']), '', wl)}"
        f"{s.stat('Expectancy', s.money(perf['expectancy'], True), s.cls(perf['expectancy']), 'per trade')}"
        f"{s.stat('Profit factor', pf_s, s.cls((perf['profit_factor'] or 2) - 1))}"
        f"{s.stat('Max DD', s.money(perf['max_dd']), 'down' if perf['max_dd'] < 0 else '')}"
        "</div>"
        "<div class='perf-split'>"
        f"{_mini(live_label, perf_live)}"
        f"{_mini(paper_label, perf_paper)}"
        "</div>"
        "</section>"
    )
