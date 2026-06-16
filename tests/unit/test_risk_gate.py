"""Paper/live gate parity tests (issue #64).

Both paper and live consult the SAME :class:`RiskGate`. These tests pin the
canonical decision for a fixed table of scenarios so a future change to the
gate trips a failure in both modes — never just one. Drift is the bug the
unified gate exists to prevent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest
import pytest_asyncio

import db as _db
from btc_5m_fv.execution.gate import EntryRequest, GateConfig, RiskGate


@pytest_asyncio.fixture(autouse=True)
async def _isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every gate test gets its own throwaway SQLite so the real journal is untouched."""
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "test_gate.db")
    await _db.init_db()
    yield


def _cfg(
    *,
    max_trade_usd: float = 5.0,
    daily_loss_halt_usd: float = 10.0,
    bankroll_cap_usd: Optional[float] = None,
    max_entry_slippage: float = 0.02,
    kill_switch_path: Path | None = None,
) -> GateConfig:
    return GateConfig(
        max_trade_usd=max_trade_usd,
        daily_loss_halt_usd=daily_loss_halt_usd,
        bankroll_cap_usd=bankroll_cap_usd,
        max_entry_slippage=max_entry_slippage,
        kill_switch_path=kill_switch_path or Path("/does/not/exist"),
    )


def _req(
    *,
    notional_usd: float = 3.0,
    position_open: bool = False,
    entry_order_resting: bool = False,
    side_price: float | None = 0.55,
    best_ask: float | None = 0.55,
) -> EntryRequest:
    return EntryRequest(
        notional_usd=notional_usd,
        position_open=position_open,
        entry_order_resting=entry_order_resting,
        side_price=side_price,
        best_ask=best_ask,
    )


class TestGateDecisionTable:
    """Table-driven scenarios: each row must give the SAME verdict in both modes.

    The test instantiates two independent gates (one we treat as "paper",
    one as "live") with the same config and inputs, applies the same
    pre-conditions to each, and asserts identical verdicts. Identical config
    + identical inputs MUST yield identical decisions.
    """

    def test_all_pass(self) -> None:
        gate = RiskGate(_cfg())
        assert gate.block_reason(_req()) is None

    def test_kill_switch_blocks(self, tmp_path: Path) -> None:
        kill = tmp_path / "KILL"
        kill.write_text("halt")
        gate_paper = RiskGate(_cfg(kill_switch_path=kill))
        gate_live = RiskGate(_cfg(kill_switch_path=kill))
        assert gate_paper.block_reason(_req()) == gate_live.block_reason(_req())
        assert "KILL switch active" in (gate_live.block_reason(_req()) or "")

    @pytest.mark.asyncio
    async def test_daily_loss_halt_blocks_both_modes(self) -> None:
        gate_paper = RiskGate(_cfg(daily_loss_halt_usd=10.0))
        gate_live = RiskGate(_cfg(daily_loss_halt_usd=10.0))
        # Both feed the same loss into their counters.
        await gate_paper.record_realized_pnl(-10.5, is_live=False)
        await gate_live.record_realized_pnl(-10.5, is_live=True)
        assert gate_paper.block_reason(_req()) == gate_live.block_reason(_req())
        assert "daily loss halt" in (gate_live.block_reason(_req()) or "")

    def test_position_open_blocks(self) -> None:
        gate = RiskGate(_cfg())
        msg = gate.block_reason(_req(position_open=True))
        assert msg == "an open position/order already exists (max 1)"

    def test_resting_entry_blocks(self) -> None:
        gate = RiskGate(_cfg())
        msg = gate.block_reason(_req(entry_order_resting=True))
        assert msg == "an open position/order already exists (max 1)"

    def test_per_trade_cap_blocks(self) -> None:
        gate = RiskGate(_cfg(max_trade_usd=3.0))
        msg = gate.block_reason(_req(notional_usd=5.0))
        assert msg is not None and "per-trade cap" in msg

    def test_negative_notional_blocks(self) -> None:
        gate = RiskGate(_cfg())
        assert gate.block_reason(_req(notional_usd=0.0)) == "notional must be positive"

    @pytest.mark.asyncio
    async def test_bankroll_cap_blocks_when_set(self) -> None:
        gate = RiskGate(_cfg(bankroll_cap_usd=10.0))
        await gate.record_buy_notional(8.0)
        msg = gate.block_reason(_req(notional_usd=5.0))
        assert msg is not None and "daily bankroll cap" in msg

    @pytest.mark.asyncio
    async def test_bankroll_cap_disabled_when_none(self) -> None:
        gate = RiskGate(_cfg(bankroll_cap_usd=None))
        await gate.record_buy_notional(50.0)
        assert gate.block_reason(_req(notional_usd=5.0)) is None

    def test_slippage_blocks_when_book_moved_away(self) -> None:
        gate = RiskGate(_cfg(max_entry_slippage=0.02))
        # Book ask is 0.60 but the signal was generated at 0.55 → 5c slippage.
        msg = gate.block_reason(_req(side_price=0.55, best_ask=0.60))
        assert msg is not None and "slippage guard" in msg

    def test_slippage_passes_when_book_steady(self) -> None:
        gate = RiskGate(_cfg(max_entry_slippage=0.02))
        # Within tolerance.
        assert gate.block_reason(_req(side_price=0.55, best_ask=0.56)) is None

    def test_slippage_skipped_when_book_unavailable(self) -> None:
        gate = RiskGate(_cfg(max_entry_slippage=0.02))
        # Book unavailable: other gates still run but slippage does not block.
        assert gate.block_reason(_req(side_price=0.55, best_ask=None)) is None


