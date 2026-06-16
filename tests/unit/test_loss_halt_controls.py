"""Operator loss-halt controls (#76).

Covers the four moving parts of issue #76:
  * the loop's auto-stop decision (`_loss_halt_stop_detail`),
  * the `/api/loss_halt/bypass` and `/api/loss_halt/reset` endpoints,
  * the one-shot stale-bypass migration, and
  * the LOSS HALT panel (STATUS pill → bypass button, Reset button).

Each test runs against its own throwaway SQLite so the real journal is untouched.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import db as _db
from btc_5m_fv.execution.gate import GateConfig, RiskGate, get_loss_halt_bypass
from btc_5m_fv.ops.dashboard.panels import guardrails
from btc_bot.paper import _loss_halt_stop_detail


def _cfg(*, daily_loss_halt_usd: float = 10.0) -> GateConfig:
    return GateConfig(
        max_trade_usd=5.0,
        daily_loss_halt_usd=daily_loss_halt_usd,
        bankroll_cap_usd=None,
        max_entry_slippage=0.02,
        kill_switch_path=Path("/does/not/exist"),
    )


@pytest_asyncio.fixture
async def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Throwaway SQLite for the async (gate/migration) tests."""
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "test_lh.db")
    await _db.init_db()
    yield


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """TestClient on an isolated DB. The lifespan runs init_db + the #76
    migration, so the bypass starts OFF (halt ON)."""
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "test_lh_ep.db")
    from btc_5m_fv.ops.dashboard.app import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Loop auto-stop decision
# ---------------------------------------------------------------------------


