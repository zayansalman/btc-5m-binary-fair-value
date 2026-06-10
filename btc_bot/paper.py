"""BTC 5-minute trading engine (paper by default, live opt-in).

It discovers the current Polymarket BTC 5m market, computes a simple
volatility-band fair probability from BTC spot, and records entries/exits in
SQLite. In paper mode (the default) no real orders are ever placed. When
``BTC_BOT_MODE=live`` AND the live boot gate passes (private key +
``BTC_LIVE_CONFIRM=YES_I_UNDERSTAND``), entries/exits are ALSO routed through
:class:`btc_5m_fv.execution.live.LiveExecutor`, which places real risk-gated
orders on the Polymarket CLOB.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from config import (
    BINANCE_API_BASE,
    BTC_CHAINLINK_STREAM_URL,
    BTC_MARKET_TIMEFRAME_MINUTES,
    BTC_PAPER_ENTRY_EDGE_MIN,
    BTC_PAPER_ENTRY_MIN_REMAINING_SECONDS,
    BTC_PAPER_MAX_TRADE_USD,
    BTC_PAPER_MIN_CONFIDENCE,
    BTC_PAPER_MIN_TRADE_USD,
    BTC_PAPER_STOP_RETURN,
    BTC_PAPER_TARGET_RETURN,
    BTC_PAPER_TICK_SECONDS,
    BTC_PAPER_TIME_EXIT_SECONDS,
    POLYMARKET_GAMMA_API,
)
import config as _config
from db import connect, notify, set_config
from logging_setup import get_logger
from btc_5m_fv.execution.live import LiveExecutor, build_live_executor
from btc_bot.strategy import (
    StrategyParams,
    fair_up_probability,
    sigma_per_second,
    signal_from_edge,
)

log = get_logger("btc_paper")

# Live executor for the current run loop. None means pure paper mode.
_live_executor: LiveExecutor | None = None

BINANCE_API = BINANCE_API_BASE
FIVE_MINUTES = BTC_MARKET_TIMEFRAME_MINUTES * 60
STRATEGY_PARAMS = StrategyParams(
    min_trade_usd=BTC_PAPER_MIN_TRADE_USD,
    max_trade_usd=BTC_PAPER_MAX_TRADE_USD,
    entry_edge_min=BTC_PAPER_ENTRY_EDGE_MIN,
    min_confidence=BTC_PAPER_MIN_CONFIDENCE,
    entry_min_remaining_seconds=BTC_PAPER_ENTRY_MIN_REMAINING_SECONDS,
)


@dataclass
class PaperSnapshot:
    created_at: str
    window_slug: str
    market_question: str
    remaining_seconds: int
    spot_price: float
    reference_price: float
    sigma_per_second: float
    market_up_price: float
    market_down_price: float
    fair_up_prob: float
    edge: float
    signal_side: str | None
    confidence: float
    notional_usd: float
    reason: str
    feed_source: str
    up_token_id: str = ""
    down_token_id: str = ""


@dataclass
class PaperSummary:
    running_state: str
    open_positions: int
    closed_positions: int
    total_pnl_usd: float
    open_exposure_usd: float
    closed_notional_usd: float
    win_rate: float | None
    avg_pnl_usd: float | None
    avg_hold_seconds: float | None
    risk_state: str
    last_signal: str
    last_tick_at: str | None
    last_window_slug: str | None
    last_spot_price: float | None
    last_fair_up_prob: float | None
    last_up_price: float | None
    last_edge: float | None
    last_feed_source: str | None
    recent_positions: list[dict[str, Any]]


async def run_paper_loop(stop_event: threading.Event) -> None:
    """Run until Stop is pressed or the process exits.

    Mode comes from ``BTC_BOT_MODE``: ``paper`` (default) journals simulated
    trades only; ``live`` ALSO routes entries/exits through the risk-gated
    LiveExecutor. Live boot refusal stops the loop — it never silently falls
    back to paper.
    """
    global _live_executor
    mode = _config.BTC_BOT_MODE
    if mode == "live":
        try:
            executor = build_live_executor()
            await executor.start()
        except Exception as e:  # noqa: BLE001 — includes LiveBootRefused
            error = f"{type(e).__name__}: {e!s}"
            await set_config("btc_bot.state", "stopped")
            await set_config("btc_bot.mode", "live")
            await _set_detail(
                f"LIVE mode refused to start: {error} "
                "The bot did NOT fall back to paper mode and is not running."
            )
            await notify("btc_live_boot_refused", f"Live mode boot refused: {error}")
            log.error("live_loop.boot_refused", error=error)
            return
        _live_executor = executor

    await set_config("btc_bot.state", "running")
    await set_config("btc_bot.mode", mode)
    if mode == "live":
        await _set_detail(
            "BTC LIVE loop running — orders are REAL. "
            f"Kill switch: touch {_config.KILL_SWITCH_PATH} to halt and cancel."
        )
        await notify("btc_live_started", "BTC LIVE bot started — orders are real")
    else:
        await _set_detail("BTC paper loop running. No real orders will be placed.")
        await notify("btc_paper_started", "BTC paper bot started")
    log.info("paper_loop.started", mode=mode)

    try:
        while not stop_event.is_set():
            try:
                snapshot = await paper_tick_once()
                await _set_detail(_detail_from_snapshot(snapshot))
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e!s}"
                log.warning("paper_loop.tick_failed", error=error)
                await _set_detail(f"BTC {mode} loop tick failed: {error}")
            await _sleep_interruptible(stop_event, float(BTC_PAPER_TICK_SECONDS))
    finally:
        if _live_executor is not None:
            # Flatten BEFORE dropping the executor: this thread owns it, so
            # Stop can never paper-close a live position (which would strand
            # real tokens on the exchange with a ledger that says flat).
            try:
                await _live_executor.cancel_open(reason="LOOP_STOP")
            except Exception as e:  # noqa: BLE001
                log.warning("live_loop.stop_cancel_failed", error=str(e))
            try:
                await force_close_open_positions("STOP_REQUEST")
            except Exception as e:  # noqa: BLE001
                log.warning("live_loop.stop_flatten_failed", error=str(e))
            _live_executor = None
        await set_config("btc_bot.state", "stopped")
        await _set_detail(f"BTC {mode} loop stopped. No new entries will be opened.")
        await notify("btc_paper_stopped", f"BTC {mode} bot stopped")
        log.info("paper_loop.stopped", mode=mode)


async def paper_tick_once() -> PaperSnapshot:
    """One trading tick. Useful for tests and dashboard-driven smoke checks."""
    kill_active = False
    if _live_executor is not None:
        # Kill switch is checked every tick BEFORE any order can be placed.
        kill_active = await _live_executor.enforce_kill_switch()
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        snapshot = await _build_snapshot(client)
    await _log_tick(snapshot)
    await _close_due_positions(snapshot)
    if not kill_active:
        await _maybe_open_position(snapshot)
    return snapshot


async def force_close_open_positions(exit_reason: str = "STOP_REQUEST") -> int:
    """Close all open positions; returns how many actually closed.

    In live mode a position only counts as closed when the executor confirmed
    the flatten — failed live exits keep their ledger rows OPEN so they are
    retried (or escalated to the operator) instead of stranding real tokens.
    """
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM btc_paper_positions WHERE state = 'open' ORDER BY opened_at"
        ) as cur:
            positions = [dict(r) for r in await cur.fetchall()]
    if not positions:
        return 0
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        snapshot = await _build_snapshot(client)
    closed = 0
    for pos in positions:
        if await _close_position(
            pos,
            snapshot,
            _current_price_for_side(snapshot, pos["side"]),
            exit_reason,
        ):
            closed += 1
    return closed


async def count_open_positions() -> int:
    """Number of open rows in the position ledger."""
    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM btc_paper_positions WHERE state = 'open'"
        ) as cur:
            return int((await cur.fetchone())["n"])


async def load_paper_summary() -> PaperSummary:
    """Dashboard summary from the SQLite paper ledger."""
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM btc_paper_ticks ORDER BY created_at DESC LIMIT 1"
        ) as cur:
            tick = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(notional_usd), 0) AS exposure "
            "FROM btc_paper_positions WHERE state = 'open'"
        ) as cur:
            open_row = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(realized_pnl_usd), 0) AS pnl, "
            "COALESCE(SUM(notional_usd), 0) AS notional, "
            "SUM(CASE WHEN realized_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins, "
            "AVG(realized_pnl_usd) AS avg_pnl, "
            "AVG(strftime('%s', closed_at) - strftime('%s', opened_at)) AS avg_hold "
            "FROM btc_paper_positions WHERE state = 'closed'"
        ) as cur:
            closed = await cur.fetchone()
        async with db.execute(
            "SELECT * FROM btc_paper_positions ORDER BY opened_at DESC LIMIT 10"
        ) as cur:
            recent = [dict(r) for r in await cur.fetchall()]

    last_signal = "none"
    if tick is not None:
        side = tick["signal_side"] or "SKIP"
        conf = tick["confidence"] if tick["confidence"] is not None else 0.0
        notional = tick["notional_usd"] if tick["notional_usd"] is not None else 0.0
        last_signal = f"{side} conf {conf:.2f} ${notional:.0f}: {tick['reason']}"

    open_count = int(open_row["n"] if open_row else 0)
    closed_count = int(closed["n"] if closed else 0)
    wins = int(closed["wins"] or 0) if closed else 0
    win_rate = (wins / closed_count) if closed_count else None
    avg_pnl = float(closed["avg_pnl"]) if closed and closed["avg_pnl"] is not None else None
    avg_hold = float(closed["avg_hold"]) if closed and closed["avg_hold"] is not None else None
    risk_state = _risk_state(open_count, tick["created_at"] if tick else None)

    return PaperSummary(
        running_state="paper",
        open_positions=open_count,
        closed_positions=closed_count,
        total_pnl_usd=float(closed["pnl"] if closed else 0.0),
        open_exposure_usd=float(open_row["exposure"] if open_row else 0.0),
        closed_notional_usd=float(closed["notional"] if closed else 0.0),
        win_rate=win_rate,
        avg_pnl_usd=avg_pnl,
        avg_hold_seconds=avg_hold,
        risk_state=risk_state,
        last_signal=last_signal,
        last_tick_at=tick["created_at"] if tick else None,
        last_window_slug=tick["window_slug"] if tick else None,
        last_spot_price=float(tick["spot_price"]) if tick else None,
        last_fair_up_prob=float(tick["fair_up_prob"]) if tick else None,
        last_up_price=float(tick["market_up_price"]) if tick else None,
        last_edge=float(tick["edge"]) if tick else None,
        last_feed_source=tick["feed_source"] if tick else None,
        recent_positions=recent,
    )


def _risk_state(open_positions: int, last_tick_at: str | None) -> str:
    if open_positions > 1:
        return "BREACH: more than one open BTC paper position"
    if last_tick_at is None:
        return "IDLE: no ticks yet"
    try:
        ts = datetime.fromisoformat(last_tick_at.replace("Z", "+00:00"))
    except ValueError:
        return "UNKNOWN: bad tick timestamp"
    age = (datetime.now(UTC) - ts).total_seconds()
    if age > max(BTC_PAPER_TICK_SECONDS * 3, 20):
        return f"STALE: last tick {int(age)}s ago"
    return "OK"


async def _build_snapshot(client: httpx.AsyncClient) -> PaperSnapshot:
    now = int(time.time())
    market = await _fetch_current_market(client, now)
    start_ts = int(market["window_start_ts"])
    slug = str(market["slug"])
    question = str(market.get("question") or slug)
    remaining = max(0, start_ts + FIVE_MINUTES - now)

    spot, closes = await _fetch_spot_and_recent_closes(client)
    reference = await _fetch_reference_price(client, start_ts)
    sigma = sigma_per_second(closes)
    up_price, down_price = _outcome_prices(market)
    up_token_id, down_token_id = _outcome_token_ids(market)
    fair_up = fair_up_probability(spot, reference, sigma, remaining)
    edge = fair_up - up_price
    side, confidence, notional, reason = signal_from_edge(
        edge,
        remaining,
        up_price,
        down_price,
        STRATEGY_PARAMS,
    )

    return PaperSnapshot(
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        window_slug=slug,
        market_question=question,
        remaining_seconds=remaining,
        spot_price=spot,
        reference_price=reference,
        sigma_per_second=sigma,
        market_up_price=up_price,
        market_down_price=down_price,
        fair_up_prob=fair_up,
        edge=edge,
        signal_side=side,
        confidence=confidence,
        notional_usd=notional,
        reason=reason,
        feed_source=f"binance_public_fallback; chainlink_target={BTC_CHAINLINK_STREAM_URL}",
        up_token_id=up_token_id,
        down_token_id=down_token_id,
    )


async def _fetch_current_market(client: httpx.AsyncClient, now: int) -> dict[str, Any]:
    current_start = now - (now % FIVE_MINUTES)
    # Try current first, then next and previous to handle boundary/API timing.
    for start_ts in (current_start, current_start + FIVE_MINUTES, current_start - FIVE_MINUTES):
        slug = f"btc-updown-5m-{start_ts}"
        data = await _gamma_get(client, "markets", {"slug": slug})
        market = _first(data)
        if market is None:
            event_data = await _gamma_get(client, "events", {"slug": slug})
            event = _first(event_data)
            markets = event.get("markets") if event else None
            market = markets[0] if isinstance(markets, list) and markets else None
        if market is None:
            continue
        market["window_start_ts"] = start_ts
        return market
    raise RuntimeError("Could not discover current BTC 5-minute Polymarket market.")


async def _gamma_get(
    client: httpx.AsyncClient, endpoint: str, params: dict[str, Any]
) -> Any:
    r = await client.get(f"{POLYMARKET_GAMMA_API}/{endpoint}", params=params)
    r.raise_for_status()
    return r.json()


async def _fetch_spot_and_recent_closes(client: httpx.AsyncClient) -> tuple[float, list[float]]:
    r = await client.get(
        f"{BINANCE_API}/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "1s", "limit": 90},
    )
    r.raise_for_status()
    rows = r.json()
    closes = [float(row[4]) for row in rows if len(row) > 4]
    if not closes:
        raise RuntimeError("Binance returned no BTC closes.")
    return closes[-1], closes


async def _fetch_reference_price(client: httpx.AsyncClient, window_start_ts: int) -> float:
    r = await client.get(
        f"{BINANCE_API}/api/v3/klines",
        params={
            "symbol": "BTCUSDT",
            "interval": "1s",
            "startTime": window_start_ts * 1000,
            "limit": 1,
        },
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError("Binance returned no BTC window reference candle.")
    return float(rows[0][4])


def _outcome_prices(market: dict[str, Any]) -> tuple[float, float]:
    prices = _json_list(market.get("outcomePrices"))
    outcomes = _json_list(market.get("outcomes"))
    if len(prices) != 2:
        raise RuntimeError("BTC market did not expose two outcome prices.")
    up_idx = 0
    if len(outcomes) == 2:
        labels = [str(x).lower() for x in outcomes]
        if "up" in labels:
            up_idx = labels.index("up")
    down_idx = 1 - up_idx
    return float(prices[up_idx]), float(prices[down_idx])


def _outcome_token_ids(market: dict[str, Any]) -> tuple[str, str]:
    """(up_token_id, down_token_id) from the gamma market's clobTokenIds.

    The clobTokenIds list is aligned with the outcomes list. Returns empty
    strings when unavailable — live entries are then blocked, never guessed.
    """
    token_ids = _json_list(market.get("clobTokenIds"))
    if len(token_ids) != 2:
        return "", ""
    outcomes = _json_list(market.get("outcomes"))
    up_idx = 0
    if len(outcomes) == 2:
        labels = [str(x).lower() for x in outcomes]
        if "up" in labels:
            up_idx = labels.index("up")
    return str(token_ids[up_idx]), str(token_ids[1 - up_idx])


def _token_id_for_side(snapshot: PaperSnapshot, side: str) -> str:
    return snapshot.up_token_id if side == "Up" else snapshot.down_token_id


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _first(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value:
        first = value[0]
        return first if isinstance(first, dict) else None
    return None


async def _log_tick(snapshot: PaperSnapshot) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO btc_paper_ticks(
              created_at, window_slug, market_question, remaining_seconds,
              spot_price, reference_price, sigma_per_second, market_up_price,
              market_down_price, fair_up_prob, edge, signal_side, confidence,
              notional_usd, feed_source, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.created_at,
                snapshot.window_slug,
                snapshot.market_question,
                snapshot.remaining_seconds,
                snapshot.spot_price,
                snapshot.reference_price,
                snapshot.sigma_per_second,
                snapshot.market_up_price,
                snapshot.market_down_price,
                snapshot.fair_up_prob,
                snapshot.edge,
                snapshot.signal_side,
                snapshot.confidence,
                snapshot.notional_usd,
                snapshot.feed_source,
                snapshot.reason,
            ),
        )
        await db.commit()


async def _maybe_open_position(snapshot: PaperSnapshot) -> None:
    if not snapshot.signal_side or snapshot.notional_usd <= 0:
        return
    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM btc_paper_positions WHERE state = 'open'"
        ) as cur:
            if (await cur.fetchone())["n"]:
                return
    entry_price = (
        snapshot.market_up_price
        if snapshot.signal_side == "Up"
        else snapshot.market_down_price
    )
    notional = snapshot.notional_usd
    shares = notional / entry_price

    executor = _live_executor
    if executor is not None:
        # Live mode: the ledger row is written BEFORE the real order goes
        # out. Every failure mode of this ordering is benign: a failed
        # submit deletes the row (or, if even that write fails, the row is
        # later closed as a confirmed zero-fill), whereas submitting first
        # could leave a REAL position with no ledger row — unmanaged by
        # every exit path.
        position_id = await _insert_position_row(
            snapshot, entry_price, notional, shares
        )
        result = await executor.submit_entry(
            token_id=_token_id_for_side(snapshot, snapshot.signal_side),
            side_price=entry_price,
            notional_usd=notional,
            window_slug=snapshot.window_slug,
        )
        if not result.ok:
            # Blocked/error — journaled in btc_live_orders. Remove the
            # provisional row so the ledger mirrors live intent.
            await _delete_position_row(position_id)
            return
        entry_price = result.price or entry_price
        notional = result.notional_usd or notional
        shares = result.size or (notional / entry_price)
        await _update_position_terms(position_id, entry_price, notional, shares)
    else:
        await _insert_position_row(snapshot, entry_price, notional, shares)
    label = "LIVE" if executor is not None else "Paper"
    await notify(
        "btc_live_entry" if executor is not None else "btc_paper_entry",
        f"{label} BUY {snapshot.signal_side} ${notional:.2f} @ {entry_price:.3f}",
        {"window_slug": snapshot.window_slug, "confidence": snapshot.confidence},
    )
    log.info(
        "paper_position.opened",
        mode="live" if executor is not None else "paper",
        window_slug=snapshot.window_slug,
        side=snapshot.signal_side,
        notional=notional,
        entry_price=entry_price,
    )


async def _insert_position_row(
    snapshot: PaperSnapshot, entry_price: float, notional: float, shares: float
) -> int:
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO btc_paper_positions(
              opened_at, window_slug, market_question, side, state, entry_price,
              notional_usd, shares, opened_spot, confidence, edge, entry_reason,
              feed_source
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.created_at,
                snapshot.window_slug,
                snapshot.market_question,
                snapshot.signal_side,
                entry_price,
                notional,
                shares,
                snapshot.spot_price,
                snapshot.confidence,
                snapshot.edge,
                snapshot.reason,
                snapshot.feed_source,
            ),
        )
        position_id = int(cur.lastrowid or 0)
        await db.commit()
    return position_id


async def _delete_position_row(position_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "DELETE FROM btc_paper_positions WHERE position_id = ?", (position_id,)
        )
        await db.commit()


async def _update_position_terms(
    position_id: int, entry_price: float, notional: float, shares: float
) -> None:
    """Overwrite a provisional row with the terms the executor actually used."""
    async with connect() as db:
        await db.execute(
            "UPDATE btc_paper_positions SET entry_price = ?, notional_usd = ?, "
            "shares = ? WHERE position_id = ?",
            (entry_price, notional, shares, position_id),
        )
        await db.commit()


async def _close_due_positions(snapshot: PaperSnapshot) -> None:
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM btc_paper_positions WHERE state = 'open' ORDER BY opened_at"
        ) as cur:
            positions = [dict(r) for r in await cur.fetchall()]

    for pos in positions:
        exit_price = _current_price_for_side(snapshot, pos["side"])
        reason = _exit_reason(snapshot, pos, exit_price)
        if reason is None:
            continue
        await _close_position(pos, snapshot, exit_price, reason)


async def _close_position(
    pos: dict[str, Any], snapshot: PaperSnapshot, exit_price: float, reason: str
) -> bool:
    """Close one position; returns True when the ledger row was closed.

    Live mode only closes the row when the executor CONFIRMED the flatten
    (or confirmed the entry never filled). A blocked/failed/unfilled live
    exit keeps the row open so it is retried on the next tick — the ledger
    must never claim flat while real tokens remain on the exchange. Realized
    PnL for the daily loss halt is recorded inside the executor on confirmed
    fills, never here from paper prices.
    """
    executor = _live_executor
    entry_price = float(pos["entry_price"])
    prior_pnl = float(pos["realized_pnl_usd"] or 0.0)
    if executor is not None:
        # Window roll / band re-entry must first cancel any unfilled resting
        # entry order before flattening whatever did fill.
        if reason in ("WINDOW_ROLL", "BAND_REENTRY"):
            await executor.cancel_open(reason=reason)
        exit_result = await executor.submit_exit(
            side_price=exit_price,
            size=float(pos["shares"]),
            window_slug=pos["window_slug"],
        )
        if not exit_result.ok and exit_result.status != "SKIPPED":
            # BLOCKED / ERROR / UNFILLED: real tokens may still be held.
            # Record any partial fill into the row, keep it OPEN, and retry.
            tranche_size = exit_result.size or 0.0
            if tranche_size > 0:
                tranche_price = (
                    exit_result.price if exit_result.price is not None else exit_price
                )
                prior_pnl += tranche_size * (tranche_price - entry_price)
                async with connect() as db:
                    await db.execute(
                        "UPDATE btc_paper_positions SET realized_pnl_usd = ? "
                        "WHERE position_id = ?",
                        (prior_pnl, pos["position_id"]),
                    )
                    await db.commit()
            log.warning(
                "live_exit.failed_position_kept_open",
                position_id=pos["position_id"],
                window_slug=pos["window_slug"],
                status=exit_result.status,
                reason=exit_result.reason,
            )
            return False
        if exit_result.status == "SKIPPED":
            # Confirmed: the entry never filled, so nothing real existed.
            pnl = prior_pnl
        else:
            sold = exit_result.size or 0.0
            if exit_result.price is not None:
                exit_price = exit_result.price
            pnl = prior_pnl + sold * (exit_price - entry_price)
    else:
        pnl = float(pos["shares"]) * (exit_price - entry_price)
    async with connect() as db:
        await db.execute(
            """
            UPDATE btc_paper_positions
            SET state = 'closed', closed_at = ?, exit_price = ?,
                closed_spot = ?, exit_reason = ?, realized_pnl_usd = ?
            WHERE position_id = ?
            """,
            (
                snapshot.created_at,
                exit_price,
                snapshot.spot_price,
                reason,
                pnl,
                pos["position_id"],
            ),
        )
        await db.commit()
    await notify(
        "btc_live_exit" if executor is not None else "btc_paper_exit",
        f"{'LIVE' if executor is not None else 'Paper'} EXIT {pos['side']} ${pnl:+.2f} ({reason})",
        {"window_slug": pos["window_slug"], "position_id": pos["position_id"]},
    )
    log.info(
        "paper_position.closed",
        mode="live" if executor is not None else "paper",
        position_id=pos["position_id"],
        window_slug=pos["window_slug"],
        side=pos["side"],
        pnl=round(pnl, 4),
        exit_reason=reason,
    )
    return True


def _current_price_for_side(snapshot: PaperSnapshot, side: str) -> float:
    return snapshot.market_up_price if side == "Up" else snapshot.market_down_price


def _exit_reason(snapshot: PaperSnapshot, pos: dict[str, Any], exit_price: float) -> str | None:
    if pos["window_slug"] != snapshot.window_slug:
        return "WINDOW_ROLL"
    entry_price = float(pos["entry_price"])
    notional = float(pos["notional_usd"])
    shares = float(pos["shares"])
    pnl = shares * (exit_price - entry_price)
    if snapshot.remaining_seconds <= BTC_PAPER_TIME_EXIT_SECONDS:
        return "TIME"
    if pnl >= notional * BTC_PAPER_TARGET_RETURN:
        return "TARGET"
    if pnl <= notional * BTC_PAPER_STOP_RETURN:
        return "STOP"
    if abs(snapshot.edge) < BTC_PAPER_ENTRY_EDGE_MIN / 2:
        return "BAND_REENTRY"
    return None


async def _set_detail(detail: str) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    await set_config("btc_bot.updated_at", now)
    await set_config("btc_bot.detail", detail)


def _detail_from_snapshot(snapshot: PaperSnapshot) -> str:
    side = snapshot.signal_side or "SKIP"
    if _live_executor is not None:
        header = (
            "BTC LIVE loop running — orders are REAL. "
            f"Kill switch: touch {_config.KILL_SWITCH_PATH}.\n\n"
        )
    else:
        header = "BTC paper loop running. No real orders are placed.\n\n"
    return header + (
        f"Window: {snapshot.window_slug} ({snapshot.remaining_seconds}s left)\n"
        f"Spot: ${snapshot.spot_price:,.2f} vs ref ${snapshot.reference_price:,.2f}\n"
        f"Polymarket Up: {snapshot.market_up_price:.3f}; fair Up: {snapshot.fair_up_prob:.3f}; "
        f"edge: {snapshot.edge:+.3f}\n"
        f"Signal: {side}; confidence {snapshot.confidence:.2f}; notional ${snapshot.notional_usd:.0f}\n"
        f"Feed: Binance public fallback while Chainlink Streams access is pending."
    )


async def _sleep_interruptible(stop_event: threading.Event, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event.is_set():
            return
        await asyncio.sleep(min(0.25, deadline - time.monotonic()))
