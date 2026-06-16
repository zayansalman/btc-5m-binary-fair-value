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
_RISK_NOTIONAL_KEY = "btc_risk.daily_buy_notional"
# Split counters (issue #67): paper bypass mode would otherwise pollute the
# operator's view of real-money PnL. The gate halts on the SUM (live+paper)
# for parity, so paper losses still drive the halt in non-bypass mode; the
# dashboard reads each leg separately to label live vs study clearly.
_RISK_LIVE_PNL_KEY = "btc_risk.live_realized_pnl"
_RISK_PAPER_PNL_KEY = "btc_risk.paper_realized_pnl"
# Pre-split combined key (issue #64). Read once on migration into the live
# bucket — by far the most common pre-split scenario was a live-only counter.
_RISK_COMBINED_PNL_KEY = "btc_risk.daily_realized_pnl"

# Paper-only study overrides (#65). Only honoured when the gate was built
# with ``allow_money_overrides=True`` — live's gate ignores them by
# construction so nobody can ever disable a hard money limit via the UI.
_BYPASS_LOSS_HALT_KEY = "btc_risk.paper_bypass_loss_halt"

# Operator runtime risk knobs (#50). Unlike the paper-only bypass above, these
# are tuning controls that apply in BOTH modes (the operator wants to resize
# the clip mid-session without a restart). Persisted in the config table and
# re-read every tick via ``refresh_runtime_limits``. Unset → fall back to the
# env/config default, so absence is fully backward-compatible.
_RUNTIME_MAX_TRADE_KEY = "btc_runtime.max_trade_usd"

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

    def __init__(self, cfg: GateConfig, *, allow_overrides: bool = False) -> None:
        self.cfg = cfg
        self._date = self._today()
        # Split PnL counters (issue #67). Gate halts on the SUM; dashboard
        # shows each leg separately so the operator can tell real-money PnL
        # apart from paper study runs.
        self._live_pnl: float = 0.0
        self._paper_pnl: float = 0.0
        self._daily_buy_notional: float = 0.0
        self._kill_handled = False
        self._loaded = False
        # Paper builds with True (lets the operator disable the loss halt for
        # study runs); live builds with False so the override is structurally
        # impossible to apply to real funds.
        self.allow_overrides = allow_overrides
        # Cached override state — refreshed by ``refresh_overrides`` on each
        # tick so the dashboard toggle takes effect immediately without
        # restarting the loop.
        self._bypass_loss_halt = False
        # Operator runtime per-trade cap override (#50). None → use the env
        # default (cfg.max_trade_usd). Refreshed every tick by
        # ``refresh_runtime_limits`` so the dashboard control applies without a
        # restart, in BOTH paper and live (this is a tuning knob, not a
        # safety-loosening override like the loss-halt bypass).
        self._runtime_max_trade_usd: float | None = None

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
            self._live_pnl = 0.0
            self._paper_pnl = 0.0
            self._daily_buy_notional = 0.0

    async def load(self) -> None:
        """Rebuild counters from SQLite.

        Reads the split ``btc_risk.{live,paper}_realized_pnl`` keys first.
        If either is absent, falls back through:
          1. The pre-split combined ``btc_risk.daily_realized_pnl`` (issue #64)
             — its value is migrated INTO the live bucket on the assumption
             that pre-split state was almost always live-only PnL.
          2. The legacy ``btc_live.*`` keys (pre-#64).
        After ``persist()`` runs, only the new split keys are written; the
        older keys are ignored on subsequent boots.
        """
        date = await get_config(_RISK_DATE_KEY)
        live_pnl_raw = await get_config(_RISK_LIVE_PNL_KEY)
        paper_pnl_raw = await get_config(_RISK_PAPER_PNL_KEY)
        notional_raw = await get_config(_RISK_NOTIONAL_KEY)
        # Migration ladder: split keys → combined #64 key → legacy #20 keys.
        if live_pnl_raw is None and paper_pnl_raw is None:
            combined_raw = await get_config(_RISK_COMBINED_PNL_KEY)
            if combined_raw is not None:
                live_pnl_raw = combined_raw
            elif date is None and notional_raw is None:
                date = await get_config(_LEGACY_DATE_KEY)
                live_pnl_raw = await get_config(_LEGACY_PNL_KEY)
                notional_raw = await get_config(_LEGACY_NOTIONAL_KEY)
        if date == self._today():
            try:
                self._live_pnl = float(live_pnl_raw or 0)
                self._paper_pnl = float(paper_pnl_raw or 0)
                self._daily_buy_notional = float(notional_raw or 0)
            except ValueError:
                self._live_pnl = 0.0
                self._paper_pnl = 0.0
                self._daily_buy_notional = 0.0
        self._date = self._today()
        self._loaded = True
        await self.persist()

    async def persist(self) -> None:
        await set_config(_RISK_DATE_KEY, self._date)
        await set_config(_RISK_LIVE_PNL_KEY, repr(self._live_pnl))
        await set_config(_RISK_PAPER_PNL_KEY, repr(self._paper_pnl))
        await set_config(_RISK_NOTIONAL_KEY, repr(self._daily_buy_notional))

    async def refresh_overrides(self) -> None:
        """Re-read paper-only override flags from SQLite (no-op when disabled)."""
        if not self.allow_overrides:
            self._bypass_loss_halt = False
            return
        self._bypass_loss_halt = (await get_config(_BYPASS_LOSS_HALT_KEY)) == "1"

    @property
    def bypass_loss_halt(self) -> bool:
        """Live always sees False; paper sees whatever the toggle is set to."""
        return self.allow_overrides and self._bypass_loss_halt

    async def refresh_runtime_limits(self) -> None:
        """Re-read operator runtime risk knobs from SQLite (BOTH paper and live).

        Distinct from ``refresh_overrides``: that gates a paper-only,
        safety-loosening toggle behind ``allow_overrides``. The per-trade cap
        is a tuning knob the operator expects to apply everywhere, so this runs
        regardless of mode. A blank / unset / non-numeric / ≤0 value clears the
        override and the gate falls back to the env default.
        """
        raw = await get_config(_RUNTIME_MAX_TRADE_KEY)
        if raw is None or raw.strip() == "":
            self._runtime_max_trade_usd = None
            return
        try:
            value = float(raw)
        except ValueError:
            self._runtime_max_trade_usd = None
            return
        self._runtime_max_trade_usd = value if value > 0 else None

    @property
    def runtime_max_trade_usd(self) -> float | None:
        """The operator-set per-trade cap override, or None when unset (raw)."""
        return self._runtime_max_trade_usd

    @property
    def effective_max_trade_usd(self) -> float:
        """The per-trade cap in force: runtime override when set, else env default."""
        if self._runtime_max_trade_usd is not None:
            return self._runtime_max_trade_usd
        return self.cfg.max_trade_usd

    # ------------------------------------------------------------------
    # Counters — fed by BOTH paper closes and live closes
    # ------------------------------------------------------------------

    async def record_realized_pnl(self, pnl_usd: float, *, is_live: bool) -> None:
        """Add realized PnL to the right bucket. Both feed the halt sum.

        ``is_live`` is the only call-site distinction — paper closes pass
        False so their PnL stays separated for dashboard reporting. The halt
        decision uses the SUM so paper losses still count toward the halt
        in non-bypass mode (preserves #64 parity).
        """
        self._roll_daily_window()
        if is_live:
            self._live_pnl += pnl_usd
        else:
            self._paper_pnl += pnl_usd
        await self.persist()

    async def record_buy_notional(self, notional_usd: float) -> None:
        self._roll_daily_window()
        self._daily_buy_notional += notional_usd
        await self.persist()

    @property
    def live_pnl(self) -> float:
        self._roll_daily_window()
        return self._live_pnl

    @property
    def paper_pnl(self) -> float:
        self._roll_daily_window()
        return self._paper_pnl

    @property
    def daily_realized_pnl(self) -> float:
        """Combined PnL — drives the halt decision; backwards-compat surface."""
        self._roll_daily_window()
        return self._live_pnl + self._paper_pnl

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
        combined_pnl = self._live_pnl + self._paper_pnl
        if (
            not self.bypass_loss_halt
            and combined_pnl <= -self.cfg.daily_loss_halt_usd
        ):
            return (
                f"daily loss halt: realized {combined_pnl:+.2f} USD "
                f"breaches -{self.cfg.daily_loss_halt_usd:.2f} USD"
            )
        if req.position_open or req.entry_order_resting:
            return "an open position/order already exists (max 1)"
        if req.notional_usd <= 0:
            return "notional must be positive"
        cap = self.effective_max_trade_usd
        if req.notional_usd > cap:
            return (
                f"per-trade cap: {req.notional_usd:.2f} USD exceeds "
                f"{cap:.2f} USD"
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


def build_gate_from_config(*, allow_overrides: bool = False) -> RiskGate:
    """Build a RiskGate from the global ``config`` module.

    The single source of truth: paper and live both use this so their gate
    configs cannot drift. The persisted state is NOT loaded here — callers
    must ``await gate.load()`` once before the first ``block_reason()``.

    ``allow_overrides=True`` is paper-mode only: it lets the dashboard
    operator disable specific gates for study runs. Live always passes
    ``False`` so paper toggles can never affect real funds.
    """
    import config as _config  # type: ignore[import-untyped]

    cfg = GateConfig(
        max_trade_usd=_config.BTC_TRADE_MAX_USD,
        daily_loss_halt_usd=_config.BTC_TRADE_DAILY_LOSS_HALT_USD,
        bankroll_cap_usd=_config.BTC_TRADE_BANKROLL_CAP_USD,
        max_entry_slippage=_config.BTC_TRADE_MAX_ENTRY_SLIPPAGE,
        kill_switch_path=Path(_config.KILL_SWITCH_PATH),
    )
    return RiskGate(cfg, allow_overrides=allow_overrides)


async def set_paper_bypass_loss_halt(enabled: bool) -> None:
    """Persist the paper-mode loss-halt bypass (no effect in live mode)."""
    await set_config(_BYPASS_LOSS_HALT_KEY, "1" if enabled else "0")


async def get_paper_bypass_loss_halt() -> bool:
    return (await get_config(_BYPASS_LOSS_HALT_KEY)) == "1"


async def set_runtime_max_trade_usd(value: float | None) -> None:
    """Persist the operator runtime per-trade cap (#50).

    ``None`` (or ≤0) clears the override so the gate falls back to the env
    default. The live loop re-reads this every tick, so the change takes effect
    without a restart, in both paper and live.
    """
    if value is None or value <= 0:
        await set_config(_RUNTIME_MAX_TRADE_KEY, "")
    else:
        await set_config(_RUNTIME_MAX_TRADE_KEY, repr(float(value)))


async def get_runtime_max_trade_usd() -> float | None:
    """The persisted runtime per-trade cap, or None when unset/invalid."""
    raw = await get_config(_RUNTIME_MAX_TRADE_KEY)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


