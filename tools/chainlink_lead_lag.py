"""Chainlink-vs-Binance BTC lead-lag analysis (issue #57).

Measures how much Chainlink BTC/USD lags Binance BTC/USDT. The bot's
``entry_edge_max=0.07`` stale-model cap currently rejects ~44% of live
signals on the hypothesis that ">7% edge means we've fallen behind a fast
market". This tool produces the evidence that hypothesis stands or falls
on: how far behind Binance does Chainlink typically run, and can we
predict the next Chainlink print direction from Binance's recent move.

Three statistics computed on the HF dataset
``aliplayer1/polymarket-crypto-updown``'s ``spot_prices`` config
(filtered to BTC: ``symbol='btc/usd' source='chainlink'`` and
``symbol='btcusdt' source='binance'``):

1. **Static gap** (bps): |chainlink - binance| / binance at every joined
   second. p50 / p90 / p99. The persistent floor below which the bot
   structurally cannot see edge.

2. **Reaction lag** (seconds): when Binance moves ≥ ``move_threshold_bps``
   in any direction over a ``move_window_s`` rolling window, how many
   seconds until Chainlink moves ≥ half that magnitude in the same
   direction? p50 / p90.

3. **Next-print predictability** (%): for each consecutive pair of
   Chainlink prints, compare the sign of the Chainlink delta to the sign
   of Binance's net move over the previous 10 seconds. Hit-rate above
   50% means Binance is informative for the bot's next-print problem.

Outputs JSON under ``data/lead_lag/latest.json`` and a stdout summary.
Read-only; never touches the trading loop or live database.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from huggingface_hub import hf_hub_download

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DATA_DIR  # noqa: E402

HF_REPO = "aliplayer1/polymarket-crypto-updown"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _download(filename: str) -> Path:
    return Path(
        hf_hub_download(HF_REPO, filename=filename, repo_type="dataset")
    )


def load_paired_btc_spots() -> pl.DataFrame:
    """One row per second with both Chainlink and Binance BTC prices joined.

    The raw ``spot_prices`` config carries ~2.2 M Chainlink and ~2.2 M
    Binance prints, each at roughly 1 Hz with occasional gaps. We bucket
    to 1-second resolution (last price per source per second), then
    inner-join on the second. Inner-join discards seconds where either
    source missed a print — that's intentional: lead-lag is only
    measurable where both feeds are live.
    """
    path = _download("data/spot_prices/part-0.parquet")
    df = (
        pl.read_parquet(path)
        .filter(
            ((pl.col("symbol") == "btc/usd") & (pl.col("source") == "chainlink"))
            | ((pl.col("symbol") == "btcusdt") & (pl.col("source") == "binance"))
        )
        .with_columns((pl.col("ts_ms") // 1_000).alias("ts_s"))
    )
    chainlink = (
        df.filter(pl.col("source") == "chainlink")
        .group_by("ts_s")
        .agg(pl.col("price").last().alias("chainlink"))
    )
    binance = (
        df.filter(pl.col("source") == "binance")
        .group_by("ts_s")
        .agg(pl.col("price").last().alias("binance"))
    )
    return chainlink.join(binance, on="ts_s", how="inner").sort("ts_s")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def static_gap_bps(paired: pl.DataFrame) -> dict[str, float]:
    """Persistent |chainlink - binance| floor at every joined second."""
    gaps = (
        paired.with_columns(
            ((pl.col("chainlink") - pl.col("binance")).abs() / pl.col("binance") * 10_000).alias("gap_bps")
        )["gap_bps"]
    )
    return {
        "n": gaps.len(),
        "p50": float(gaps.quantile(0.50)),
        "p90": float(gaps.quantile(0.90)),
        "p99": float(gaps.quantile(0.99)),
        "mean": float(gaps.mean()),
    }


def reaction_lag(
    paired: pl.DataFrame, move_threshold_bps: float = 10.0, move_window_s: int = 5
) -> dict[str, float | int]:
    """Median seconds from a Binance move ≥ threshold to Chainlink ≥ half match.

    Detects every second where Binance's price-change over the previous
    ``move_window_s`` exceeds ``move_threshold_bps`` (in either direction).
    For each such trigger we walk forward and record the first second
    where Chainlink's cumulative move from the trigger reaches half the
    Binance magnitude with the same sign. Misses (Chainlink never catches
    up within ``timeout_s``) are reported separately so the median isn't
    polluted by infinities.
    """
    n = paired.height
    if n < move_window_s + 2:
        return {"n_triggers": 0}
    bn = paired["binance"].to_list()
    cl = paired["chainlink"].to_list()
    ts = paired["ts_s"].to_list()

    triggers: list[float] = []  # lag seconds for matched triggers
    misses = 0
    timeout_s = 60
    for i in range(move_window_s, n - 1):
        # Binance move over the past move_window_s seconds.
        ref = bn[i - move_window_s]
        if ref <= 0:
            continue
        delta_bps = (bn[i] - ref) / ref * 10_000
        if abs(delta_bps) < move_threshold_bps:
            continue
        sign = 1.0 if delta_bps > 0 else -1.0
        target = abs(delta_bps) / 2.0
        cl_ref = cl[i]
        matched_at: int | None = None
        for j in range(i, min(n, i + timeout_s)):
            cl_move = (cl[j] - cl_ref) / cl_ref * 10_000 if cl_ref > 0 else 0.0
            if cl_move * sign >= target:
                matched_at = j
                break
        if matched_at is None:
            misses += 1
        else:
            triggers.append(float(ts[matched_at] - ts[i]))
    n_triggers = len(triggers) + misses
    if not triggers:
        return {
            "n_triggers": n_triggers,
            "n_matched": 0,
            "miss_rate": 1.0 if misses else 0.0,
            "p50_lag_s": None,
            "p90_lag_s": None,
            "move_threshold_bps": move_threshold_bps,
            "move_window_s": move_window_s,
            "timeout_s": timeout_s,
        }
    s = pl.Series("lag", triggers)
    return {
        "n_triggers": n_triggers,
        "n_matched": len(triggers),
        "miss_rate": misses / n_triggers,
        "p50_lag_s": float(s.quantile(0.50)),
        "p90_lag_s": float(s.quantile(0.90)),
        "mean_lag_s": float(s.mean()),
        "move_threshold_bps": move_threshold_bps,
        "move_window_s": move_window_s,
        "timeout_s": timeout_s,
    }


def next_print_predictability(
    paired: pl.DataFrame, lookback_s: int = 10
) -> dict[str, float | int]:
    """For each Chainlink delta, does Binance's prior lookback_s sign agree?

    Walks Chainlink second-by-second. Compares the SIGN of every non-zero
    Chainlink change to the SIGN of Binance's net change over the
    preceding ``lookback_s`` seconds at the same instant. Reports the
    fraction that agree — the conditionally most direct evidence that
    watching Binance gives us the next Chainlink print's direction.
    """
    n = paired.height
    if n < lookback_s + 2:
        return {"n_pairs": 0}
    cl = paired["chainlink"].to_list()
    bn = paired["binance"].to_list()

    agree = 0
    total = 0
    null_bn = 0
    for i in range(lookback_s, n):
        cl_delta = cl[i] - cl[i - 1]
        if cl_delta == 0:
            continue
        bn_delta = bn[i] - bn[i - lookback_s]
        if bn_delta == 0:
            null_bn += 1
            continue
        total += 1
        if (cl_delta > 0) == (bn_delta > 0):
            agree += 1
    return {
        "n_pairs": total,
        "n_null_binance": null_bn,
        "hit_rate": (agree / total) if total else None,
        "lookback_s": lookback_s,
    }


def regime_breakdown(paired: pl.DataFrame, sigma_window_s: int = 300) -> dict:
    """Split the static gap by regime: calm vs volatile rolling Binance σ."""
    enriched = paired.with_columns(
        ((pl.col("chainlink") - pl.col("binance")).abs() / pl.col("binance") * 10_000).alias("gap_bps"),
        pl.col("binance").pct_change().rolling_std(sigma_window_s).alias("bn_sigma"),
    ).drop_nulls("bn_sigma")
    if enriched.is_empty():
        return {}
    median_sigma = float(enriched["bn_sigma"].median())
    calm = enriched.filter(pl.col("bn_sigma") <= median_sigma)["gap_bps"]
    vol = enriched.filter(pl.col("bn_sigma") > median_sigma)["gap_bps"]
    return {
        "sigma_window_s": sigma_window_s,
        "median_binance_pct_sigma": median_sigma,
        "calm_gap_p50_bps": float(calm.quantile(0.50)) if calm.len() else None,
        "calm_gap_p90_bps": float(calm.quantile(0.90)) if calm.len() else None,
        "vol_gap_p50_bps": float(vol.quantile(0.50)) if vol.len() else None,
        "vol_gap_p90_bps": float(vol.quantile(0.90)) if vol.len() else None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    move_threshold_bps: float = 10.0,
    move_window_s: int = 5,
    lookback_s: int = 10,
    max_seconds: int | None = None,
) -> dict:
    paired = load_paired_btc_spots()
    if max_seconds:
        paired = paired.head(max_seconds)

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": HF_REPO,
        "n_paired_seconds": paired.height,
        "static_gap_bps": static_gap_bps(paired),
        "reaction_lag": reaction_lag(paired, move_threshold_bps, move_window_s),
        "next_print_predictability_10s": next_print_predictability(paired, lookback_s),
        "regime_breakdown": regime_breakdown(paired),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--move-threshold-bps", type=float, default=10.0)
    parser.add_argument("--move-window-s", type=int, default=5)
    parser.add_argument("--lookback-s", type=int, default=10)
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=None,
        help="Cap on paired seconds (default: all ~2M).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA_DIR / "lead_lag" / "latest.json",
    )
    args = parser.parse_args()

    report = run(
        move_threshold_bps=args.move_threshold_bps,
        move_window_s=args.move_window_s,
        lookback_s=args.lookback_s,
        max_seconds=args.max_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n=== Chainlink-vs-Binance BTC lead-lag ===")
    print(f"paired seconds        : {report['n_paired_seconds']:,}")
    g = report["static_gap_bps"]
    print(
        f"static |gap| bps      : p50 {g['p50']:.1f}  p90 {g['p90']:.1f}  p99 {g['p99']:.1f}"
    )
    rg = report["regime_breakdown"]
    if rg:
        print(
            f"  calm regime         : p50 {rg.get('calm_gap_p50_bps', '?'):.1f}  "
            f"p90 {rg.get('calm_gap_p90_bps', '?'):.1f}"
        )
        print(
            f"  volatile regime     : p50 {rg.get('vol_gap_p50_bps', '?'):.1f}  "
            f"p90 {rg.get('vol_gap_p90_bps', '?'):.1f}"
        )
    r = report["reaction_lag"]
    if r.get("p50_lag_s") is not None:
        print(
            f"reaction lag (s)      : p50 {r['p50_lag_s']:.1f}  p90 {r['p90_lag_s']:.1f}  "
            f"(n={r['n_triggers']}, miss {r['miss_rate'] * 100:.0f}%)"
        )
    else:
        print(f"reaction lag          : no triggers above {args.move_threshold_bps:.0f} bps")
    p = report["next_print_predictability_10s"]
    if p.get("hit_rate") is not None:
        print(
            f"next-print sign agree : {p['hit_rate'] * 100:.1f}%  "
            f"(n={p['n_pairs']:,}, lookback {p['lookback_s']}s)"
        )
    print(f"\nreport: {args.output}")


if __name__ == "__main__":
    main()
