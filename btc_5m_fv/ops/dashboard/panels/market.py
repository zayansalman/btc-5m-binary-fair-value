"""Live market panel: probability gauge, UP/DOWN book, basis."""
from __future__ import annotations

from html import escape
from typing import Any

from . import _shared as s


def render(tick: dict[str, Any] | None) -> str:
    if not tick:
        return (
            "<section class='card'><div class='card-h'>LIVE MARKET</div>"
            "<div class='chart-empty'>no ticks yet</div></section>"
        )
    spot = tick.get("spot_price") or 0
    ref = tick.get("reference_price") or 0
    basis = spot - ref
    rem = tick.get("remaining_seconds") or 0
    edge = tick.get("edge")
    return (
        "<section class='card'><div class='card-h'>LIVE MARKET"
        f"<span class='win'>{escape((tick.get('window_slug') or '').replace('btc-updown-5m-', '#'))} · {rem}s</span></div>"
        f"{s.gauge(tick.get('fair_up_prob'))}"
        "<div class='book'>"
        "<div class='book-side up'><div class='bk-l'>UP</div>"
        f"<div class='bk-px'>{s.bk(tick.get('up_best_bid'))} / {s.bk(tick.get('up_best_ask'))}</div></div>"
        "<div class='book-side down'><div class='bk-l'>DOWN</div>"
        f"<div class='bk-px'>{s.bk(tick.get('down_best_bid'))} / {s.bk(tick.get('down_best_ask'))}</div></div>"
        "</div>"
        "<div class='kv tight'>"
        f"<div><span>BTC spot</span><b>${spot:,.2f}</b></div>"
        f"<div><span>Window ref</span><b>${ref:,.2f}</b></div>"
        f"<div><span>Basis</span><b class='{s.cls(basis)}'>{basis:+.2f}</b></div>"
        f"<div><span>Edge</span><b class='{s.cls(edge)}'>{('%+.3f' % edge) if edge is not None else '—'}</b></div>"
        "</div>"
        f"<div class='decision'>{escape((tick.get('reason') or 'idle'))}</div>"
        "</section>"
    )
