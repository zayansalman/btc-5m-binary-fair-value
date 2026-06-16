"""Operator runtime config endpoint + persistence (#50).

The dashboard POSTs to /api/runtime-config to set the unified max trade size;
the value is persisted to the config table and read by the loop every tick.
Each test runs against its own throwaway SQLite so the real journal is untouched.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db as _db
from btc_5m_fv.execution.gate import get_runtime_max_trade_usd


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "rt_config.db")
    from btc_5m_fv.ops.dashboard.app import app

    with TestClient(app) as c:
        yield c


class TestRuntimeConfigEndpoint:
    def test_set_max_trade_size_ok_and_persists(self, client: TestClient) -> None:
        r = client.post(
            "/api/runtime-config", json={"key": "max_trade_usd", "value": 3.5}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["value"] == 3.5
        # Persisted so the loop's next tick enforces it.
        assert asyncio.run(get_runtime_max_trade_usd()) == 3.5

    def test_rounds_to_cents(self, client: TestClient) -> None:
        r = client.post(
            "/api/runtime-config", json={"key": "max_trade_usd", "value": 4.567}
        )
        assert r.json()["value"] == 4.57

    def test_rejects_non_numeric(self, client: TestClient) -> None:
        r = client.post(
            "/api/runtime-config", json={"key": "max_trade_usd", "value": "abc"}
        )
        assert r.json()["status"] == "error"
        assert "number" in r.json()["detail"]

    def test_rejects_zero_and_negative(self, client: TestClient) -> None:
        for bad in (0, -1.0):
            r = client.post(
                "/api/runtime-config", json={"key": "max_trade_usd", "value": bad}
            )
            assert r.json()["status"] == "error"

    def test_rejects_out_of_range(self, client: TestClient) -> None:
        r = client.post(
            "/api/runtime-config", json={"key": "max_trade_usd", "value": 99999}
        )
        assert r.json()["status"] == "error"

    def test_unknown_key_rejected(self, client: TestClient) -> None:
        r = client.post(
            "/api/runtime-config", json={"key": "nonsense", "value": 1}
        )
        assert r.json()["status"] == "error"
        assert "unknown runtime key" in r.json()["detail"]
