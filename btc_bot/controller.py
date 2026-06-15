"""Start/stop controller for the BTC 5-minute trader (paper default, live opt-in)."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

import config as _config
from btc_5m_fv.execution.live import LiveBootRefused, assert_live_boot_allowed
from btc_bot.paper import (
    count_open_positions,
    force_close_open_positions,
    run_paper_loop,
)
from config import BTC_BOT_MODE, BTC_PAPER_MAX_TRADE_USD, BTC_PAPER_MIN_TRADE_USD
from db import get_config, set_config
from logging_setup import get_logger

log = get_logger("btc_controller")

PAPER_ONLY_DETAIL = (
    "BTC 5-minute paper mode is ready. No live orders are placed in paper mode."
)
LIVE_MODE_DETAIL = (
    "BTC 5-minute LIVE mode is configured — Start will place REAL orders on the "
    "Polymarket CLOB, risk-gated and journaled to btc_live_orders."
)

_runner_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None
_thread_lock = threading.Lock()


@dataclass
class BtcBotStatus:
    state: str
    mode: str
    updated_at: str | None
    detail: str


def _default_detail() -> str:
    if _config.BTC_BOT_MODE == "live":
        return (
            f"{LIVE_MODE_DETAIL}\n\n"
            f"Per-trade cap ${_config.BTC_LIVE_MAX_TRADE_USD:.2f}, daily loss halt "
            f"${_config.BTC_LIVE_DAILY_LOSS_HALT_USD:.2f}, bankroll cap "
            f"${_config.BTC_LIVE_BANKROLL_CAP_USD:.2f}. "
            f"Kill switch: touch {_config.KILL_SWITCH_PATH}."
        )
    return (
        f"{PAPER_ONLY_DETAIL}\n\n"
        f"Paper sizing range: ${BTC_PAPER_MIN_TRADE_USD:.0f}-"
        f"${BTC_PAPER_MAX_TRADE_USD:.0f} by confidence."
    )


def _is_runner_alive() -> bool:
    return _runner_thread is not None and _runner_thread.is_alive()


async def get_status() -> BtcBotStatus:
    """Return current BTC controller status."""
    state = await get_config("btc_bot.state", "stopped")
    mode = await get_config("btc_bot.mode", BTC_BOT_MODE)
    updated_at = await get_config("btc_bot.updated_at")
    detail = await get_config("btc_bot.detail", _default_detail())

    # State derives from the actual runner thread, not the stored row alone
    # (issue #23): a display that can read STOPPED while the loop places
    # orders makes the operator's kill decision unreliable — and vice versa.
    if state == "running" and not _is_runner_alive():
        state = "stopped"
        detail = "BTC bot loop is not running in this process. Press Start to restart."
        await set_config("btc_bot.state", state)
        await set_config("btc_bot.detail", detail)
    elif state != "running" and _is_runner_alive():
        state = "running"
        await set_config("btc_bot.state", state)

    return BtcBotStatus(
        state=state or "stopped",
        mode=mode or BTC_BOT_MODE,
        updated_at=updated_at,
        detail=detail or _default_detail(),
    )


async def current_mode() -> str:
    """The active execution mode: runtime selector overrides the env default."""
    return await get_config("btc_bot.requested_mode", _config.BTC_BOT_MODE) or "paper"


async def set_mode(mode: str) -> BtcBotStatus:
    """Switch execution mode from the dashboard, then restart the loop cleanly.

    Live is gated exactly like a boot: if the gate fails the mode is NOT
    changed and an error status is returned. Stop-before-start guarantees a
    single loop (no overlap).
    """
    if mode not in ("paper", "live"):
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "live":
        # Refuse the switch up front if live can't legally run.
        assert_live_boot_allowed()
    await request_stop()
    await set_config("btc_bot.requested_mode", mode)
    return await request_start()


async def request_start() -> BtcBotStatus:
    """Start the trading runner (paper by default, live only when fully gated)."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    mode = await current_mode()
    if mode == "live":
        try:
            # Boot gate is checked HERE, before any thread starts. Refusal
            # means nothing runs — live never silently falls back to paper.
            assert_live_boot_allowed()
        except LiveBootRefused as e:
            detail = str(e)
            await set_config("btc_bot.state", "stopped")
            await set_config("btc_bot.mode", "live")
            await set_config("btc_bot.updated_at", now)
            await set_config("btc_bot.detail", detail)
            log.error("btc.live_start_refused", error=detail)
            return await get_status()
    _ensure_runner_started()
    await set_config("btc_bot.state", "running")
    await set_config("btc_bot.mode", mode)
    await set_config("btc_bot.updated_at", now)
    if mode == "live":
        detail = (
            "BTC LIVE loop starting — orders are REAL. It will discover the "
            "current BTC 5m market and place risk-gated CLOB orders."
        )
    else:
        detail = (
            "BTC paper loop starting. It will discover the current BTC 5m "
            "market and log simulated trades only."
        )
    await set_config("btc_bot.detail", detail)
    return await get_status()


async def request_stop() -> BtcBotStatus:
    """Stop the runner and disable new entries (live orders are flattened).

    Ordering matters: the stop event is set FIRST, then we WAIT for the
    runner thread to exit. The runner's own shutdown sequence (on its own
    event loop, the only thread that ever drives the LiveExecutor) cancels
    resting orders and flattens live positions before dropping the executor,
    so no entry can fire after the flatten and no live position can ever be
    paper-closed by this controller. In live mode, any ledger row that is
    still open afterwards means the live exit FAILED — it is left open for
    the operator instead of being closed with fictional paper prices.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    mode = _config.BTC_BOT_MODE
    if _stop_event is not None:
        _stop_event.set()
    runner = _runner_thread
    if runner is not None and runner.is_alive():
        await asyncio.to_thread(runner.join, 90.0)

    if mode == "live":
        remaining = await count_open_positions()
        if runner is not None and runner.is_alive():
            detail = (
                "BTC live loop stop requested but the runner has not finished its "
                "shutdown flatten yet. Do NOT restart until it exits; check logs "
                "and btc_live_orders."
            )
        elif remaining:
            detail = (
                f"BTC live loop stopped, but {remaining} live position(s) could NOT "
                "be flattened and remain OPEN in the ledger. Flatten manually on "
                "Polymarket and check the btc_live_orders journal."
            )
        else:
            detail = (
                "BTC live loop stopped. Resting orders cancelled and open live "
                "positions flattened."
            )
    else:
        closed_count, close_error = await _safe_force_close()
        detail = (
            "BTC paper loop stop requested. New entries are disabled. "
            f"Force-closed {closed_count} open paper position(s)."
        )
        if close_error:
            detail = f"{detail} Force-close check failed: {close_error}"
    await set_config("btc_bot.state", "stopped")
    await set_config("btc_bot.mode", mode)
    await set_config("btc_bot.updated_at", now)
    await set_config("btc_bot.detail", detail)
    return await get_status()


def _ensure_runner_started() -> None:
    global _runner_thread, _stop_event
    with _thread_lock:
        if _runner_thread is not None and _runner_thread.is_alive():
            return
        _stop_event = threading.Event()
        _runner_thread = threading.Thread(
            target=_run_loop_in_thread,
            args=(_stop_event,),
            name="btc-paper-runner",
            daemon=True,
        )
        _runner_thread.start()


def _run_loop_in_thread(stop_event: threading.Event) -> None:
    asyncio.run(run_paper_loop(stop_event))


async def _safe_force_close() -> tuple[int, str | None]:
    try:
        return await force_close_open_positions("STOP_REQUEST"), None
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        log.warning("btc.stop_force_close_failed", error=error)
        return 0, error
