"""Binance bulk-archive loader for the Phase A measurement program (#180).

Downloads monthly/daily zips from data.binance.vision (public, keyless),
verifies the published SHA-256 checksum, and converts to parquet under
``data/binance_archive/parquet/``. Raw zips are kept in ``.../raw/`` unless
``--prune`` deletes them after a successful conversion (aggTrades months are
~0.5-0.7 GB each; 24 months of tape does not fit comfortably next to itself).

Datasets (all verified reachable 2026-08-10; liquidationSnapshot is 404 at
every path/date and is deliberately absent — liquidations are forward-record
only, see docs/CORRECTIONS.md):

    klines        spot|um   monthly, any interval (um 2026+ timestamps are in
                            MICROseconds; older files milliseconds — both handled)
    aggTrades     um        monthly (aggressor flag = is_buyer_maker)
    fundingRate   um        monthly
    premiumIndex  um        monthly klines (basis)
    metrics       um        DAILY only (open interest + long/short ratios, 5-min)

Usage::

    .venv/bin/python tools/binance_archive.py klines --market um --interval 1m \
        --symbol BTCUSDT --months 2024-07:2026-07
    .venv/bin/python tools/binance_archive.py aggTrades --symbol BTCUSDT \
        --months 2024-07:2024-09 --prune

Read-only with respect to the trading system; writes only under
``data/binance_archive/``. Never imported by the runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as _config  # noqa: E402

BASE = "https://data.binance.vision/data"
ROOT = _config.DATA_DIR / "binance_archive"

KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "n_trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
AGG_COLS = [
    "agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
    "transact_time", "is_buyer_maker",
]


def _month_range(spec: str) -> list[str]:
    """Expand ``2024-07:2024-09`` (inclusive) into ['2024-07', ...]."""
    if ":" not in spec:
        return [spec]
    lo, hi = spec.split(":", 1)
    y, m = (int(x) for x in lo.split("-"))
    y2, m2 = (int(x) for x in hi.split("-"))
    out = []
    while (y, m) <= (y2, m2):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _url(dataset: str, market: str, symbol: str, period: str, interval: str | None) -> str:
    seg = "spot" if market == "spot" else "futures/um"
    if dataset == "klines":
        return f"{BASE}/{seg}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{period}.zip"
    if dataset == "aggTrades":
        return f"{BASE}/{seg}/monthly/aggTrades/{symbol}/{symbol}-aggTrades-{period}.zip"
    if dataset == "fundingRate":
        return f"{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{period}.zip"
    if dataset == "premiumIndex":
        return f"{BASE}/futures/um/monthly/premiumIndexKlines/{symbol}/{interval}/{symbol}-{interval}-{period}.zip"
    if dataset == "metrics":  # daily files only
        return f"{BASE}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{period}.zip"
    raise ValueError(f"unknown dataset {dataset!r}")


def _download(url: str, dest: Path) -> bool:
    """Stream ``url`` to ``dest`` and verify the published SHA-256. False on 404."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return True
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  MISS 404 {url.rsplit('/', 1)[-1]}")
            return False
        raise
    # checksum sidecar: "<sha256>  <filename>"
    try:
        with urllib.request.urlopen(url + ".CHECKSUM", timeout=60) as r:
            expected = r.read().decode().split()[0].strip()
        actual = hashlib.sha256(dest.read_bytes()).hexdigest()
        if actual != expected:
            dest.unlink()
            raise RuntimeError(f"checksum mismatch for {dest.name}")
    except urllib.error.HTTPError:
        print(f"  WARN no checksum published for {dest.name}")
    return True


def _read_csv_zip(path: Path, names: list[str]) -> pd.DataFrame:
    """Read the single CSV inside ``path``; tolerate optional header rows."""
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    first = raw.split(b"\n", 1)[0]
    header = 0 if any(c.isalpha() for c in first.decode(errors="ignore")) else None
    df = pd.read_csv(io.BytesIO(raw), header=header, names=names if header is None else None)
    if header == 0:  # normalise venue header spellings to our names
        df.columns = [c.strip().lower() for c in df.columns]
        df = df.rename(columns={"create_time": "ts"})
    return df


def _to_utc(series: pd.Series) -> pd.Series:
    """Binance stamps are ms in older files, us in 2025+ um files — split by magnitude."""
    unit = pd.Series("ms", index=series.index).mask(series > 10**14, "us")
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns, UTC]")
    for u in ("ms", "us"):
        m = unit == u
        if m.any():
            out.loc[m] = pd.to_datetime(series[m], unit=u, utc=True)
    return out


def fetch(dataset: str, market: str, symbol: str, periods: list[str],
          interval: str | None, prune: bool) -> None:
    for period in periods:
        url = _url(dataset, market, symbol, period, interval)
        tag = f"{dataset}-{market}-{symbol}-{interval or ''}-{period}".replace("--", "-")
        raw = ROOT / "raw" / f"{tag}.zip"
        pq = ROOT / "parquet" / f"{tag}.parquet"
        if pq.exists():
            print(f"  ok (cached) {pq.name}")
            continue
        if not _download(url, raw):
            continue
        if dataset in ("klines", "premiumIndex"):
            df = _read_csv_zip(raw, KLINE_COLS)
            df["open_time"] = _to_utc(df["open_time"])
            df = df.drop(columns=["close_time", "ignore"], errors="ignore")
        elif dataset == "aggTrades":
            df = _read_csv_zip(raw, AGG_COLS)
            df["transact_time"] = _to_utc(df["transact_time"])
        else:  # fundingRate / metrics: small, keep venue columns as-is
            df = _read_csv_zip(raw, [])
        pq.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(pq, index=False)
        print(f"  ok {pq.name}  rows={len(df):,}")
        if prune:
            raw.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("dataset", choices=["klines", "aggTrades", "fundingRate", "premiumIndex", "metrics"])
    ap.add_argument("--market", default="um", choices=["um", "spot"])
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1m")
    ap.add_argument("--months", required=True,
                    help="YYYY-MM or YYYY-MM:YYYY-MM (inclusive); for metrics use YYYY-MM-DD[:...] daily form")
    ap.add_argument("--prune", action="store_true", help="delete raw zip after parquet conversion")
    a = ap.parse_args()
    periods = _month_range(a.months) if a.dataset != "metrics" else [a.months]
    fetch(a.dataset, a.market, a.symbol, periods,
          a.interval if a.dataset in ("klines", "premiumIndex") else None, a.prune)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
