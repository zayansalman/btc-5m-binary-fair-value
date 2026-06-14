"""Adaptive risk controller tests (#36)."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

import config as _config
import db as _db
from btc_bot.adaptive import (
    evaluate_and_maybe_pause,
    is_paused,
    rolling_performance,
    should_pause,
    clear_auto_pause,
)


@pytest_asyncio.fixture
async def test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "t.db")
    await _db.init_db()
    return _db


async def _add(db, *, pnl, notional=5.0, edge=0.06, entry=0.55, style="settle",
               quote="clob", state="closed"):
    async with db.connect() as conn:
        await conn.execute(
            "INSERT INTO btc_paper_positions(opened_at, window_slug, side, state,"
            " entry_price, notional_usd, shares, edge, realized_pnl_usd,"
            " quote_source, strategy_style)"
            " VALUES ('t','w','Up',?,?,?,1,?,?,?,?)",
            (state, entry, notional, edge, pnl, quote, style),
        )
        await conn.commit()


# --- pure decision ---------------------------------------------------------


def test_should_pause_warmup_never_pauses():
    paused, reason = should_pause({"n": 5, "roi": -0.9, "pnl": -9}, min_trades=10, min_roi=-0.15)
    assert paused is False and "warming up" in reason


def test_should_pause_trips_below_floor():
    paused, reason = should_pause({"n": 20, "roi": -0.30, "pnl": -30}, min_trades=10, min_roi=-0.15)
    assert paused is True and "below floor" in reason


def test_should_pause_ok_above_floor():
    paused, _ = should_pause({"n": 20, "roi": 0.10, "pnl": 10}, min_trades=10, min_roi=-0.15)
    assert paused is False


# --- rolling metrics -------------------------------------------------------


@pytest.mark.asyncio
async def test_rolling_performance_and_calibration(test_db):
    # 6 wins @ +4.5 (entry .55 -> payout 1.0 on 5 notional ~ +4.09), 4 losses @ -5
    for _ in range(6):
        await _add(test_db, pnl=4.09, edge=0.06, entry=0.55)   # model_prob .61, won
    for _ in range(4):
        await _add(test_db, pnl=-5.0, edge=0.06, entry=0.55)   # model_prob .61, lost
    perf = await rolling_performance(window=20, style="settle")
    assert perf["n"] == 10
    assert perf["win_rate"] == pytest.approx(0.6)
    assert perf["brier"] is not None and 0 < perf["brier"] < 1


@pytest.mark.asyncio
async def test_rolling_excludes_other_style_and_nonclob(test_db):
    await _add(test_db, pnl=4.0, style="scalp")
    await _add(test_db, pnl=4.0, quote="gamma")
    await _add(test_db, pnl=4.0, state="open")
    perf = await rolling_performance(window=20, style="settle")
    assert perf["n"] == 0


# --- sticky auto-pause integration ----------------------------------------


@pytest.mark.asyncio
async def test_evaluate_trips_and_is_sticky(test_db, monkeypatch):
    monkeypatch.setattr(_config, "BTC_AUTO_PAUSE_ENABLED", True)
    monkeypatch.setattr(_config, "BTC_AUTO_PAUSE_WINDOW", 20)
    monkeypatch.setattr(_config, "BTC_AUTO_PAUSE_MIN_TRADES", 10)
    monkeypatch.setattr(_config, "BTC_AUTO_PAUSE_MIN_ROI", -0.15)
    monkeypatch.setattr(_config, "BTC_EXIT_STYLE", "settle")
    for _ in range(12):
        await _add(test_db, pnl=-5.0)  # all losses -> ROI -100%
    paused, reason = await evaluate_and_maybe_pause()
    assert paused is True and "below floor" in reason
    # sticky: stays paused even after we add winners (no auto-resume)
    for _ in range(20):
        await _add(test_db, pnl=4.0)
    paused2, _ = await is_paused()
    assert paused2 is True
    # operator clears -> resumes
    await clear_auto_pause()
    assert (await is_paused())[0] is False


@pytest.mark.asyncio
async def test_evaluate_warmup_does_not_pause(test_db, monkeypatch):
    monkeypatch.setattr(_config, "BTC_AUTO_PAUSE_ENABLED", True)
    monkeypatch.setattr(_config, "BTC_AUTO_PAUSE_MIN_TRADES", 10)
    monkeypatch.setattr(_config, "BTC_EXIT_STYLE", "settle")
    for _ in range(5):
        await _add(test_db, pnl=-5.0)
    paused, _ = await evaluate_and_maybe_pause()
    assert paused is False


@pytest.mark.asyncio
async def test_disabled_never_pauses(test_db, monkeypatch):
    monkeypatch.setattr(_config, "BTC_AUTO_PAUSE_ENABLED", False)
    monkeypatch.setattr(_config, "BTC_EXIT_STYLE", "settle")
    for _ in range(20):
        await _add(test_db, pnl=-5.0)
    paused, _ = await evaluate_and_maybe_pause()
    assert paused is False
