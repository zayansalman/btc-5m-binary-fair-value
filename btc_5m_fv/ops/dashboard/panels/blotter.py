"""Trade blotter: open positions on top, last 12 closed below, mode chip per row.

Mode chip per row is the operator's at-a-glance answer to "was that one
real money?" — without it, a row from yesterday's paper run looks identical
to a real fill, and the LIVE PnL number above can't be tracked back to the
trades that produced it.
"""
from __future__ import annotations

from html import escape
from typing import Any

from . import _shared as s


def render(*, closed: list[dict[str, Any]], open_pos: list[dict[str, Any]]) -> str:
    def _mode_chip(m: Any) -> str:
        label = str(m or "?").upper()
        c = "live" if label == "LIVE" else "paper" if label == "PAPER" else "warn"
        return f"<span class='pill {c}'>{escape(label)}</span>"

    rows = ""
    recent = list(reversed(closed))[:12]
    for c in recent:
        p = c["realized_pnl_usd"] or 0.0
        rows += (
            "<tr>"
            f"<td>{_mode_chip(c.get('mode'))}</td>"
            f"<td class='mono dim'>{s.ago(c.get('closed_at'))}</td>"
            f"<td><span class='tag {c['side'].lower()}'>{escape(c['side'])}</span></td>"
            f"<td class='mono'>{(c['entry_price'] or 0):.3f}</td>"
            f"<td class='mono'>{(c['exit_price'] if c['exit_price'] is not None else 0):.2f}</td>"
            f"<td class='mono'>{s.money(c['notional_usd'])}</td>"
            f"<td class='mono {s.cls(p)}'>{s.money(p, True)}</td>"
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
            f"<td class='mono'>{s.money(c['notional_usd'])}</td>"
            f"<td class='mono flat'>OPEN</td>"
            f"<td class='dim'>holding→resolution</td>"
            "</tr>"
        ) + rows
    return (
        "<section class='card wide'><div class='card-h'>TRADE BLOTTER</div>"
        "<table class='blotter'><thead><tr>"
        "<th>mode</th><th>age</th><th>side</th><th>entry</th><th>exit</th><th>size</th><th>P&L</th><th>reason</th>"
        "</tr></thead><tbody>"
        + (rows or "<tr><td colspan='8' class='dim' style='text-align:center;padding:18px'>no trades yet — the strategy is selective</td></tr>")
        + "</tbody></table></section>"
    )
