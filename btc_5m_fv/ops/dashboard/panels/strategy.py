"""Strategy panel: active params, proposed-vs-applied delta, calibration."""
from __future__ import annotations

from html import escape

import config as _config
from btc_bot import calibration as _calibration
from btc_bot import params as _params


def render(
    *,
    style: str,
    is_live: bool,
    paused: bool,
    pause_reason: str,
    max_trade: float | None = None,
    trade_shares: float | None = None,
    current_price: float | None = None,
) -> str:
    active = _params.load_active()
    # Sizing line: share-denominated when the operator set a share count (#89),
    # else the dollar clip.
    if trade_shares is not None:
        if current_price and current_price > 0:
            sizing = (
                f"{trade_shares:g} shares (~${trade_shares * current_price:,.2f}) · 1 pos max"
            )
        else:
            sizing = f"{trade_shares:g} shares · 1 pos max"
    else:
        _dollar = (
            max_trade
            if max_trade is not None
            else (_config.BTC_LIVE_MAX_TRADE_USD if is_live else _config.BTC_PAPER_MAX_TRADE_USD)
        )
        sizing = f"${_dollar:.0f}/clip · 1 pos max"
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

    return (
        "<section class='card'><div class='card-h'>STRATEGY</div>"
        "<div class='kv'>"
        f"<div><span>Model</span><b>Fair-Value · Settle</b></div>"
        f"<div><span>Style</span><b>{escape(style)} (1 entry/window, hold→resolution)</b></div>"
        f"<div><span>Edge band</span><b>{_config.BTC_PAPER_ENTRY_EDGE_MIN:.3f} – {_config.BTC_PAPER_ENTRY_EDGE_MAX:.3f}</b></div>"
        f"<div><span>Entry floor</span><b>≥ {_config.BTC_PAPER_MIN_ENTRY_PRICE:.2f} (favorites)</b></div>"
        f"<div><span>Sizing</span><b>{sizing}</b></div>"
        f"<div><span>Settlement</span><b>Chainlink BTC/USD · ≥ ⇒ Up</b></div>"
        f"<div><span>Params</span>{params_html}</div>"
        f"{proposed_html}"
        f"<div><span>Calibration</span>{cal_html}</div>"
        f"<div><span>Auto-pause</span><b class='{'down' if paused else 'up'}'>{'PAUSED — ' + escape(pause_reason[:40]) if paused else 'armed (edge-decay)'}</b></div>"
        "</div></section>"
    )