class TestPaperLiveCounterParity:
    """Realized PnL and buy notional advance the SAME persisted counters."""

    @pytest.mark.asyncio
    async def test_paper_pnl_advances_halt(self) -> None:
        gate = RiskGate(_cfg(daily_loss_halt_usd=10.0))
        await gate.record_realized_pnl(-4.0, is_live=False)
        assert gate.block_reason(_req()) is None  # not yet at halt
        await gate.record_realized_pnl(-6.5, is_live=False)  # cumulative -10.5
        msg = gate.block_reason(_req())
        assert msg is not None and "daily loss halt" in msg

    @pytest.mark.asyncio
    async def test_buy_notional_credit_back_releases_cap(self) -> None:
        gate = RiskGate(_cfg(bankroll_cap_usd=10.0))
        await gate.record_buy_notional(8.0)
        # 8 + 5 > 10 → blocked.
        assert "bankroll cap" in (gate.block_reason(_req(notional_usd=5.0)) or "")
        # Credit back $4 (e.g. partial cancel).
        await gate.record_buy_notional(-4.0)
        # 4 + 5 ≤ 10 → unblocked.
        assert gate.block_reason(_req(notional_usd=5.0)) is None


class TestPnlSplit:
    """Issue #67: live and paper PnL track separately, halt sums both."""

    @pytest.mark.asyncio
    async def test_live_paper_separate_buckets(self) -> None:
        gate = RiskGate(_cfg(daily_loss_halt_usd=10.0))
        await gate.record_realized_pnl(5.0, is_live=True)
        await gate.record_realized_pnl(-2.0, is_live=False)
        assert gate.live_pnl == pytest.approx(5.0)
        assert gate.paper_pnl == pytest.approx(-2.0)
        assert gate.daily_realized_pnl == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_halt_sums_both_buckets(self) -> None:
        gate = RiskGate(_cfg(daily_loss_halt_usd=10.0))
        await gate.record_realized_pnl(-6.0, is_live=True)
        await gate.record_realized_pnl(-5.0, is_live=False)
        # Combined -11 USD breaches the -10 halt.
        msg = gate.block_reason(_req())
        assert msg is not None and "daily loss halt" in msg


class TestPaperOverrideStructurallyIgnoredInLive:
    """Live's gate is built with allow_overrides=False so the paper study
    toggle cannot affect real funds even if the SQLite flag is set."""

    @pytest.mark.asyncio
    async def test_live_gate_ignores_bypass_flag(self) -> None:
        from btc_5m_fv.execution.gate import set_paper_bypass_loss_halt

        await set_paper_bypass_loss_halt(True)  # operator hits the toggle
        live = RiskGate(_cfg(daily_loss_halt_usd=10.0), allow_overrides=False)
        await live.record_realized_pnl(-15.0, is_live=True)
        await live.refresh_overrides()
        # Live still halts despite the bypass flag being persisted.
        msg = live.block_reason(_req())
        assert msg is not None and "daily loss halt" in msg
        assert live.bypass_loss_halt is False

    @pytest.mark.asyncio
    async def test_paper_gate_respects_bypass_flag(self) -> None:
        from btc_5m_fv.execution.gate import set_paper_bypass_loss_halt

        await set_paper_bypass_loss_halt(True)
        paper = RiskGate(_cfg(daily_loss_halt_usd=10.0), allow_overrides=True)
        await paper.record_realized_pnl(-15.0, is_live=False)
        await paper.refresh_overrides()
        # Paper passes — that's the whole point of the study toggle.
        assert paper.block_reason(_req()) is None
        assert paper.bypass_loss_halt is True
        # Other gates STILL run (per-trade cap, slippage, singleton).
        msg = paper.block_reason(_req(notional_usd=999.0))
        assert msg is not None and "per-trade cap" in msg


