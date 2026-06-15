"""End-to-end tests for the EMS dashboard (#37 redesign).

Verifies the full page-load flow, EMS panels, controls, API round-trips,
static assets, and the trading-terminal visual contract.
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


class TestFullPageLoad:
    def test_page_loads_200(self, client: TestClient):
        assert client.get("/").status_code == 200

    def test_page_has_doctype_structure(self, client: TestClient):
        text = client.get("/").text
        for tag in ("<!DOCTYPE html>", "<html", "</html>", "<head>", "<body>"):
            assert tag in text

    def test_page_has_meta_viewport(self, client: TestClient):
        assert "width=device-width" in client.get("/").text

    def test_ems_panels_present(self, client: TestClient):
        text = client.get("/").text
        for panel in ("STRATEGY", "LIVE MARKET", "PERFORMANCE / ALPHA",
                      "TCA", "TRADE BLOTTER", "ems-grid", "ribbon"):
            assert panel in text

    def test_ems_content_container(self, client: TestClient):
        text = client.get("/").text
        assert "ems-content" in text
        assert "activity-content" in text
        assert "backtest-content" in text

    def test_strategy_panel_shows_params(self, client: TestClient):
        text = client.get("/").text
        # Strategy panel surfaces the model + bands.
        assert "Fair-Value" in text
        assert "Edge band" in text
        assert "Settlement" in text


class TestButtonInteractivity:
    def test_start_button(self, client: TestClient):
        text = client.get("/").text
        assert "handleStart()" in text
        assert "Start" in text

    def test_stop_button(self, client: TestClient):
        text = client.get("/").text
        assert "handleStop()" in text

    def test_refresh_button(self, client: TestClient):
        assert "handleRefresh()" in client.get("/").text


class TestApiRoundTrip:
    def test_start_returns_status(self, client: TestClient):
        r = client.post("/api/start")
        assert r.status_code == 200 and "status" in r.json()

    def test_stop_returns_status(self, client: TestClient):
        r = client.post("/api/stop")
        assert r.status_code == 200 and "status" in r.json()

    def test_data_after_start_stop_cycle(self, client: TestClient):
        client.post("/api/start")
        client.post("/api/stop")
        data = client.get("/api/data").json()
        assert "ems" in data
        assert "activity" in data
        assert "backtest" in data


class TestStaticAssets:
    def test_css_complete(self, client: TestClient):
        css = client.get("/static/style.css").text
        selectors = [
            ":root", "body", ".topbar", ".ribbon", ".ems-grid", ".card",
            ".card-h", ".stat", ".pill", ".gauge", ".book", ".spark",
            ".calib", ".blotter", ".tag", ".btn", ".sse-indicator", ".toast",
        ]
        for sel in selectors:
            assert sel in css, f"missing CSS selector: {sel}"

    def test_js_has_core_functions(self, client: TestClient):
        js = client.get("/static/dashboard.js").text
        for fn in ("showToast", "handleStart", "handleStop", "handleRefresh",
                   "updateDashboard", "connectSSE", "updateSseIndicator"):
            assert fn in js, f"missing JS function: {fn}"


class TestVisualContract:
    """Trading-terminal dark theme."""

    def test_dark_palette(self, client: TestClient):
        css = client.get("/static/style.css").text
        assert "#080b12" in css       # --bg near-black
        assert "#36e0c8" in css       # --accent cyan

    def test_pnl_color_classes(self, client: TestClient):
        css = client.get("/static/style.css").text
        assert ".up" in css and ".down" in css
        assert "--green:" in css and "--red:" in css

    def test_monospace_numbers(self, client: TestClient):
        assert "--mono:" in client.get("/static/style.css").text

    def test_pill_and_tag_variants(self, client: TestClient):
        css = client.get("/static/style.css").text
        assert ".pill.live" in css
        assert ".tag.up" in css and ".tag.down" in css
