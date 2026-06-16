"""Risk guardrails: daily spend, loss-halt, bot state, recent BLOCKED entries.

The loss-halt toggle is rendered in BOTH paper and live so the control
surface matches across modes. In live mode the button is disabled and
labeled — the live gate is built with ``allow_overrides=False`` and would
ignore the POST anyway; disabling at the UI layer makes the safety invariant
visible to the operator instead of silently no-opping.
"""
from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from . import _shared as s


def render(
    *,
    day_spend: float,
    bankroll_cap: float | None,
    submitted_count: int,
    submitted_notional: float,
    day_pnl: float,
    live_pnl: float,
    paper_pnl: float,
    loss_halt_usd: float,
    state: str,
    bot_detail: str,
    session_start: str | None,
    paused: bool,
    pause_reason: str,
    blocked: list[dict[str, Any]],
    mode: str = "paper",
    bypass_loss_halt: bool = False,
) -> str:
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
    halted = day_pnl <= -loss_halt_usd and not bypass_loss_halt
    headroom = loss_halt_usd + min(0.0, day_pnl)
    if bypass_loss_halt:
        halt_pill = "<span class='pill warn' title='Paper study: halt disabled'>BYPASS</span>"
    elif halted:
        halt_pill = "<span class='pill warn'>HALTED</span>"
    else:
        halt_pill = "<span class='pill on'>OK</span>"
    headroom_cls = "down" if headroom < loss_halt_usd * 0.4 else ""

    def _toggle_button(
        endpoint: str,
        on: bool,
        on_label: str,
        off_label: str,
        disabled: bool = False,
        disabled_title: str = "",
    ) -> str:
        next_state = "false" if on else "true"
        btn_label = on_label if on else off_label
        btn_cls = "btn-warn" if not on else "btn-ok"
        if disabled:
            return (
                f"<button class='gr-btn {btn_cls}' disabled "
                f"title='{escape(disabled_title)}'>{escape(btn_label)}</button>"
            )
        return (
            f"<button class='gr-btn {btn_cls}' "
            f"onclick=\"fetch('{endpoint}',"
            f"{{method:'POST',headers:{{'Content-Type':'application/json'}},"
            f"body:JSON.stringify({{enabled:{next_state}}})}})"
            f".then(()=>setTimeout(refreshAll,300))\">"
            f"{escape(btn_label)}</button>"
        )

    is_live_mode = mode == "live"
    halt_btn = _toggle_button(
        "/api/paper/bypass_loss_halt",
        bypass_loss_halt,
        "Re-enable halt",
        "Disable halt (study)",
        disabled=is_live_mode,
        disabled_title="Live mode: loss halt is a real-money safety gate and cannot be disabled from the UI",
    )
    halt_hint = (
        "live: safety gate enforced — cannot disable"
        if is_live_mode
        else "paper study only — never affects live"
    )
    toggle_html = (
        "<div class='gr-toggle'>"
        + halt_btn
        + f"<span class='gr-toggle-hint'>{escape(halt_hint)}</span>"
        "</div>"
    )
    halt_col = (
        "<div class='de-col'>"
        "<div class='de-h'>LOSS HALT</div>"
        "<div class='de-kv'>"
        f"<div><span>Live P&amp;L</span><b class='mono {s.cls(live_pnl)}' "
        f"title='Real money — drives halt decision'>{s.money(live_pnl, True)}</b></div>"
        f"<div><span>Paper P&amp;L</span><b class='mono {s.cls(paper_pnl)}' "
        f"title='Study — counts toward halt unless bypassed'>{s.money(paper_pnl, True)}</b></div>"
        f"<div><span>Halt threshold</span><b class='mono'>−${loss_halt_usd:,.2f}</b></div>"
        f"<div><span>Headroom (combined)</span><b class='mono {headroom_cls}'>${headroom:,.2f}</b></div>"
        f"<div><span>Status</span>{halt_pill}</div>"
        "</div>"
        + toggle_html
        + "</div>"
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
        f"<div><span>Uptime</span><b class='mono'>{escape(s.ago(session_start))}</b></div>"
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
            row_mode = (r.get("mode") or "live").lower()
            mode_tag = (
                "<span class='pill dim' style='margin-right:6px'>paper</span>"
                if row_mode == "paper" else ""
            )
            rows += (
                "<tr>"
                f"<td class='mono dim'>{escape(hhmm)}</td>"
                f"<td class='mono down' title='{escape(reason)}' style='white-space:normal'>"
                f"{mode_tag}{escape(short)}</td>"
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
