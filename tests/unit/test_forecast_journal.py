"""Forecast-journal pilot tool (#162): scoring math + journal flows.

The tool owns a dedicated SQLite file (never the bot ledger), so every test
runs against ``tmp_path`` — no interaction with the live race is possible.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tools.forecast_journal import (
    brier,
    cmd_add,
    cmd_report,
    cmd_resolve,
    connect,
    market_implied,
    mean_ci,
    simulated_pnl_per_share,
    taker_fee_per_share,
)


# ---------------------------------------------------------------------------
# Scoring math
# ---------------------------------------------------------------------------


class TestScoring:
    def test_brier_truth_table(self) -> None:
        assert brier(1.0, True) == 0.0
        assert brier(0.0, True) == 1.0
        assert brier(0.5, True) == pytest.approx(0.25)
        assert brier(0.7, False) == pytest.approx(0.49)

    def test_market_implied_is_mid(self) -> None:
        assert market_implied(0.40, 0.44) == pytest.approx(0.42)

    def test_no_trade_inside_divergence_band(self) -> None:
        # forecast 0.45 vs mid 0.42 → |edge| 0.03 < 0.05 → no position.
        assert simulated_pnl_per_share(0.45, 0.40, 0.44, True, 0.0) is None

    def test_yes_side_win_and_loss_fee_free(self) -> None:
        # forecast 0.60 vs mid 0.42 → buy YES at ask 0.44.
        win = simulated_pnl_per_share(0.60, 0.40, 0.44, True, 0.0)
        loss = simulated_pnl_per_share(0.60, 0.40, 0.44, False, 0.0)
        assert win == pytest.approx(0.56)   # 1 − 0.44, no fee
        assert loss == pytest.approx(-0.44)

    def test_no_side_uses_complement_of_yes_bid(self) -> None:
        # forecast 0.20 vs mid 0.42 → buy NO at 1 − yes_bid = 0.60.
        no_wins = simulated_pnl_per_share(0.20, 0.40, 0.44, False, 0.0)
        no_loses = simulated_pnl_per_share(0.20, 0.40, 0.44, True, 0.0)
        assert no_wins == pytest.approx(0.40)   # 1 − 0.60
        assert no_loses == pytest.approx(-0.60)

    def test_fee_reduces_pnl_on_both_outcomes(self) -> None:
        fee = taker_fee_per_share(0.44, 0.04)
        win = simulated_pnl_per_share(0.60, 0.40, 0.44, True, 0.04)
        loss = simulated_pnl_per_share(0.60, 0.40, 0.44, False, 0.04)
        assert win == pytest.approx(0.56 - fee)
        assert loss == pytest.approx(-0.44 - fee)
        assert fee == pytest.approx(0.04 * 0.44 * 0.56)

    def test_mean_ci_basics(self) -> None:
        mu, lo, hi = mean_ci([1.0, -1.0, 1.0, -1.0])
        assert mu == pytest.approx(0.0)
        assert lo < 0 < hi
        assert mean_ci([]) == (0.0, 0.0, 0.0)
        assert mean_ci([2.0]) == (2.0, 2.0, 2.0)


# ---------------------------------------------------------------------------
# Journal flows (temp DB)
# ---------------------------------------------------------------------------


def _ns(**kw) -> argparse.Namespace:
    base = dict(
        market="m1", question=None, category="geopolitics", forecast=0.6,
        yes_bid=0.40, yes_ask=0.44, resolves_by=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def db(tmp_path: Path):
    conn = connect(tmp_path / "journal.db")
    yield conn
    conn.close()


class TestJournalFlows:
    def test_add_and_resolve_roundtrip(self, db) -> None:
        cmd_add(db, _ns())
        row = db.execute("SELECT * FROM forecasts").fetchone()
        assert row["fee_rate"] == 0.0          # geopolitics is fee-free
        assert row["forecast_yes"] == pytest.approx(0.6)
        assert row["outcome"] is None
        cmd_resolve(db, argparse.Namespace(id=row["id"], outcome="yes"))
        row = db.execute("SELECT * FROM forecasts").fetchone()
        assert row["outcome"] == "yes" and row["resolved_at"] is not None

    def test_crypto_category_refused(self, db) -> None:
        """The pre-registration excludes crypto — the tool enforces it."""
        with pytest.raises(SystemExit):
            cmd_add(db, _ns(category="crypto"))
        assert db.execute("SELECT COUNT(*) c FROM forecasts").fetchone()["c"] == 0

    def test_invalid_forecast_refused(self, db) -> None:
        for bad in (0.0, 1.0, -0.2, 1.7):
            with pytest.raises(SystemExit):
                cmd_add(db, _ns(forecast=bad))

    def test_crossed_quote_refused(self, db) -> None:
        with pytest.raises(SystemExit):
            cmd_add(db, _ns(yes_bid=0.50, yes_ask=0.44))

    def test_double_resolve_refused(self, db) -> None:
        cmd_add(db, _ns())
        cmd_resolve(db, argparse.Namespace(id=1, outcome="no"))
        with pytest.raises(SystemExit):
            cmd_resolve(db, argparse.Namespace(id=1, outcome="yes"))

    def test_unknown_id_refused(self, db) -> None:
        with pytest.raises(SystemExit):
            cmd_resolve(db, argparse.Namespace(id=99, outcome="yes"))


class TestReport:
    def test_underpowered_verdict_below_target(self, db, capsys) -> None:
        cmd_add(db, _ns())
        cmd_resolve(db, argparse.Namespace(id=1, outcome="yes"))
        cmd_report(db, argparse.Namespace())
        out = capsys.readouterr().out
        assert "resolved 1 / target ≥30" in out
        assert "underpowered" in out

    def test_skill_and_pnl_computed(self, db, capsys) -> None:
        """A forecaster who calls a 0.42-mid market at 0.90 and is right:
        beats the market's Brier and books +0.56/share fee-free."""
        cmd_add(db, _ns(forecast=0.90))
        cmd_resolve(db, argparse.Namespace(id=1, outcome="yes"))
        cmd_report(db, argparse.Namespace())
        out = capsys.readouterr().out
        assert "our Brier: 0.0100" in out          # (0.9 − 1)²
        assert "market Brier: 0.3364" in out       # (0.42 − 1)²
        assert "we beat the market" in out
        assert "1 trades, mean +0.5600" in out

    def test_quoteless_rows_score_brier_only(self, db, capsys) -> None:
        cmd_add(db, _ns(yes_bid=None, yes_ask=None))
        cmd_resolve(db, argparse.Namespace(id=1, outcome="no"))
        cmd_report(db, argparse.Namespace())
        out = capsys.readouterr().out
        assert "our Brier" in out
        assert "no simulated trades" in out

    def test_bar_verdict_at_target_n(self, db, capsys) -> None:
        """30 resolved, all called correctly at high divergence → PASSES."""
        for i in range(30):
            cmd_add(db, _ns(market=f"m{i}", forecast=0.90))
            cmd_resolve(db, argparse.Namespace(id=i + 1, outcome="yes"))
        cmd_report(db, argparse.Namespace())
        out = capsys.readouterr().out
        assert "PASSES the pre-registered bar" in out

    def test_bar_fails_when_market_wins(self, db, capsys) -> None:
        """30 resolved, forecaster always wrong side → FAILS the bar."""
        for i in range(30):
            cmd_add(db, _ns(market=f"m{i}", forecast=0.90))
            cmd_resolve(db, argparse.Namespace(id=i + 1, outcome="no"))
        cmd_report(db, argparse.Namespace())
        out = capsys.readouterr().out
        assert "FAILS the pre-registered bar" in out
        assert "do not fund" in out