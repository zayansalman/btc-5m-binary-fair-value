"""Live execution on the Polymarket CLOB via py-clob-client.

Safety model
------------
* Boot is gated: live mode refuses to start unless ``POLYMARKET_PRIVATE_KEY``
  is set AND ``BTC_LIVE_CONFIRM == "YES_I_UNDERSTAND"`` AND the wallet config
  is coherent (a funder address is mandatory for proxy signature types 1/2).
* Hard risk gates run BEFORE every order: per-trade notional cap, one open
  position max, daily realized-loss halt, daily bankroll cap. The daily
  counters are PERSISTED in SQLite and rebuilt at boot, so Stop/Start or a
  process restart cannot reset the daily loss halt or grant a fresh bankroll.
* Boot reconciliation: ``start()`` cancels ALL resting CLOB orders on the
  account and re-adopts any open ledger position from the order journal, so
  a crash or restart never silently abandons real tokens or resting orders.
* A kill-switch file (``data/KILL`` by default) blocks all NEW entries and
  cancels resting orders the moment it appears, and re-arms automatically
  when the file is deleted. Exits stay ALLOWED under kill — flattening only
  reduces exposure.
* Exits never rest: the GTC SELL is awaited for a bounded time and cancelled
  if unfilled, so no stale exit order can sit in the book of a 5-minute
  market into resolution. Callers must treat a non-ok exit as "position
  still open — retry".
* Every order/cancel attempt — including blocked ones — is journaled to the
  ``btc_live_orders`` SQLite table.

Threading: a single runner thread owns the executor for its whole life
(entries, exits, kill handling, shutdown flatten). The dashboard controller
never drives it directly, which is what makes Stop race-free.

The private key is never logged and never journaled.
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

# Ensure project root importable (mirrors ops/dashboard/app.py convention).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config as _config
from db import (  # type: ignore[import-untyped]
    connect,
    get_config,
    journal_live_order,
    notify,
    set_config,
)
from logging_setup import get_logger  # type: ignore[import-untyped]

log = get_logger("btc_live")

BUY = "BUY"
SELL = "SELL"
CONFIRM_PHRASE = "YES_I_UNDERSTAND"

# Polymarket CLOB conventions (see installed py_clob_client_v2 source):
# - size granularity is 2 decimals for every tick size (ROUNDING_CONFIG.size == 2)
# - tick sizes are one of 0.1 / 0.01 / 0.001 / 0.0001 (GET /tick-size per token)
# - the order book reports min_order_size in shares (typically 5 for 5m markets)
SIZE_DECIMALS = 2
DEFAULT_TICK_SIZE = 0.01
DEFAULT_MIN_ORDER_SIZE = 5.0

# get_order statuses meaning the order can never trade again.
_TERMINAL_ORDER_STATUSES = {"matched", "canceled", "cancelled"}

# SQLite config keys for the persisted daily risk counters.
_RISK_DATE_KEY = "btc_live.risk_date"
_RISK_PNL_KEY = "btc_live.daily_realized_pnl"
_RISK_NOTIONAL_KEY = "btc_live.daily_buy_notional"


class LiveBootRefused(RuntimeError):
    """Raised when live mode is requested but the boot gate is not satisfied."""


def assert_live_boot_allowed(
    private_key: str | None = None,
    confirm: str | None = None,
    funder: str | None = None,
    signature_type: int | None = None,
) -> None:
    """Refuse live boot unless the operator config is complete AND coherent.

    Reads ``config.POLYMARKET_*`` / ``config.BTC_LIVE_CONFIRM`` at call time
    (not import time) so operators and tests can adjust config. Also refuses
    when any risk-limit env var failed to parse (config.CONFIG_PARSE_ERRORS):
    a typo in a risk limit must never silently degrade to looser defaults.
    """
    key = private_key if private_key is not None else _config.POLYMARKET_PRIVATE_KEY
    phrase = confirm if confirm is not None else _config.BTC_LIVE_CONFIRM
    fund = funder if funder is not None else _config.POLYMARKET_FUNDER
    sig = (
        signature_type
        if signature_type is not None
        else _config.POLYMARKET_SIGNATURE_TYPE
    )
    problems: list[str] = []
    parse_errors = getattr(_config, "CONFIG_PARSE_ERRORS", [])
    if parse_errors:
        problems.append(
            "invalid env value(s): " + "; ".join(parse_errors)
            + " (fix the typo instead of trading on silent defaults)"
        )
    if not key:
        problems.append("POLYMARKET_PRIVATE_KEY is not set")
    if phrase != CONFIRM_PHRASE:
        problems.append(f"BTC_LIVE_CONFIRM is not '{CONFIRM_PHRASE}'")
    if sig not in (0, 1, 2, 3):
        problems.append(
            f"POLYMARKET_SIGNATURE_TYPE={sig} is not one of 0 (EOA), 1 (email/Magic "
            "proxy), 2 (Gnosis Safe), 3 (deposit wallet / ERC-1271)"
        )
    elif sig in (1, 2, 3) and not fund:
        problems.append(
            f"POLYMARKET_FUNDER is required for signature_type={sig} (proxy/deposit "
            "wallet): without it every order is signed with the EOA as maker and "
            "the CLOB rejects it"
        )
    if problems:
        raise LiveBootRefused(
            "Live mode boot REFUSED: " + " and ".join(problems) + ". "
            "Live trading places real orders with real funds; all gates are mandatory. "
            "The bot will NOT fall back to paper mode."
        )


@dataclass
class LiveOrderResult:
    """Outcome of one live order attempt."""

    ok: bool
    status: str  # SUBMITTED / BLOCKED / SKIPPED / ERROR / CANCELLED / UNFILLED / FLAT
    reason: str = ""
    order_id: Optional[str] = None
    price: Optional[float] = None
    size: Optional[float] = None
    notional_usd: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)


def _round_price_to_tick(price: float, tick: float) -> float:
    """Round a price to the nearest valid tick, clamped to [tick, 1 - tick]."""
    if tick <= 0:
        tick = DEFAULT_TICK_SIZE
    decimals = max(0, -int(math.floor(math.log10(tick))))
    rounded = round(round(price / tick) * tick, decimals)
    lower, upper = tick, round(1 - tick, decimals)
    return min(max(rounded, lower), upper)


def _round_size_down(size: float) -> float:
    """Round a share size DOWN to the CLOB size granularity (2 decimals)."""
    factor = 10**SIZE_DECIMALS
    return math.floor(size * factor) / factor


class LiveExecutor:
    """Wraps a (synchronous) ``ClobClient`` behind an async, risk-gated API.

    All network calls run in a worker thread via ``asyncio.to_thread`` so the
    engine's event loop never blocks. A pre-built client can be injected for
    tests; production builds one from config in :meth:`start`.

    Single ownership: only the runner thread that called :meth:`start` may
    drive this object. There is no internal locking by design — the
    controller communicates via the stop event and waits for the runner to
    finish its own shutdown flatten.
    """

    def __init__(
        self,
        private_key: str,
        funder: str = "",
        signature_type: int = 1,
        *,
        host: str | None = None,
        chain_id: int | None = None,
        max_trade_usd: float | None = None,
        daily_loss_halt_usd: float | None = None,
        bankroll_cap_usd: float | None = None,
        max_entry_slippage: float | None = None,
        exit_fill_timeout_seconds: float | None = None,
        kill_switch_path: Path | None = None,
        client: Any | None = None,
    ) -> None:
        if not private_key and client is None:
            raise LiveBootRefused("LiveExecutor requires a private key.")
        self._private_key = private_key  # never logged, never journaled
        self._funder = funder
        self._signature_type = signature_type
        self._host = host or _config.POLYMARKET_CLOB_API
        self._chain_id = chain_id or _config.POLYMARKET_CHAIN_ID
        self.max_trade_usd = (
            max_trade_usd if max_trade_usd is not None else _config.BTC_LIVE_MAX_TRADE_USD
        )
        self.daily_loss_halt_usd = (
            daily_loss_halt_usd
            if daily_loss_halt_usd is not None
            else _config.BTC_LIVE_DAILY_LOSS_HALT_USD
        )
        self.bankroll_cap_usd = (
            bankroll_cap_usd
            if bankroll_cap_usd is not None
            else _config.BTC_LIVE_BANKROLL_CAP_USD
        )
        self.max_entry_slippage = (
            max_entry_slippage
            if max_entry_slippage is not None
            else _config.BTC_LIVE_MAX_ENTRY_SLIPPAGE
        )
        self.exit_fill_timeout_seconds = (
            exit_fill_timeout_seconds
            if exit_fill_timeout_seconds is not None
            else _config.BTC_LIVE_EXIT_FILL_TIMEOUT_SECONDS
        )
        self.kill_switch_path = Path(
            kill_switch_path if kill_switch_path is not None else _config.KILL_SWITCH_PATH
        )

        self._client = client
        self._started = client is not None
        # Risk state (daily, persisted to SQLite — see _load/_persist_risk_state)
        self._daily_buy_notional = 0.0
        self._daily_realized_pnl = 0.0
        self._daily_pnl_date = self._today()
        self._kill_handled = False
        # Position / order tracking (max 1 open position by design)
        self._entry_order_id: Optional[str] = None
        self._entry_token_id: Optional[str] = None
        self._entry_price: Optional[float] = None
        self._entry_size: float = 0.0
        self._entry_matched_size: Optional[float] = None
        self._entry_sold_size: float = 0.0
        self._position_open = False
        # Exit order tracking — only set while an exit SELL might still rest.
        self._exit_order_id: Optional[str] = None
        self._exit_price: Optional[float] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Authenticate, verify reachability, rebuild risk state, reconcile.

        Reconciliation makes restarts safe: all resting CLOB orders from any
        previous session are cancelled, and an open ledger position is
        re-adopted from the order journal so it keeps being managed. If the
        account state cannot be reconciled, boot is REFUSED — the bot never
        trades on top of unknown exposure.
        """
        if self._client is None:
            self._client = await asyncio.to_thread(self._build_client)
        creds = await asyncio.to_thread(self._client.create_or_derive_api_key)
        if creds is None:
            raise LiveBootRefused(
                "Could not create or derive Polymarket CLOB API credentials."
            )
        await asyncio.to_thread(self._client.set_api_creds, creds)
        # Reachability check (raises on network failure).
        await asyncio.to_thread(self._client.get_ok)
        # Refresh the CLOB's cached view of the funder's collateral balance
        # and allowance — the documented step before a first order. Best
        # effort: a refresh failure is logged, and an actually unfunded or
        # unapproved wallet will surface as order rejections (and in
        # tools/live_preflight.py), not as a silent boot failure here.
        try:
            from py_clob_client_v2 import AssetType, BalanceAllowanceParams

            await asyncio.to_thread(
                self._client.update_balance_allowance,
                BalanceAllowanceParams(
                    asset_type=AssetType.COLLATERAL,
                    signature_type=self._signature_type,
                ),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("live_executor.allowance_refresh_failed", error=str(e))
        await self._load_risk_state()
        await self._reconcile_account()
        log.info(
            "live_executor.started",
            host=self._host,
            signature_type=self._signature_type,
            funder_set=bool(self._funder),
            max_trade_usd=self.max_trade_usd,
            daily_loss_halt_usd=self.daily_loss_halt_usd,
            bankroll_cap_usd=self.bankroll_cap_usd,
            daily_realized_pnl=round(self._daily_realized_pnl, 4),
            daily_buy_notional=round(self._daily_buy_notional, 4),
            adopted_position=self._position_open,
            kill_switch=str(self.kill_switch_path),
        )

    def _build_client(self) -> Any:
        # py-clob-client (v1) is archived and non-functional; v2 keeps the
        # same constructor surface incl. signature_type/funder (issue #31).
        from py_clob_client_v2 import ClobClient

        return ClobClient(
            self._host,
            chain_id=self._chain_id,
            key=self._private_key,
            signature_type=self._signature_type,
            funder=self._funder or None,
        )

    async def _reconcile_account(self) -> None:
        """Cancel resting orders from dead sessions and re-adopt open positions."""
        # 1) Cancel ALL resting orders on the account. A resting GTC in the
        # book of a 5-minute market from a dead session is pure downside.
        try:
            raw = await asyncio.to_thread(self._client.cancel_all)
        except Exception as e:  # noqa: BLE001
            raise LiveBootRefused(
                f"Boot reconciliation failed: could not cancel resting orders "
                f"({type(e).__name__}: {e}). Refusing to trade on top of unknown "
                "resting orders."
            ) from e
        await journal_live_order(
            intent="CANCEL_ALL", side="-", status="CANCELLED",
            details={"reason": "BOOT_RECONCILE", "response": raw},
        )

        # 2) Re-adopt any open ledger position so it keeps being managed.
        async with connect() as db:
            async with db.execute(
                "SELECT * FROM btc_paper_positions WHERE state = 'open' ORDER BY opened_at"
            ) as cur:
                open_rows = [dict(r) for r in await cur.fetchall()]
        if not open_rows:
            return
        if len(open_rows) > 1:
            raise LiveBootRefused(
                f"Boot reconciliation failed: {len(open_rows)} open ledger positions "
                "found (max 1 by design). Resolve them manually (flatten on Polymarket, "
                "then UPDATE btc_paper_positions SET state='closed', exit_reason='MANUAL' "
                "for each row) before restarting live mode."
            )
        row = open_rows[0]
        async with connect() as db:
            async with db.execute(
                "SELECT token_id, clob_order_id, price, size FROM btc_live_orders "
                "WHERE intent = 'ENTRY' AND status = 'SUBMITTED' AND window_slug = ? "
                "ORDER BY id DESC LIMIT 1",
                (row["window_slug"],),
            ) as cur:
                entry = await cur.fetchone()

        if entry is None or not entry["token_id"] or not entry["clob_order_id"]:
            # No live order was ever submitted for this row — it is a paper
            # artifact (e.g. from a previous paper session). Closing it costs
            # nothing real.
            await self._close_ledger_row(row, "RECONCILED_NO_LIVE_TRACE")
            log.warning(
                "live_executor.reconcile_closed_paper_row",
                position_id=row["position_id"], window_slug=row["window_slug"],
            )
            return

        try:
            raw_order = await asyncio.to_thread(
                self._client.get_order, entry["clob_order_id"]
            )
            matched = float(raw_order.get("size_matched") or 0.0)
        except Exception as e:  # noqa: BLE001
            raise LiveBootRefused(
                f"Boot reconciliation failed: could not fetch entry order "
                f"{entry['clob_order_id']} for open position "
                f"{row['position_id']} ({type(e).__name__}: {e}). Flatten manually on "
                "Polymarket and close the ledger row, or retry once the CLOB is "
                "reachable."
            ) from e

        if matched <= 0:
            # Entry never filled and its remainder was just cancelled by
            # cancel_all — there is nothing real behind this row.
            await self._close_ledger_row(row, "RECONCILED_UNFILLED")
            log.info(
                "live_executor.reconcile_closed_unfilled",
                position_id=row["position_id"], window_slug=row["window_slug"],
            )
            return

        # Adopt: the resting remainder is already cancelled; track the filled
        # size so the normal exit path flattens it.
        self._entry_order_id = None
        self._entry_token_id = str(entry["token_id"])
        self._entry_price = float(entry["price"] or row["entry_price"])
        self._entry_size = float(entry["size"] or row["shares"])
        self._entry_matched_size = matched
        self._entry_sold_size = 0.0
        self._position_open = True
        await notify(
            "btc_live_reconciled",
            f"Re-adopted open live position from a previous session: "
            f"{matched:.2f} shares of {row['side']} in {row['window_slug']}. "
            "It will be flattened by the normal exit path.",
            {"position_id": row["position_id"]},
        )
        log.warning(
            "live_executor.reconcile_adopted_position",
            position_id=row["position_id"], matched=matched,
            window_slug=row["window_slug"],
        )

    @staticmethod
    async def _close_ledger_row(row: dict[str, Any], reason: str) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE btc_paper_positions SET state = 'closed', closed_at = ?, "
                "exit_reason = ?, realized_pnl_usd = COALESCE(realized_pnl_usd, 0) "
                "WHERE position_id = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"), reason,
                 row["position_id"]),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    def kill_switch_active(self) -> bool:
        return self.kill_switch_path.exists()

    async def enforce_kill_switch(self) -> bool:
        """If the kill file exists, cancel open orders once and halt entries.

        Re-arms automatically when the file is removed, so touching the file
        again later triggers the cancel sweep again. Exits remain allowed
        while the kill switch is active: flattening only reduces exposure.
        """
        if not self.kill_switch_active():
            if self._kill_handled:
                self._kill_handled = False
                log.info("live_executor.kill_switch_rearmed", path=str(self.kill_switch_path))
            return False
        if not self._kill_handled:
            self._kill_handled = True
            log.error("live_executor.kill_switch_triggered", path=str(self.kill_switch_path))
            await notify(
                "btc_live_kill_switch",
                f"KILL switch file detected at {self.kill_switch_path}. "
                "New entries halted; cancelling resting orders. Open positions "
                "will still be flattened by the exit path.",
            )
            await self.cancel_open(reason="KILL_SWITCH")
        return True

    # ------------------------------------------------------------------
    # Risk gates (daily counters persisted to SQLite)
    # ------------------------------------------------------------------

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _roll_daily_window(self) -> None:
        today = self._today()
        if today != self._daily_pnl_date:
            self._daily_pnl_date = today
            self._daily_realized_pnl = 0.0
            self._daily_buy_notional = 0.0

    async def _load_risk_state(self) -> None:
        """Rebuild the daily counters from SQLite so restarts cannot reset them."""
        stored_date = await get_config(_RISK_DATE_KEY)
        if stored_date == self._today():
            try:
                self._daily_realized_pnl = float(await get_config(_RISK_PNL_KEY, "0") or 0)
                self._daily_buy_notional = float(
                    await get_config(_RISK_NOTIONAL_KEY, "0") or 0
                )
            except ValueError:
                log.warning("live_executor.risk_state_unreadable_reset")
                self._daily_realized_pnl = 0.0
                self._daily_buy_notional = 0.0
        self._daily_pnl_date = self._today()
        await self._persist_risk_state()

    async def _persist_risk_state(self) -> None:
        await set_config(_RISK_DATE_KEY, self._daily_pnl_date)
        await set_config(_RISK_PNL_KEY, repr(self._daily_realized_pnl))
        await set_config(_RISK_NOTIONAL_KEY, repr(self._daily_buy_notional))

    async def record_realized_pnl(self, pnl_usd: float) -> None:
        """Feed realized PnL into the daily loss halt tracker (persisted)."""
        self._roll_daily_window()
        self._daily_realized_pnl += pnl_usd
        await self._persist_risk_state()

    async def record_settlement(self, won: bool, window_slug: str) -> LiveOrderResult:
        """Register a resolution outcome for the held position without selling.

        Settle-style positions ride to resolution: winning tokens redeem at
        $1.00 (redemption is an operator action — see the runbook), losing
        tokens expire worthless. PnL feeds the daily-loss halt exactly like
        an exit fill, and the position slot frees for the next window.
        """
        if not self._position_open:
            return LiveOrderResult(
                ok=False, status="SKIPPED", reason="no live position tracked"
            )
        if self._entry_order_id is not None:
            # The market has resolved; any resting entry remainder is dead.
            # Cancel it so boot reconciliation never re-adopts a ghost order.
            await self.cancel_open(reason="SETTLEMENT")
        matched = await self._matched_entry_size()
        held = _round_size_down(max(0.0, matched - self._entry_sold_size))
        entry_price = self._entry_price or 0.0
        payout = 1.0 if won else 0.0
        pnl = round(held * (payout - entry_price), 4)
        if held > 0:
            await self.record_realized_pnl(pnl)
        await journal_live_order(
            intent="SETTLEMENT",
            side=SELL,
            status="SETTLED",
            window_slug=window_slug,
            token_id=self._entry_token_id,
            price=payout,
            size=held,
            notional_usd=pnl,
            error=None if won else "resolved against position; tokens worthless",
        )
        log.info(
            "live_executor.settled",
            window_slug=window_slug,
            won=won,
            held=held,
            pnl=pnl,
        )
        self._clear_position()
        return LiveOrderResult(ok=True, status="SETTLED", price=payout, size=held)

    @property
    def daily_realized_pnl(self) -> float:
        self._roll_daily_window()
        return self._daily_realized_pnl

    @property
    def daily_buy_notional(self) -> float:
        self._roll_daily_window()
        return self._daily_buy_notional

    def entry_block_reason(self, notional_usd: float) -> str | None:
        """Return why a new entry is blocked, or None if all risk gates pass."""
        if self.kill_switch_active():
            return f"KILL switch active at {self.kill_switch_path}"
        self._roll_daily_window()
        if self._daily_realized_pnl <= -self.daily_loss_halt_usd:
            return (
                f"daily loss halt: realized {self._daily_realized_pnl:+.2f} USD "
                f"breaches -{self.daily_loss_halt_usd:.2f} USD"
            )
        if self._position_open or self._entry_order_id is not None:
            return "an open live position/order already exists (max 1)"
        if notional_usd <= 0:
            return "notional must be positive"
        if notional_usd > self.max_trade_usd:
            return (
                f"per-trade cap: {notional_usd:.2f} USD exceeds "
                f"{self.max_trade_usd:.2f} USD"
            )
        if self._daily_buy_notional + notional_usd > self.bankroll_cap_usd:
            return (
                f"daily bankroll cap: {self._daily_buy_notional:.2f} + "
                f"{notional_usd:.2f} USD exceeds {self.bankroll_cap_usd:.2f} USD"
            )
        return None

    # ------------------------------------------------------------------
    # Market metadata helpers
    # ------------------------------------------------------------------

    async def _book_context(self, token_id: str) -> tuple[float | None, float | None, float, float]:
        """Return (best_ask, best_bid, tick_size, min_order_size) for a token.

        py-clob-client order books list levels from worst to best, so the best
        ask/bid is the LAST element (see builder.calculate_*_market_price).
        Falls back to safe defaults when the book is unavailable.
        """
        best_ask: float | None = None
        best_bid: float | None = None
        tick = DEFAULT_TICK_SIZE
        min_size = DEFAULT_MIN_ORDER_SIZE
        try:
            book = await asyncio.to_thread(self._client.get_order_book, token_id)
        except Exception as e:  # noqa: BLE001
            log.warning("live_executor.order_book_failed", error=str(e))
            return best_ask, best_bid, tick, min_size
        try:
            if book.asks:
                best_ask = float(book.asks[-1].price)
            if book.bids:
                best_bid = float(book.bids[-1].price)
            if book.tick_size:
                tick = float(book.tick_size)
            if book.min_order_size:
                min_size = float(book.min_order_size)
        except (TypeError, ValueError) as e:
            log.warning("live_executor.order_book_parse_failed", error=str(e))
        return best_ask, best_bid, tick, min_size

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def submit_entry(
        self,
        token_id: str,
        side_price: float,
        notional_usd: float,
        window_slug: str | None = None,
    ) -> LiveOrderResult:
        """Place a GTC limit BUY at the best ask, sized from *notional_usd*.

        Runs every risk gate first; blocked attempts are journaled with
        status BLOCKED and never reach the network. A slippage guard blocks
        the entry when the live ask has moved too far above the signal price
        that justified the trade.
        """
        blocked = self.entry_block_reason(notional_usd)
        if blocked is not None:
            await self._journal_blocked(
                intent="ENTRY", side=BUY, reason=blocked,
                window_slug=window_slug, token_id=token_id,
                notional_usd=notional_usd,
            )
            return LiveOrderResult(ok=False, status="BLOCKED", reason=blocked)
        if not token_id:
            reason = "no CLOB token id available for this market/outcome"
            await self._journal_blocked(
                intent="ENTRY", side=BUY, reason=reason,
                window_slug=window_slug, notional_usd=notional_usd,
            )
            return LiveOrderResult(ok=False, status="BLOCKED", reason=reason)

        best_ask, _, tick, min_size = await self._book_context(token_id)
        if (
            best_ask is not None
            and side_price > 0
            and best_ask - side_price > self.max_entry_slippage
        ):
            reason = (
                f"entry slippage guard: book ask {best_ask:.3f} is "
                f"{best_ask - side_price:+.3f} above the signal price "
                f"{side_price:.3f} (max {self.max_entry_slippage:.3f}); "
                "the edge that justified this trade no longer exists"
            )
            await self._journal_blocked(
                intent="ENTRY", side=BUY, reason=reason,
                window_slug=window_slug, token_id=token_id,
                price=best_ask, notional_usd=notional_usd,
            )
            return LiveOrderResult(ok=False, status="BLOCKED", reason=reason)
        raw_price = best_ask if best_ask is not None else side_price
        price = _round_price_to_tick(raw_price, tick)
        size = _round_size_down(notional_usd / price)
        if size < min_size:
            reason = (
                f"size {size:.2f} shares below Polymarket minimum {min_size:.2f} "
                f"at price {price:.4f} (notional {notional_usd:.2f} USD)"
            )
            await self._journal_blocked(
                intent="ENTRY", side=BUY, reason=reason,
                window_slug=window_slug, token_id=token_id,
                price=price, size=size, notional_usd=notional_usd,
            )
            return LiveOrderResult(ok=False, status="BLOCKED", reason=reason)

        result = await self._place_order(
            intent="ENTRY", token_id=token_id, side=BUY,
            price=price, size=size, window_slug=window_slug,
        )
        if result.ok:
            self._entry_order_id = result.order_id
            self._entry_token_id = token_id
            self._entry_price = price
            self._entry_size = size
            self._entry_matched_size = None
            self._entry_sold_size = 0.0
            self._position_open = True
            self._daily_buy_notional += round(price * size, 4)
            await self._persist_risk_state()
        return result

    async def submit_exit(
        self,
        token_id: str | None = None,
        side_price: float | None = None,
        size: float | None = None,
        window_slug: str | None = None,
    ) -> LiveOrderResult:
        """Flatten the FILLED entry size with a GTC limit SELL at the best bid.

        Allowed even while the kill switch is active — flattening only reduces
        exposure. The SELL is awaited for a bounded time and cancelled if it
        does not fill, so no stale exit order ever rests in the book of a
        5-minute market. Realized PnL is recorded here, on confirmed fills at
        the exit order's limit price — never on submission alone.

        Returns ok=True only when the position is confirmed flat (or was
        confirmed never-filled, status SKIPPED is returned with ok=False but
        nothing real remains). Callers MUST treat any other non-ok result as
        "real tokens may still be held — keep the position open and retry".
        """
        token = token_id or self._entry_token_id
        if not token:
            reason = (
                "no live entry tracked; cannot flatten "
                "(restart reconciliation should have adopted it — manual check required)"
            )
            await journal_live_order(
                intent="EXIT", side=SELL, status="ERROR",
                window_slug=window_slug, error=reason,
            )
            log.error("live_executor.exit_untracked", window_slug=window_slug)
            return LiveOrderResult(ok=False, status="ERROR", reason=reason)

        # Clear any stale exit SELL from a previous attempt whose cancel
        # failed, so we never have two exit orders working at once.
        if self._exit_order_id is not None:
            stale_id, stale_price = self._exit_order_id, self._exit_price
            if not await self._try_cancel(stale_id, reason="EXIT_RETRY"):
                return LiveOrderResult(
                    ok=False, status="ERROR",
                    reason="could not cancel stale exit order; will retry",
                )
            sold, order_price = await self._order_fill_info(stale_id, default_size=0.0)
            self._exit_order_id = None
            self._exit_price = None
            await self._register_exit_fill(sold, stale_price or order_price)

        # Cancel any still-resting entry remainder before flattening, so the
        # bot is never buying and selling the same token simultaneously.
        if self._entry_order_id is not None:
            await self.cancel_open(reason="EXIT_FLATTEN")
            if self._entry_order_id is not None:
                return LiveOrderResult(
                    ok=False, status="ERROR",
                    reason="could not cancel resting entry order; exit deferred",
                )

        matched = await self._matched_entry_size()
        remaining = _round_size_down(max(0.0, matched - self._entry_sold_size))
        sellable = remaining if size is None else _round_size_down(min(size, remaining))
        if sellable <= 0:
            if matched <= 0:
                self._clear_position()
                reason = "entry order has no matched size; nothing to sell"
                await journal_live_order(
                    intent="EXIT", side=SELL, status="SKIPPED",
                    window_slug=window_slug, token_id=token, error=reason,
                )
                return LiveOrderResult(ok=False, status="SKIPPED", reason=reason)
            # Earlier partial exits already sold everything that filled.
            self._clear_position()
            await journal_live_order(
                intent="EXIT", side=SELL, status="FLAT",
                window_slug=window_slug, token_id=token,
                error="already fully flattened by earlier exits",
            )
            return LiveOrderResult(
                ok=True, status="FLAT", reason="already fully flattened", size=0.0
            )

        _, best_bid, tick, _ = await self._book_context(token)
        raw_price = best_bid if best_bid is not None else (side_price or 0.0)
        if raw_price <= 0:
            reason = "no bid available to price the exit"
            await self._journal_blocked(
                intent="EXIT", side=SELL, reason=reason,
                window_slug=window_slug, token_id=token, size=sellable,
            )
            return LiveOrderResult(ok=False, status="BLOCKED", reason=reason)
        price = _round_price_to_tick(raw_price, tick)

        result = await self._place_order(
            intent="EXIT", token_id=token, side=SELL,
            price=price, size=sellable, window_slug=window_slug,
        )
        if not result.ok:
            return result

        # Track the resting SELL until it is confirmed filled or cancelled.
        self._exit_order_id = result.order_id
        self._exit_price = price
        filled = await self._await_fill(result.order_id, sellable)
        if filled >= sellable:
            self._exit_order_id = None
            self._exit_price = None
            await self._register_exit_fill(sellable, price)
            if self._entry_sold_size >= matched:
                self._clear_position()
            return result

        # Not filled in time: cancel so no stale SELL rests into resolution.
        if not await self._try_cancel(result.order_id, reason="EXIT_FILL_TIMEOUT"):
            # Cancel failed — keep tracking the order id so the next attempt
            # clears it before placing a new SELL (no double exposure).
            return LiveOrderResult(
                ok=False, status="UNFILLED",
                reason="exit SELL not filled in time and cancel failed; will retry",
                order_id=result.order_id, price=price, size=0.0,
            )
        final, _ = await self._order_fill_info(result.order_id, default_size=0.0)
        self._exit_order_id = None
        self._exit_price = None
        await self._register_exit_fill(final, price)
        if matched > 0 and self._entry_sold_size >= matched:
            # The order actually filled completely during the cancel race.
            self._clear_position()
            return LiveOrderResult(
                ok=True, status="SUBMITTED", reason="filled during cancel race",
                order_id=result.order_id, price=price, size=final,
                notional_usd=round(price * final, 4),
            )
        await journal_live_order(
            intent="EXIT", side=SELL, status="UNFILLED",
            window_slug=window_slug, token_id=token,
            price=price, size=final, order_type="GTC",
            clob_order_id=result.order_id,
            error=f"exit SELL filled {final:.2f}/{sellable:.2f} before timeout-cancel",
        )
        return LiveOrderResult(
            ok=False, status="UNFILLED",
            reason=f"exit SELL filled only {final:.2f}/{sellable:.2f} shares; will retry",
            order_id=result.order_id, price=price, size=final,
        )

    async def cancel_open(self, reason: str = "CANCEL_REQUEST") -> list[str]:
        """Cancel tracked resting orders (entry, and any stale exit).

        The matched (filled) size of the entry is captured AFTER the cancel
        is confirmed, so a fill landing during the cancel round-trip is still
        counted and the follow-up exit flattens the right amount. The unfilled
        remainder's notional is credited back to the daily bankroll cap.
        On cancel failure the order id stays tracked so a later attempt can
        retry — the order is never silently forgotten while possibly live.
        """
        cancelled: list[str] = []
        # Stale exit order first (only set if an exit cancel failed earlier).
        if self._exit_order_id is not None:
            stale_id, stale_price = self._exit_order_id, self._exit_price
            if await self._try_cancel(stale_id, reason=reason):
                cancelled.append(stale_id)
                sold, order_price = await self._order_fill_info(stale_id, default_size=0.0)
                self._exit_order_id = None
                self._exit_price = None
                await self._register_exit_fill(sold, stale_price or order_price)

        order_id = self._entry_order_id
        if order_id is None:
            return cancelled
        if not await self._try_cancel(order_id, reason=reason):
            return cancelled
        cancelled.append(order_id)
        matched, _ = await self._order_fill_info(order_id, default_size=self._entry_size)
        self._entry_matched_size = matched
        unfilled = max(0.0, self._entry_size - matched)
        if unfilled > 0 and self._entry_price:
            self._daily_buy_notional = max(
                0.0, self._daily_buy_notional - round(unfilled * self._entry_price, 4)
            )
            await self._persist_risk_state()
        self._entry_order_id = None
        return cancelled

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _clear_position(self) -> None:
        self._entry_order_id = None
        self._entry_token_id = None
        self._entry_price = None
        self._entry_size = 0.0
        self._entry_matched_size = None
        self._entry_sold_size = 0.0
        self._position_open = False
        self._exit_order_id = None
        self._exit_price = None

    async def _register_exit_fill(self, sold_size: float, exit_price: float | None) -> None:
        """Account a confirmed exit fill: track sold shares, record realized PnL."""
        if sold_size <= 0:
            return
        self._entry_sold_size = round(self._entry_sold_size + sold_size, SIZE_DECIMALS)
        entry_px = self._entry_price or 0.0
        px = exit_price if exit_price is not None else entry_px
        await self.record_realized_pnl(sold_size * (px - entry_px))

    async def _try_cancel(self, order_id: str, reason: str) -> bool:
        """Cancel one order; True only when it is confirmed no longer live.

        Inspects the DELETE response body ({"canceled": [...], "not_canceled":
        {...}}) instead of trusting a non-exception. An order reported
        not-canceled is re-checked via get_order: a terminal status (already
        matched/cancelled) also counts as "no longer live".
        """
        try:
            from py_clob_client_v2 import OrderPayload

            raw = await asyncio.to_thread(
                self._client.cancel_order, OrderPayload(orderID=order_id)
            )
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            await journal_live_order(
                intent="CANCEL", side="-", status="ERROR",
                token_id=self._entry_token_id, clob_order_id=order_id, error=error,
                details={"reason": reason},
            )
            log.warning("live_executor.cancel_failed", order_id=order_id, error=error)
            return False
        if isinstance(raw, dict):
            canceled_ids = raw.get("canceled") or []
            if order_id not in canceled_ids:
                status = await self._order_status(order_id)
                if status not in _TERMINAL_ORDER_STATUSES:
                    error = f"cancel not confirmed (status={status or 'unknown'})"
                    await journal_live_order(
                        intent="CANCEL", side="-", status="ERROR",
                        token_id=self._entry_token_id, clob_order_id=order_id,
                        error=error, details={"reason": reason, "response": raw},
                    )
                    log.warning(
                        "live_executor.cancel_not_confirmed",
                        order_id=order_id, response=str(raw),
                    )
                    return False
        await journal_live_order(
            intent="CANCEL", side="-", status="CANCELLED",
            token_id=self._entry_token_id, clob_order_id=order_id,
            details={"reason": reason, "response": raw},
        )
        log.info("live_executor.order_cancelled", order_id=order_id, reason=reason)
        return True

    async def _order_status(self, order_id: str) -> str:
        try:
            raw = await asyncio.to_thread(self._client.get_order, order_id)
            return str(raw.get("status") or "").lower()
        except Exception:  # noqa: BLE001
            return ""

    async def _order_fill_info(
        self, order_id: str, default_size: float
    ) -> tuple[float, float | None]:
        """(size_matched, limit_price) for an order; default on lookup failure."""
        try:
            raw = await asyncio.to_thread(self._client.get_order, order_id)
        except Exception as e:  # noqa: BLE001
            log.warning("live_executor.get_order_failed", error=str(e))
            return default_size, None
        try:
            matched = float(raw.get("size_matched") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return default_size, None
        try:
            price = float(raw.get("price")) if raw.get("price") else None
        except (TypeError, ValueError):
            price = None
        return matched, price

    async def _matched_entry_size(self) -> float:
        """Matched (filled) share size of the tracked entry order."""
        if self._entry_order_id is None:
            return self._entry_matched_size or 0.0
        # On lookup failure assume fully filled: an oversized SELL is rejected
        # by the exchange and retried, while underselling strands real tokens.
        matched, _ = await self._order_fill_info(
            self._entry_order_id, default_size=self._entry_size
        )
        return matched

    async def _await_fill(self, order_id: str | None, target_size: float) -> float:
        """Poll an order's matched size until *target_size* or timeout."""
        if order_id is None:
            return 0.0
        deadline = time.monotonic() + max(0.0, self.exit_fill_timeout_seconds)
        while True:
            matched, _ = await self._order_fill_info(order_id, default_size=0.0)
            if matched >= target_size:
                return matched
            now = time.monotonic()
            if now >= deadline:
                return matched
            await asyncio.sleep(min(0.5, deadline - now))

    async def _place_order(
        self,
        intent: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        window_slug: str | None,
    ) -> LiveOrderResult:
        from py_clob_client_v2 import OrderArgs

        notional = round(price * size, 4)
        # Last-instant kill re-check for ENTRIES: the gate ran before the
        # order-book round-trip, and a kill file appearing in that window must
        # still stop the buy. Exits stay allowed (they reduce exposure).
        if side == BUY and self.kill_switch_active():
            reason = f"KILL switch appeared before posting at {self.kill_switch_path}"
            await self._journal_blocked(
                intent=intent, side=side, reason=reason,
                window_slug=window_slug, token_id=token_id,
                price=price, size=size, notional_usd=notional,
            )
            return LiveOrderResult(
                ok=False, status="BLOCKED", reason=reason,
                price=price, size=size, notional_usd=notional,
            )
        args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
        try:
            raw = await asyncio.to_thread(self._client.create_and_post_order, args)
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            await journal_live_order(
                intent=intent, side=side, status="ERROR",
                window_slug=window_slug, token_id=token_id,
                price=price, size=size, notional_usd=notional,
                order_type="GTC", error=error,
            )
            log.warning("live_executor.order_failed", intent=intent, error=error)
            return LiveOrderResult(
                ok=False, status="ERROR", reason=error,
                price=price, size=size, notional_usd=notional,
            )

        response = raw if isinstance(raw, dict) else {}
        order_id = response.get("orderID") or response.get("orderId")
        success = bool(response.get("success", order_id is not None))
        status = "SUBMITTED" if success else "ERROR"
        error = None if success else str(response.get("errorMsg") or response)
        await journal_live_order(
            intent=intent, side=side, status=status,
            window_slug=window_slug, token_id=token_id,
            price=price, size=size, notional_usd=notional,
            order_type="GTC", clob_order_id=order_id, error=error,
            details={"response": response},
        )
        log.info(
            "live_executor.order_submitted" if success else "live_executor.order_rejected",
            intent=intent, side=side, price=price, size=size,
            notional=notional, order_id=order_id,
        )
        return LiveOrderResult(
            ok=success, status=status, reason=error or "",
            order_id=order_id, price=price, size=size,
            notional_usd=notional, raw=response,
        )

    async def _journal_blocked(
        self,
        intent: str,
        side: str,
        reason: str,
        window_slug: str | None = None,
        token_id: str | None = None,
        price: float | None = None,
        size: float | None = None,
        notional_usd: float | None = None,
    ) -> None:
        await journal_live_order(
            intent=intent, side=side, status="BLOCKED",
            window_slug=window_slug, token_id=token_id,
            price=price, size=size, notional_usd=notional_usd, error=reason,
        )
        log.warning("live_executor.order_blocked", intent=intent, reason=reason)


def build_live_executor() -> LiveExecutor:
    """Build a LiveExecutor from config after passing the boot gate."""
    assert_live_boot_allowed()
    return LiveExecutor(
        private_key=_config.POLYMARKET_PRIVATE_KEY,
        funder=_config.POLYMARKET_FUNDER,
        signature_type=_config.POLYMARKET_SIGNATURE_TYPE,
    )