class TestRuntimeMaxTradeOverride:
    """Operator runtime per-trade cap (#50): applies in BOTH modes, no restart.

    Distinct from the paper-only loss-halt bypass — this is a tuning knob, so
    it is honoured whether or not the gate was built with allow_overrides.
    """

    def test_effective_defaults_to_cfg(self) -> None:
        gate = RiskGate(_cfg(max_trade_usd=5.0))
        assert gate.runtime_max_trade_usd is None
        assert gate.effective_max_trade_usd == 5.0

    @pytest.mark.asyncio
    async def test_override_lowers_cap(self) -> None:
        from btc_5m_fv.execution.gate import set_runtime_max_trade_usd

        gate = RiskGate(_cfg(max_trade_usd=5.0))
        await set_runtime_max_trade_usd(2.0)
        await gate.refresh_runtime_limits()
        assert gate.effective_max_trade_usd == 2.0
        msg = gate.block_reason(_req(notional_usd=3.0))
        assert msg is not None and "per-trade cap" in msg and "2.00" in msg

    @pytest.mark.asyncio
    async def test_override_raises_cap_for_both_modes(self) -> None:
        from btc_5m_fv.execution.gate import set_runtime_max_trade_usd

        # The override applies regardless of allow_overrides (paper or live).
        paper = RiskGate(_cfg(max_trade_usd=3.0), allow_overrides=True)
        live = RiskGate(_cfg(max_trade_usd=3.0), allow_overrides=False)
        # Without override, $5 trips the $3 env cap in both.
        assert paper.block_reason(_req(notional_usd=5.0)) is not None
        assert live.block_reason(_req(notional_usd=5.0)) is not None
        await set_runtime_max_trade_usd(8.0)
        await paper.refresh_runtime_limits()
        await live.refresh_runtime_limits()
        assert paper.effective_max_trade_usd == 8.0
        assert live.effective_max_trade_usd == 8.0
        assert paper.block_reason(_req(notional_usd=5.0)) is None
        assert live.block_reason(_req(notional_usd=5.0)) is None

    @pytest.mark.asyncio
    async def test_override_cleared_falls_back_to_env(self) -> None:
        from btc_5m_fv.execution.gate import set_runtime_max_trade_usd

        gate = RiskGate(_cfg(max_trade_usd=5.0))
        await set_runtime_max_trade_usd(2.0)
        await gate.refresh_runtime_limits()
        assert gate.effective_max_trade_usd == 2.0
        await set_runtime_max_trade_usd(None)  # clear
        await gate.refresh_runtime_limits()
        assert gate.runtime_max_trade_usd is None
        assert gate.effective_max_trade_usd == 5.0

    @pytest.mark.asyncio
    async def test_set_get_round_trip(self) -> None:
        from btc_5m_fv.execution.gate import (
            get_runtime_max_trade_usd,
            set_runtime_max_trade_usd,
        )

        assert await get_runtime_max_trade_usd() is None
        await set_runtime_max_trade_usd(4.5)
        assert await get_runtime_max_trade_usd() == 4.5
        await set_runtime_max_trade_usd(0)  # ≤0 clears
        assert await get_runtime_max_trade_usd() is None

    @pytest.mark.asyncio
    async def test_invalid_persisted_value_ignored(self) -> None:
        import db as _db

        gate = RiskGate(_cfg(max_trade_usd=5.0))
        await _db.set_config("btc_runtime.max_trade_usd", "not-a-number")
        await gate.refresh_runtime_limits()
        assert gate.runtime_max_trade_usd is None
        assert gate.effective_max_trade_usd == 5.0
