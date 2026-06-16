"""Operator runtime controls (#50): live-editable risk knobs.

Currently exposes the unified max trade size. The bot re-reads the persisted
value every tick, so changes apply without a restart, in paper AND live. Pure
``render(...)`` transform like every other panel — no DB access here (the
current value is loaded in ``ems.py`` and passed in). Singleton/multiple
position mode + max concurrent positions will register here later.
"""
from __future__ import annotations


def render(
    *,
    max_trade_current: float | None,
    max_trade_env: float,
    min_trade: float,
) -> str:
    """Render the CONTROLS card.

    ``max_trade_current`` is the operator override (None when unset);
    ``max_trade_env`` is the env/config default the bot falls back to.
    """
    effective = max_trade_current if max_trade_current is not None else max_trade_env
    source = "operator" if max_trade_current is not None else "env default"
    src_cls = "up" if max_trade_current is not None else "dim"
    return (
        "<section class='card'>"
        "<div class='card-h'>CONTROLS<span class='win'>runtime · no restart</span></div>"
        "<div class='de-kv'>"
        f"<div><span>Max trade size</span>"
        f"<b class='mono'>${effective:,.2f} <em class='{src_cls}'>({source})</em></b></div>"
        f"<div><span>Env default</span><b class='mono dim'>${max_trade_env:,.2f}</b></div>"
        "</div>"
        "<div class='ctl-row'>"
        "<input id='ctl-max-trade' class='ctl-input' type='number' "
        f"step='0.5' min='0.5' value='{effective:.2f}' "
        "aria-label='Max trade size in USD' />"
        "<button class='gr-btn btn-ok' onclick='setMaxTradeSize()'>Apply</button>"
        "</div>"
        "<div class='gr-toggle-hint'>"
        f"sizes by confidence up to this cap · min ${min_trade:,.2f} · applies to paper + live"
        "</div>"
        "</section>"
    )
