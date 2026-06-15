"""Venue-independent pre-trade risk gate (issue #64).

Both paper and live entries go through the SAME ``RiskGate`` so paper is a
faithful preview of live: what paper blocks, live would have blocked; what
paper opens, live would have opened (modulo the actual CLOB order placement,
which is the only live-only concern).

The gate owns the persisted daily counters (date, realized PnL, cumulative
BUY notional) in SQLite. They are venue-independent — both paper closes and
live closes feed them — so every gate that consults a counter (the daily
realized-loss halt, the optional bankroll cap) advances identically in both
modes.

State lives under the ``btc_risk.*`` config keys. Legacy ``btc_live.*`` keys
written before issue #64 are read once at boot and migrated in place; after
the first persist they are no longer referenced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from db import get_config, set_config  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Persisted-state keys
# ---------------------------------------------------------------------------

# Current keys (issue #64). Generic — fed by paper closes and live closes.
_RISK_DATE_KEY = "btc_risk.date"
_RISK_PNL_KEY = "btc_risk.daily_realized_pnl"
_RISK_NOTIONAL_KEY = "btc_risk.daily_buy_notional"

# Legacy keys, written by the live-only counter before issue #64. Read once at
# boot if the new keys are absent, then never touched again — the next persist
# writes the new keys exclusively.
_LEGACY_DATE_KEY = "btc_live.risk_date"
_LEGACY_PNL_KEY = "btc_live.daily_realized_pnl"
_LEGACY_NOTIONAL_KEY = "btc_live.daily_buy_notional"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateConfig:
    """Static gate configuration. Identical instance shared by paper and live."""

    max_trade_usd: float
    daily_loss_halt_usd: float
    bankroll_cap_usd: Optional[float]  # None / ≤0 → cap disabled
    max_entry_slippage: float
    kill_switch_path: Path


@dataclass(frozen=True)
class EntryRequest:
    """What the caller is asking permission to do.

    ``best_ask`` and ``side_price`` together drive the slippage guard. When
    either is ``None`` (e.g. the book is unavailable), the guard does not
    contribute a block reason — other gates still run.
    """

    notional_usd: float
    position_open: bool  # any open ledger / live position
    entry_order_resting: bool  # live: unfilled entry order resting in the book
    side_price: Optional[float]  # the price the signal was computed against
    best_ask: Optional[float]  # the live ask AT FILL TIME


# ---------------------------------------------------------------------------
# RiskGate
# ---------------------------------------------------------------------------


class RiskGate:
    """Pre-trade gate + persisted daily counters, shared by paper and live.

    All gate logic lives here so paper and live cannot diverge by accident.
    Counters are persisted to SQLite (``btc_risk.*``) and rebuilt at boot so
    Stop/Start or a process restart never resets the daily-loss halt or
    silently grants a fresh bankroll when the cap is enabled.
    """

    def __init__(self, cfg: GateConfig) -> None:
        self.cfg = cfg
        self._date = self._today()
        self._daily_realized_pnl: float = 0.0
        self._daily_buy_notional: float = 0.0
        self._kill_handled = False
        self._loaded = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _roll_daily_window(self) -> None:
        today = self._today()
        if today != self._date:
            self._date = today
            self._daily_realized_pnl = 0.0
            self._daily_buy_notional = 0.0

    async def load(self) -> None:
        """Rebuild counters from SQLite.

        Reads the new ``btc_risk.*`` keys first. If they are absent (boot
        after upgrading past issue #64), falls back to the legacy
        ``btc_live.*`` keys once. After ``persist()`` runs, only the new keys
        are written; the legacy keys go stale and are ignored on subsequent
        boots.
        """
        date = await get_config(_RISK_DATE_KEY)
        pnl_raw = await get_config(_RISK_PNL_KEY)
        notional_raw = await get_config(_RISK_NOTIONAL_KEY)
        if date is None and pnl_raw is None and notional_raw is None:
            # First boot after the rename: migrate from legacy keys if present.
            date = await get_config(_LEGACY_DATE_KEY)
            pnl_raw = await get_config(_LEGACY_PNL_KEY)
            notional_raw = await get_config(_LEGACY_NOTIONAL_KEY)
        if date == self._today():
            try:
                self._daily_realized_pnl = float(pnl_raw or 0)
                self._daily_buy_notional = float(notional_raw or 0)
            except ValueError:
                self._daily_realized_pnl = 0.0
                self._daily_buy_notional = 0.0
        self._date = self._today()
        self._loaded = True
        await self.persist()

    async def persist(self) -> None:
        await set_config(_RISK_DATE_KEY, self._date)
        await set_config(_RISK_PNL_KEY, repr(self._daily_realized_pnl))
        await set_config(_RISK_NOTIONAL_KEY, repr(self._daily_buy_notional))

    # ------------------------------------------------------------------
    # Counters — fed by BOTH paper closes and live closes
    # ------------------------------------------------------------------

    async def record_realized_pnl(self, pnl_usd: float) -> None:
        self._roll_daily_window()
        self._daily_realized_pnl += pnl_usd
        await self.persist()

    async def record_buy_notional(self, notional_usd: float) -> None:
        self._roll_daily_window()
        self._daily_buy_notional += notional_usd
        await self.persist()

    @property
    def daily_realized_pnl(self) -> float:
        self._roll_daily_window()
        return self._daily_realized_pnl

    @property
    def daily_buy_notional(self) -> float:
        self._roll_daily_window()
        return self._daily_buy_notional

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    def kill_switch_active(self) -> bool:
        return self.cfg.kill_switch_path.exists()

    def mark_kill_handled(self) -> None:
        self._kill_handled = True

    def kill_already_handled(self) -> bool:
        return self._kill_handled

    def rearm_kill(self) -> None:
        self._kill_handled = False

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------

    def block_reason(self, req: EntryRequest) -> str | None:
        """Return why this entry is blocked, or ``None`` if all gates pass.

        Order matters only for the error message — the FIRST tripped gate is
        reported. The set of gates is canonical: every entry, paper or live,
        gets the same answer for the same inputs.
        """
        if self.kill_switch_active():
            return f"KILL switch active at {self.cfg.kill_switch_path}"
        self._roll_daily_window()
        if self._daily_realized_pnl <= -self.cfg.daily_loss_halt_usd:
            return (
                f"daily loss halt: realized {self._daily_realized_pnl:+.2f} USD "
                f"breaches -{self.cfg.daily_loss_halt_usd:.2f} USD"
            )
        if req.position_open or req.entry_order_resting:
            return "an open position/order already exists (max 1)"
        if req.notional_usd <= 0:
            return "notional must be positive"
        if req.notional_usd > self.cfg.max_trade_usd:
            return (
                f"per-trade cap: {req.notional_usd:.2f} USD exceeds "
                f"{self.cfg.max_trade_usd:.2f} USD"
            )
        if (
            self.cfg.bankroll_cap_usd is not None
            and self.cfg.bankroll_cap_usd > 0
            and self._daily_buy_notional + req.notional_usd > self.cfg.bankroll_cap_usd
        ):
            return (
                f"daily bankroll cap: {self._daily_buy_notional:.2f} + "
                f"{req.notional_usd:.2f} USD exceeds "
                f"{self.cfg.bankroll_cap_usd:.2f} USD"
            )
        if (
            req.best_ask is not None
            and req.side_price is not None
            and req.side_price > 0
            and req.best_ask - req.side_price > self.cfg.max_entry_slippage
        ):
            return (
                f"entry slippage guard: book ask {req.best_ask:.3f} is "
                f"{req.best_ask - req.side_price:+.3f} above the signal price "
                f"{req.side_price:.3f} (max {self.cfg.max_entry_slippage:.3f}); "
                "the edge that justified this trade no longer exists"
            )
        return None


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def build_gate_from_config() -> RiskGate:
    """Build a RiskGate from the global ``config`` module.

    The single source of truth: paper and live both use this so their gate
    configs cannot drift. The persisted state is NOT loaded here — callers
    must ``await gate.load()`` once before the first ``block_reason()``.
    """
    import config as _config  # type: ignore[import-untyped]

    cfg = GateConfig(
        max_trade_usd=_config.BTC_TRADE_MAX_USD,
        daily_loss_halt_usd=_config.BTC_TRADE_DAILY_LOSS_HALT_USD,
        bankroll_cap_usd=_config.BTC_TRADE_BANKROLL_CAP_USD,
        max_entry_slippage=_config.BTC_TRADE_MAX_ENTRY_SLIPPAGE,
        kill_switch_path=Path(_config.KILL_SWITCH_PATH),
    )
    return RiskGate(cfg)