class TestLossHaltStopDetail:
    @pytest.mark.asyncio
    async def test_stop_detail_on_live_breach(self, isolated_db) -> None:
        g = RiskGate(_cfg(), is_live=True)
        await g.record_realized_pnl(-12.4, is_live=True)
        detail = _loss_halt_stop_detail(g, "live")
        assert detail is not None
        assert "loss halt" in detail.lower()
        assert "live" in detail.lower()
        assert "Reset" in detail

    @pytest.mark.asyncio
    async def test_none_within_limit(self, isolated_db) -> None:
        g = RiskGate(_cfg(), is_live=True)
        await g.record_realized_pnl(-5.0, is_live=True)
        assert _loss_halt_stop_detail(g, "live") is None

    @pytest.mark.asyncio
    async def test_none_when_bypassed(self, isolated_db) -> None:
        from btc_5m_fv.execution.gate import set_loss_halt_bypass

        await set_loss_halt_bypass(True)
        g = RiskGate(_cfg(), is_live=True)
        await g.record_realized_pnl(-50.0, is_live=True)
        await g.refresh_overrides()
        assert _loss_halt_stop_detail(g, "live") is None

    @pytest.mark.asyncio
    async def test_live_ignores_paper_leg(self, isolated_db) -> None:
        g = RiskGate(_cfg(), is_live=True)
        await g.record_realized_pnl(-50.0, is_live=False)  # paper only
        assert _loss_halt_stop_detail(g, "live") is None

    def test_none_when_gate_missing(self) -> None:
        assert _loss_halt_stop_detail(None, "live") is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class TestLossHaltEndpoints:
    def test_bypass_enable_then_disable(self, client: TestClient) -> None:
        r = client.post("/api/loss_halt/bypass", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["bypass_loss_halt"] is True
        assert asyncio.run(get_loss_halt_bypass()) is True

        r = client.post("/api/loss_halt/bypass", json={"enabled": False})
        assert r.json()["bypass_loss_halt"] is False
        assert asyncio.run(get_loss_halt_bypass()) is False

    def test_reset_rejected_when_running(self, client: TestClient) -> None:
        asyncio.run(_db.set_config("btc_bot.state", "running"))
        asyncio.run(_db.set_config("btc_risk.live_realized_pnl", "-8.0"))
        r = client.post("/api/loss_halt/reset")
        assert r.json()["status"] == "error"
        # Tally untouched.
        assert asyncio.run(_db.get_config("btc_risk.live_realized_pnl")) == "-8.0"

    def test_reset_zeroes_when_stopped(self, client: TestClient) -> None:
        asyncio.run(_db.set_config("btc_bot.state", "stopped"))
        asyncio.run(_db.set_config("btc_risk.live_realized_pnl", "-8.0"))
        asyncio.run(_db.set_config("btc_risk.paper_realized_pnl", "-3.0"))
        r = client.post("/api/loss_halt/reset")
        assert r.json()["status"] == "ok"
        assert float(asyncio.run(_db.get_config("btc_risk.live_realized_pnl"))) == 0.0
        assert float(asyncio.run(_db.get_config("btc_risk.paper_realized_pnl"))) == 0.0


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class TestBypassMigration:
    @pytest.mark.asyncio
    async def test_clears_stale_flag_once(self, isolated_db) -> None:
        from btc_5m_fv.execution.gate import (
            migrate_clear_stale_bypass_v76,
            set_loss_halt_bypass,
        )

        await set_loss_halt_bypass(True)  # stale paper-era flag
        await migrate_clear_stale_bypass_v76()
        assert await get_loss_halt_bypass() is False  # cleared → halt ON

    @pytest.mark.asyncio
    async def test_does_not_wipe_later_deliberate_bypass(self, isolated_db) -> None:
        from btc_5m_fv.execution.gate import (
            migrate_clear_stale_bypass_v76,
            set_loss_halt_bypass,
        )

        await migrate_clear_stale_bypass_v76()  # first run sets the sentinel
        await set_loss_halt_bypass(True)  # operator deliberately enables later
        await migrate_clear_stale_bypass_v76()  # must be a no-op now
        assert await get_loss_halt_bypass() is True


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


def _render(**over) -> str:
    args = dict(
        day_spend=0.0,
        bankroll_cap=30.0,
        submitted_count=0,
        submitted_notional=0.0,
        day_pnl=0.0,
        live_pnl=0.0,
        paper_pnl=0.0,
        loss_halt_usd=10.0,
        state="stopped",
        bot_detail="",
        session_start=None,
        paused=False,
        pause_reason="",
        blocked=[],
        mode="paper",
        bypass_loss_halt=False,
    )
    args.update(over)
    return guardrails.render(**args)


class TestGuardrailsPanel:
    def test_status_is_a_bypass_button(self) -> None:
        html = _render(mode="live", bypass_loss_halt=True)
        assert "/api/loss_halt/bypass" in html
        assert ">BYPASS<" in html
        assert "enabled:false" in html  # clicking BYPASS re-enables the halt

    def test_status_ok_click_enables_bypass(self) -> None:
        html = _render(mode="live", live_pnl=-5.0)
        assert ">OK<" in html
        assert "enabled:true" in html  # clicking OK disables the halt

    def test_status_halted_when_live_leg_breached(self) -> None:
        html = _render(mode="live", live_pnl=-12.0)
        assert ">HALTED<" in html

    def test_paper_losses_do_not_halt_live_panel(self) -> None:
        # Live leg fine (-5), paper leg deep (-30): live mode shows OK, not HALTED.
        html = _render(mode="live", live_pnl=-5.0, paper_pnl=-30.0)
        assert ">OK<" in html
        assert ">HALTED<" not in html

    def test_headroom_uses_live_leg_in_live(self) -> None:
        html = _render(mode="live", live_pnl=-4.0, paper_pnl=-30.0)
        assert "Headroom (live)" in html
        assert "$6.00" in html  # 10 - 4, paper -30 ignored

    def test_reset_button_present_and_enabled_when_stopped(self) -> None:
        html = _render(mode="live", state="stopped")
        assert "/api/loss_halt/reset" in html
        assert "Reset halt" in html

    def test_reset_disabled_when_running(self) -> None:
        html = _render(mode="live", state="running")
        assert "Stop the bot to reset" in html

    def test_no_cannot_disable_text(self) -> None:
        html = _render(mode="live")
        assert "cannot disable" not in html
