"""Shadow forward-tester runner: build_view mapping + record/settle end-to-end.

Exercises the integration seam the parallel module tests don't cover: a real
``PaperSnapshot`` flowing through ``record_shadow`` into the ledger, and the
net-of-fee settlement of the logged candidates.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

import db as _db
from btc_bot import strategy
from btc_bot.paper import PaperSnapshot
from btc_bot.shadow import runner
from btc_bot.shadow.fees import net_pnl_per_share
from btc_bot.shadow.ledger import settle_open_shadow


@pytest.fixture
def params() -> strategy.StrategyParams:
    return strategy.StrategyParams(
        min_trade_usd=1.0,
        max_trade_usd=5.0,
        entry_edge_min=0.045,
        entry_edge_max=0.07,
        min_confidence=0.50,
        entry_min_remaining_seconds=60,
        min_entry_price=0.50,
        max_entry_price=0.95,
    )


@pytest_asyncio.fixture
async def test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "t.db")
    await _db.init_db()
    return _db


def _snapshot(**overrides: object) -> PaperSnapshot:
    base = dict(
        created_at="2026-06-18T00:00:00+00:00",
        window_slug="btc-updown-5m-1700000000",
        market_question="Bitcoin Up or Down?",
        remaining_seconds=120,
        spot_price=64020.0,
        reference_price=63990.0,
        sigma_per_second=2.5e-5,
        market_up_price=0.56,
        market_down_price=0.46,
        fair_up_prob=0.62,
        edge=0.06,
        signal_side="Up",
        confidence=0.67,
        notional_usd=0.0,
        reason="",
        feed_source="binance",
        up_best_bid=0.55,
        up_best_ask=0.56,
        down_best_bid=0.44,
        down_best_ask=0.46,
        quote_source="clob",
    )
    base.update(overrides)
    return PaperSnapshot(**base)  # type: ignore[arg-type]


async def _models_for(db, window_slug: str) -> dict[str, dict]:
    async with db.connect() as conn:
        async with conn.execute(
            "SELECT * FROM btc_model_shadow_positions WHERE window_slug = ?",
            (window_slug,),
        ) as cur:
            return {r["model_id"]: dict(r) for r in await cur.fetchall()}


def test_build_view_maps_fields_and_mid() -> None:
    view = runner.build_view(_snapshot())
    assert view.up_ask == 0.56 and view.down_ask == 0.46
    assert view.spot == 64020.0 and view.reference == 63990.0
    assert view.fair_up == 0.62
    # market_up_price is the MID of the Up book, not the ask.
    assert view.market_up_price == pytest.approx((0.55 + 0.56) / 2)


@pytest.mark.asyncio
async def test_favorite_window_logs_v0_and_cushion(test_db, params) -> None:
    await runner.record_shadow(_snapshot(), params)
    rows = await _models_for(test_db, "btc-updown-5m-1700000000")
    # A clean cushioned favourite: v0 fires AND the cushion gate passes.
    assert "fair_value_v0" in rows
    assert "cushion_favorite_v2" in rows
    # v0 fires Up here and the regime is neutral -> the regime-aware skeptic
    # logs too (it reduces to v4, which never tolls an Up pick).
    assert "down_skeptic_drift_v6" in rows
    assert rows["cushion_favorite_v2"]["side"] == "Up"
    assert rows["cushion_favorite_v2"]["entry_price"] == pytest.approx(0.56)
    assert rows["cushion_favorite_v2"]["shares"] == runner.SHADOW_SHARES


@pytest.mark.asyncio
async def test_recording_is_idempotent_per_window(test_db, params) -> None:
    await runner.record_shadow(_snapshot(), params)
    await runner.record_shadow(_snapshot(market_up_price=0.58, up_best_ask=0.58), params)
    rows = await _models_for(test_db, "btc-updown-5m-1700000000")
    # The first signal per (window, model) wins; the second tick is dropped.
    assert rows["cushion_favorite_v2"]["entry_price"] == pytest.approx(0.56)


@pytest.mark.asyncio
async def test_settle_books_net_of_fee_pnl(test_db, params) -> None:
    await runner.record_shadow(_snapshot(), params)
    settled = await settle_open_shadow(
        window_slug="btc-updown-5m-1700000000",
        outcome_side="Up",
        settlement_price=1.0,
        resolved_at="2026-06-18T00:05:00+00:00",
    )
    assert settled >= 2  # v0 + cushion both resolved
    rows = await _models_for(test_db, "btc-updown-5m-1700000000")
    row = rows["cushion_favorite_v2"]
    assert row["state"] == "settled" and row["outcome"] == "Up"
    expected = runner.SHADOW_SHARES * net_pnl_per_share(0.56, won=True)
    assert row["realized_pnl_usd"] == pytest.approx(expected)
    assert row["realized_pnl_usd"] > 0  # cushioned favourite that won, net of fee


def test_model_registry_constants() -> None:
    assert runner.DEFAULT_MODEL == "fair_value_v0"
    # Logged set: full roster incl. the restored late_convergence_v3 (#111).
    assert set(runner.MODEL_IDS) == {
        "fair_value_v0",
        "cushion_favorite_v2",
        "late_convergence_v3",
        "down_skeptic_v4",
        "cushion_drift_v5",
        "down_skeptic_drift_v6",
    }
    # Operator-selectable set is the full roster, in vN experiment order (#111).
    assert runner.SELECTABLE_MODELS == [
        "fair_value_v0",
        "cushion_favorite_v2",
        "late_convergence_v3",
        "down_skeptic_v4",
        "cushion_drift_v5",
        "down_skeptic_drift_v6",
    ]
    # v3/v4/v6 are live-dispatchable; v0 stays out (native path).
    assert "down_skeptic_drift_v6" in runner.CANDIDATE_SIGNALS
    assert "late_convergence_v3" in runner.CANDIDATE_SIGNALS
    assert "fair_value_v0" not in runner.CANDIDATE_SIGNALS
    # every logged id has a label + description for the dashboard
    for mid in runner.MODEL_IDS:
        assert mid in runner.MODEL_LABELS and mid in runner.MODEL_DESCRIPTIONS


def test_candidate_signal_dispatch(params) -> None:
    """The live-dispatch helper routes to the candidate, and v0/unknown -> None."""
    view = runner.build_view(_snapshot())  # cushioned Up favourite (see _snapshot)
    # v0 and unknown ids return None — the caller uses the native v0 path.
    assert runner.candidate_signal("fair_value_v0", view, params) is None
    assert runner.candidate_signal("not_a_model", view, params) is None
    # a real candidate dispatches to its function.
    sig = runner.candidate_signal("cushion_favorite_v2", view, params)
    assert sig is not None and sig.side == "Up"
