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
