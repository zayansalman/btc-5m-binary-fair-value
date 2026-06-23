"""Unit tests for the FastAPI EMS dashboard (#37 redesign).

Covers app creation, the EMS page structure, static assets, and the
/api endpoints. Visual contract is the trading-terminal theme.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi.testclient import TestClient

from btc_5m_fv.ops.dashboard.app import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


class TestAppCreation:
    def test_app_has_title(self):
        assert app.title == "BTC 5m Binary Fair Value"

    def test_app_has_routes(self):
        paths = {r.path for r in app.routes}
        assert "/" in paths
        assert "/api/data" in paths
        assert "/api/start" in paths
        assert "/api/stop" in paths
        assert "/api/stream" in paths


class TestDashboardPage:
    def test_get_root_returns_html(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_html_links_assets(self, client: TestClient):
        text = client.get("/").text
        assert "/static/style.css" in text
        assert "/static/dashboard.js" in text

    def test_has_ems_panels(self, client: TestClient):
        text = client.get("/").text
        for panel in ("ribbon", "ems-grid", "STRATEGY", "LIVE MARKET",
                      "PERFORMANCE / ALPHA", "TCA", "TRADE BLOTTER"):
            assert panel in text, f"missing EMS panel: {panel}"

    def test_has_controls(self, client: TestClient):
        text = client.get("/").text
        assert "handleStart()" in text
        assert "handleStop()" in text

    def test_has_runtime_controls_card(self, client: TestClient):
        text = client.get("/").text
        assert "CONTROLS" in text
        assert "ctl-shares" in text
        assert "setTradeShares()" in text
        assert "Polymarket minimum order" in text

    def test_has_secondary_panels(self, client: TestClient):
        text = client.get("/").text
        assert "ACTIVITY LOG" in text
        assert "BACKTEST" in text


class TestStaticFiles:
    def test_css_served(self, client: TestClient):
        r = client.get("/static/style.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["content-type"]

    def test_css_has_theme_variables(self, client: TestClient):
        css = client.get("/static/style.css").text
        for var in ("--accent:", "--green:", "--red:", "--bg:", "--mono:"):
            assert var in css, f"missing var {var}"

    def test_css_has_ems_components(self, client: TestClient):
        css = client.get("/static/style.css").text
        for sel in (".ribbon", ".card", ".ems-grid", ".blotter", ".stat", ".pill", ".tag"):
            assert sel in css, f"missing {sel}"

    def test_js_served(self, client: TestClient):
        r = client.get("/static/dashboard.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]

    def test_js_has_handlers_and_sse(self, client: TestClient):
        js = client.get("/static/dashboard.js").text
        for fn in ("handleStart", "handleStop", "handleRefresh",
                   "updateDashboard", "EventSource"):
            assert fn in js, f"missing {fn}"

    def test_js_swaps_ems_content(self, client: TestClient):
        assert "ems-content" in client.get("/static/dashboard.js").text

    def test_js_has_runtime_control_handler(self, client: TestClient):
        js = client.get("/static/dashboard.js").text
        assert "setTradeShares" in js
        assert "updateShareValue" in js

    def test_css_has_control_input(self, client: TestClient):
        assert ".ctl-input" in client.get("/static/style.css").text


class TestApiData:
    def test_api_data_returns_json(self, client: TestClient):
        r = client.get("/api/data")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/json"

    def test_api_data_has_expected_keys(self, client: TestClient):
        data = client.get("/api/data").json()
        assert "ems" in data
        assert "activity" in data
        assert "backtest" in data

    def test_api_data_ems_is_rendered_html(self, client: TestClient):
        ems = client.get("/api/data").json()["ems"]
        assert isinstance(ems, str) and len(ems) > 200
        assert "ribbon" in ems


class TestApiStart:
    def test_start_returns_json(self, client: TestClient):
        r = client.post("/api/start")
        assert r.status_code == 200
        assert "status" in r.json()


class TestApiStop:
    def test_stop_returns_json(self, client: TestClient):
        r = client.post("/api/stop")
        assert r.status_code == 200
        assert "status" in r.json()


class TestApiStream:
    def test_stream_route_exists(self):
        assert any(r.path == "/api/stream" for r in app.routes)


class TestPerformanceReconLine:
    """The 'Reconciled vs Polymarket' footer surfaces real account truth (#102)."""

    _PERF = dict(
        n=5, wins=3, losses=2, pnl=1.2, roi=0.05, win_rate=0.6,
        expectancy=0.24, profit_factor=1.5, max_dd=-0.5, equity=[0.0, 1.0, 2.0],
    )
    _RECON = {
        "real_btc_pnl_lifetime": "-14.5139",
        "real_account_pnl_lifetime": "-34.096",
        "open_positions_value": "6.2578",
        "asof": "2026-06-22T05:03:56Z",
        "source": "polymarket-data-api",
    }

    def test_recon_line_renders_real_numbers(self) -> None:
        from btc_5m_fv.ops.dashboard.panels import performance

        html = performance.render(
            style="settle", perf=self._PERF, perf_live={"n": 0}, perf_paper={"n": 0},
            recon=self._RECON,
        )
        assert "Reconciled vs Polymarket" in html
        assert "$-14.51" in html  # BTC bot real
        assert "$-34.10" in html  # account real
        assert "2026-06-22T05:03:56" in html

    def test_recon_line_absent_without_data(self) -> None:
        from btc_5m_fv.ops.dashboard.panels import performance

        html = performance.render(
            style="settle", perf=self._PERF, perf_live={"n": 0}, perf_paper={"n": 0},
            recon=None,
        )
        assert "Reconciled vs Polymarket" not in html
class TestGuardrailsTrailingHalt:
    """Issue #112: the LOSS HALT panel reflects a trailing high-water-mark stop —
    halt floor = peak - limit, headroom = pnl - floor. A positive PnL that has
    drawn down past the floor shows HALTED; a never-profitable leg behaves like
    the old fixed -limit floor."""

    def _render(self, **over):
        from btc_5m_fv.ops.dashboard.panels import guardrails

        kw = dict(
            day_spend=0.0, bankroll_cap=None, submitted_count=0, submitted_notional=0.0,
            day_pnl=0.0, live_pnl=0.0, paper_pnl=0.0, live_peak=0.0, paper_peak=0.0,
            loss_halt_usd=10.0, state="running", bot_detail="", session_start=None,
            paused=False, pause_reason="", blocked=[], mode="live", bypass_loss_halt=False,
        )
        kw.update(over)
        return guardrails.render(**kw)

    def test_headroom_is_full_at_peak(self) -> None:
        # pnl == peak == 0 → full headroom, not halted.
        html = self._render(live_pnl=0.0, live_peak=0.0)
        assert "$10.00" in html  # headroom
        assert ">OK<" in html

    def test_headroom_shrinks_after_drawdown_from_peak(self) -> None:
        # Banked +30, gave back 5 → headroom 5, floor +20, still OK.
        html = self._render(live_pnl=25.0, live_peak=30.0)
        assert "$5.00" in html  # headroom = 25 - (30-10)
        assert ">OK<" in html

    def test_positive_pnl_can_be_halted_by_trailing_stop(self) -> None:
        # +18 after a +30 peak → floor +20, 18 <= 20 → HALTED despite positive PnL.
        html = self._render(live_pnl=18.0, live_peak=30.0)
        assert ">HALTED<" in html

    def test_never_profitable_matches_fixed_floor(self) -> None:
        # peak 0 → floor -10; -10 → HALTED, exactly like the pre-#112 behaviour.
        html = self._render(live_pnl=-10.0, live_peak=0.0)
        assert ">HALTED<" in html

    def test_panel_shows_peak_and_floor(self) -> None:
        html = self._render(live_pnl=25.0, live_peak=30.0)
        assert "Peak (live)" in html
        assert "Halt floor" in html

    def test_paper_mode_uses_paper_leg(self) -> None:
        # In paper mode the paper leg + paper peak drive the display.
        html = self._render(mode="paper", paper_pnl=4.0, paper_peak=12.0,
                            live_pnl=0.0, live_peak=0.0)
        # floor = 12 - 10 = 2; headroom = 4 - 2 = 2.
        assert "$2.00" in html


class TestModelSelector:
    """The dropdown lists the full logged roster (SELECTABLE_MODELS); an unknown
    active model still renders (orphan guard); the switch rejects unknown ids."""

    def test_selector_lists_all_selectable_models(self) -> None:
        from btc_5m_fv.ops.dashboard.panels import controls
        from btc_bot.shadow import runner

        html = controls.render(
            trade_shares_current=None, current_price=None, active_model="down_skeptic_v4"
        )
        for mid in runner.SELECTABLE_MODELS:
            assert f"value='{mid}'" in html
        # The full roster is selectable now (#111): the former controls and the
        # restored late_convergence_v3 all appear as options.
        assert "value='fair_value_v0'" in html
        assert "value='cushion_favorite_v2'" in html
        assert "value='late_convergence_v3'" in html
        # An unknown id is never rendered as an option.
        assert "value='no_such_model'" not in html

    def test_selector_includes_orphaned_active_model(self) -> None:
        """An unknown / non-selectable active model still renders (orphan guard)."""
        from btc_5m_fv.ops.dashboard.panels import controls

        html = controls.render(
            trade_shares_current=None, current_price=None, active_model="ghost_model"
        )
        assert "value='ghost_model'" in html

    def test_active_model_rejects_unknown(self, client: TestClient) -> None:
        """Posting an unknown / non-selectable model id is rejected (no write)."""
        r = client.post(
            "/api/runtime-config",
            json={"key": "active_model", "value": "no_such_model"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "error"
