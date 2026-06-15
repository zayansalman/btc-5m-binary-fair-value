"""Fit the side-relative probability calibrator from the closed-trade journal.

Run::

    python -m btc_bot.calibration_fit          # fit on all closed clob trades
    python -m btc_bot.calibration_fit --min 20 # require at least 20 samples
    python -m btc_bot.calibration_fit --style settle  # restrict to one style
    python -m btc_bot.calibration_fit --dry-run       # print, do not persist

Reads only the journal — no schema change. For each closed clob position:

* ``p_model = edge + entry_price`` reconstructs the model's pre-trade P(chosen
  side wins).
* ``side_won = 1 if realized_pnl_usd > 0 else 0`` is the realised outcome.

Persists to ``$DATA_DIR/calibration.json`` (atomic write). Identity fallback in
``calibration.load()`` means the live bot is a no-op until this file exists.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from btc_bot.calibration import IsotonicCalibrator, save
from db import connect


async def _fetch_pairs(style: str | None) -> list[tuple[float, float]]:
    sql = (
        "SELECT edge, entry_price, realized_pnl_usd "
        "FROM btc_paper_positions "
        "WHERE state='closed' AND quote_source='clob' "
        "AND edge IS NOT NULL AND entry_price IS NOT NULL "
        "AND realized_pnl_usd IS NOT NULL"
    )
    params: list = []
    if style:
        sql += " AND strategy_style = ?"
        params.append(style)
    async with connect() as db:
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    pairs: list[tuple[float, float]] = []
    for r in rows:
        p = max(0.0, min(1.0, float(r["edge"]) + float(r["entry_price"])))
        y = 1.0 if float(r["realized_pnl_usd"]) > 0 else 0.0
        pairs.append((p, y))
    return pairs


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=20, help="minimum samples (default 20)")
    ap.add_argument("--style", default=None, help="restrict to a strategy_style")
    ap.add_argument("--dry-run", action="store_true", help="print only, do not persist")
    args = ap.parse_args()

    pairs = await _fetch_pairs(args.style)
    n = len(pairs)
    if n < args.min:
        print(
            f"refuse: only {n} closed clob samples (need {args.min}); "
            "live bot stays on identity calibrator."
        )
        return 2

    probs = [p for p, _ in pairs]
    outcomes = [y for _, y in pairs]
    cal = IsotonicCalibrator.fit(
        probs,
        outcomes,
        fit_at=datetime.now(UTC).isoformat(timespec="seconds"),
        meta={"style": args.style or "all", "min_samples": args.min},
    )

    print(f"fit on n={cal.n_samples} closed clob trades")
    print(f"  brier raw      = {cal.brier_raw:.4f}")
    print(f"  brier calibrated = {cal.brier_cal:.4f}")
    if cal.brier_raw is not None and cal.brier_cal is not None:
        delta = cal.brier_raw - cal.brier_cal
        print(f"  delta          = {delta:+.4f} ({'IMPROVED' if delta > 0 else 'WORSE'})")
    print(f"  blocks         = {len(cal.block_x)}")
    for x, y in zip(cal.block_x, cal.block_y):
        print(f"    p<= {x:.3f} -> {y:.3f}")

    if args.dry_run:
        print("dry-run: not persisted.")
        return 0

    path = save(cal)
    print(f"persisted to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
