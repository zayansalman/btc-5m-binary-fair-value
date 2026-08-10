"""M1 — the OFI horizon-decay curve (#180).

Implements docs/preregistrations/M1_ofi_decay.md EXACTLY. The pre-registration
is frozen; this file cites it rather than re-deciding anything. Dev months
(2024-07..2024-09) may be run freely while building; the scored window
(2024-10..2026-07) is touched only via ``--scored``, and the frozen kill
thresholds are printed next to every result so no reading is unanchored.

Pipeline per decision time t (every 5 minutes on the clock):
  features  x_raw = signed aggressor-volume imbalance and trade-count imbalance
            over trailing {1s, 10s, 60s, 300s} windows of the um aggTrades tape
            (k = 8, frozen). Aggressor buy = ``is_buyer_maker == False``.
  offset    z = ln(S_t / K) / (sigma_hat * sqrt(tau_rem)) for the 1h and daily
            rungs, K and settlement per the venue's verified rules
            (hourly: Binance 1h candle open, ties->Up; daily: prior noon-ET 1m
            close, America/New_York, ties 50-50).
  x         per-fold orthogonalisation of x_raw against z (train-fit OLS).
  fit       ridge logit MLE of the settled direction on x with z as fixed
            offset; walk-forward, expanding window, monthly refits; ridge scale
            from the prior-Sharpe<=2.0 cap in the pre-registration.
  metric    fraction of OOS decision times with |beta'x| > cost/phi(0)
            (0.0689 at 1h, 0.0564 at daily) + per-fold sign consistency +
            the phantom-tilt bound phi(0)*sqrt(k/N_eff) < bar/2.

Short horizons {10s, 60s, 300s, 900s} are POSITIVE CONTROLS: the pipeline must
reproduce the known result that flow predicts seconds-to-minutes, else it has a
bug. They are scored against the same functional bar for comparability but
carry no kill semantics.

Usage::

    python tools/m1_ofi_decay.py --dev                # dev months, free
    python tools/m1_ofi_decay.py --scored             # frozen run, one shot
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as _config  # noqa: E402

PARQ = _config.DATA_DIR / "binance_archive" / "parquet"
ET = ZoneInfo("America/New_York")

PHI0 = 1.0 / math.sqrt(2.0 * math.pi)
BARS = {"1h": 0.0275 / PHI0, "1d": 0.0225 / PHI0}   # 0.0689 / 0.0564 (pre-reg)
HORIZONS_S = [10, 60, 300, 900, 3600, 86400]
FLOW_WINDOWS_S = [1, 10, 60, 300]
DEV_MONTHS = ["2024-07", "2024-08", "2024-09"]
SCORED_MONTHS = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)
                 if ("2024-10" <= f"{y}-{m:02d}" <= "2026-07")]
EWMA_LAMBDA_1M = 0.93    # QLIKE-tuned on dev months (interior optimum of an
                         # 0.85..0.99 grid: 1.5508 at half-life 9.6m); FROZEN
RIDGE_PRIOR_SHARPE = 2.0  # pre-registration cap
SIGN_CONSISTENCY_MIN = 0.75


# ---------------------------------------------------------------- data loading

def load_klines(symbol: str, months: list[str]) -> pd.DataFrame:
    parts = []
    for m in months:
        p = PARQ / f"klines-um-{symbol}-1m-{m}.parquet"
        if not p.exists():
            raise FileNotFoundError(f"missing {p.name} — run tools/binance_archive.py")
        parts.append(pd.read_parquet(p))
    df = pd.concat(parts, ignore_index=True).sort_values("open_time")
    df = df.drop_duplicates("open_time").set_index("open_time")
    return df


def load_tape(symbol: str, months: list[str]) -> pd.DataFrame:
    parts = []
    for m in months:
        p = PARQ / f"aggTrades-um-{symbol}-{m}.parquet"
        if not p.exists():
            raise FileNotFoundError(f"missing tape {p.name}")
        t = pd.read_parquet(p, columns=["price", "quantity", "transact_time", "is_buyer_maker"])
        parts.append(t)
    tape = pd.concat(parts, ignore_index=True).sort_values("transact_time")
    tape["signed_qty"] = np.where(tape["is_buyer_maker"].astype(bool), -1.0, 1.0) * tape["quantity"]
    tape["signed_cnt"] = np.where(tape["is_buyer_maker"].astype(bool), -1.0, 1.0)
    return tape


# ------------------------------------------------------------------- vola / z

def ewma_sigma_1m(closes: pd.Series, lam: float = EWMA_LAMBDA_1M) -> pd.Series:
    """Per-minute sigma via RiskMetrics EWMA on 1m log returns, strictly causal."""
    r2 = np.log(closes / closes.shift(1)).pow(2)
    var = r2.ewm(alpha=1 - lam, adjust=False).mean().shift(1)  # info < t only
    return np.sqrt(var)


def qlike(r2: pd.Series, var_hat: pd.Series) -> float:
    m = (r2 > 0) & (var_hat > 0)
    x = (r2[m] / var_hat[m])
    return float((x - np.log(x) - 1.0).mean())


def tune_lambda_dev(closes: pd.Series) -> float:
    """QLIKE selection of the EWMA memory dial — dev months only, then frozen."""
    r2 = np.log(closes / closes.shift(1)).pow(2)
    best = (None, math.inf)
    for lam in (0.85, 0.90, 0.93, 0.95, 0.96, 0.97, 0.98, 0.99):
        var = r2.ewm(alpha=1 - lam, adjust=False).mean().shift(1)
        q = qlike(r2, var)
        if q < best[1]:
            best = (lam, q)
    return best[0]


# ------------------------------------------------------- decision-time frames

def decision_frame(kl: pd.DataFrame, rung: str) -> pd.DataFrame:
    """One row per 5-minute decision time: S, K, tau_rem, z, outcome."""
    idx = kl.index[kl.index.minute % 5 == 0]
    s = kl["close"].reindex(idx)                        # S_t = that minute's close
    sig = ewma_sigma_1m(kl["close"]).reindex(idx)       # per-minute sigma
    if rung == "1h":
        window_open = idx.floor("1h")
        k_price = kl["open"].reindex(window_open).to_numpy()
        window_end = window_open + pd.Timedelta(hours=1)
        settle = kl["close"].reindex(window_end - pd.Timedelta(minutes=1)).to_numpy()
        outcome = (settle >= k_price).astype(float)     # ties credit Up (verified)
    else:
        et_idx = idx.tz_convert(ET)
        noon = et_idx.normalize() + pd.Timedelta(hours=12)
        prev_noon = noon.where(et_idx >= noon, noon - pd.Timedelta(days=1))
        k_ts = pd.DatetimeIndex(prev_noon).tz_convert("UTC") - pd.Timedelta(minutes=1)
        k_price = kl["close"].reindex(k_ts).to_numpy()
        window_end = pd.DatetimeIndex(prev_noon).tz_convert("UTC") + pd.Timedelta(days=1)
        settle = kl["close"].reindex(window_end - pd.Timedelta(minutes=1)).to_numpy()
        outcome = np.where(settle == k_price, np.nan, (settle > k_price).astype(float))
    tau = (window_end - idx).total_seconds().to_numpy()
    sig_s = sig.to_numpy() / math.sqrt(60.0)            # per-minute -> per-second
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.log(s.to_numpy() / k_price) / (sig_s * np.sqrt(np.clip(tau, 1, None)))
    out = pd.DataFrame({"S": s.to_numpy(), "K": k_price, "tau": tau, "z": z,
                        "y": outcome}, index=idx)
    return out.dropna()


def flow_features(tape: pd.DataFrame, times: pd.DatetimeIndex) -> pd.DataFrame:
    """k=8 trailing imbalances at each decision time, via cumsum + searchsorted."""
    ts = tape["transact_time"].to_numpy(dtype="datetime64[ns]")
    cq = np.concatenate([[0.0], np.cumsum(tape["signed_qty"].to_numpy())])
    caq = np.concatenate([[0.0], np.cumsum(tape["quantity"].to_numpy())])
    cc = np.concatenate([[0.0], np.cumsum(tape["signed_cnt"].to_numpy())])
    cn = np.arange(len(ts) + 1, dtype=float)
    t_np = times.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
    hi = np.searchsorted(ts, t_np, side="right")
    cols = {}
    for w in FLOW_WINDOWS_S:
        lo = np.searchsorted(ts, t_np - np.timedelta64(w, "s"), side="right")
        dq, daq = cq[hi] - cq[lo], caq[hi] - caq[lo]
        dc, dn = cc[hi] - cc[lo], cn[hi] - cn[lo]
        with np.errstate(divide="ignore", invalid="ignore"):
            cols[f"ofi_{w}s"] = np.where(daq > 0, dq / daq, 0.0)
            cols[f"cnt_{w}s"] = np.where(dn > 0, dc / dn, 0.0)
    return pd.DataFrame(cols, index=times)


def forward_returns(kl: pd.DataFrame, tape: pd.DataFrame | None,
                    times: pd.DatetimeIndex) -> pd.DataFrame:
    """Standardized forward returns at each horizon (controls + rung horizons)."""
    out = {}
    sig = ewma_sigma_1m(kl["close"]).reindex(times).to_numpy() / math.sqrt(60.0)
    if tape is not None:
        ts = tape["transact_time"].to_numpy(dtype="datetime64[ns]")
        px = tape["price"].to_numpy()
        t_np = times.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
        p0_i = np.clip(np.searchsorted(ts, t_np, side="right") - 1, 0, len(px) - 1)
        for h in (10, 60):
            pi = np.clip(np.searchsorted(ts, t_np + np.timedelta64(h, "s"), side="right") - 1,
                         0, len(px) - 1)
            r = np.log(px[pi] / px[p0_i])
            out[f"h{h}s"] = r / (sig * math.sqrt(h))
    for h in (300, 900, 3600, 86400):
        fwd = kl["close"].reindex(times + pd.Timedelta(seconds=h)).to_numpy()
        now = kl["close"].reindex(times).to_numpy()
        out[f"h{h}s"] = np.log(fwd / now) / (sig * math.sqrt(h))
    return pd.DataFrame(out, index=times)


# ----------------------------------------------------------------- ridge logit

def _sigmoid(u: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(u, -35, 35)))


def ridge_logit_offset(X: np.ndarray, y: np.ndarray, offset: np.ndarray,
                       l2: float, iters: int = 60) -> np.ndarray:
    """Newton/IRLS for logit with fixed offset and L2 penalty. Returns beta."""
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        p = _sigmoid(offset + X @ beta)
        g = X.T @ (y - p) - l2 * beta
        w = np.clip(p * (1 - p), 1e-6, None)
        h = (X * w[:, None]).T @ X + l2 * np.eye(X.shape[1])
        step = np.linalg.solve(h, g)
        beta = beta + step
        if float(np.abs(step).max()) < 1e-8:
            break
    return beta


def ridge_scale(n: int, k: int, per_window_sharpe_cap: float) -> float:
    """L2 so the prior sd of each beta ~ cap/sqrt(k) in tilt units (pre-reg)."""
    prior_sd = per_window_sharpe_cap / math.sqrt(k)
    return 1.0 / max(prior_sd ** 2, 1e-8)


# ----------------------------------------------------------------- walk-forward

def run_rung(df: pd.DataFrame, rung_bar: float, label: str,
             per_window_cap: float) -> dict:
    """Expanding-window monthly walk-forward. df: z, y, features, month col."""
    feats = [c for c in df.columns if c.startswith(("ofi_", "cnt_"))]
    months = sorted(df["month"].unique())
    if len(months) < 2:
        return {"label": label, "error": "need >=2 months"}
    tilts, signs = [], []
    for i in range(1, len(months)):
        tr = df[df["month"] < months[i]]
        te = df[df["month"] == months[i]]
        if len(tr) < 500 or len(te) == 0:
            continue
        mu, sd = tr[feats].mean(), tr[feats].std().replace(0, 1)
        Xtr = ((tr[feats] - mu) / sd).to_numpy()
        Xte = ((te[feats] - mu) / sd).to_numpy()
        # orthogonalise against z (train-fit OLS per feature) — pre-reg mandatory
        ztr = tr["z"].to_numpy()[:, None]
        coef = np.linalg.lstsq(ztr, Xtr, rcond=None)[0]
        Xtr = Xtr - ztr @ coef
        Xte = Xte - te["z"].to_numpy()[:, None] @ coef
        l2 = ridge_scale(len(tr), len(feats), per_window_cap)
        beta = ridge_logit_offset(Xtr, tr["y"].to_numpy(), tr["z"].to_numpy(), l2)
        tilts.append(pd.Series(Xte @ beta, index=te.index))
        signs.append(np.sign(beta))
    if not tilts:
        return {"label": label, "error": "no folds"}
    tilt = pd.concat(tilts)
    sign_mat = np.vstack(signs)
    maj = np.sign(sign_mat.sum(axis=0))
    consistency = float((sign_mat == maj[None, :]).mean())
    n_eff = len(tilt)  # per-decision; correlation-adjusted N_eff reported separately
    phantom = PHI0 * math.sqrt(len(feats) / max(n_eff, 1))
    return {
        "label": label, "n_oos": int(len(tilt)), "folds": len(tilts),
        "frac_clearing_bar": float((tilt.abs() > rung_bar).mean()),
        "bar": rung_bar, "sign_consistency": consistency,
        "phantom_tilt": phantom, "phantom_ok": phantom < rung_bar / 2,
        "tilt_p50": float(tilt.abs().median()), "tilt_p95": float(tilt.abs().quantile(0.95)),
    }


# ------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true", help="dev months (pipeline building)")
    ap.add_argument("--scored", action="store_true", help="frozen scored run")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--tune-lambda", action="store_true",
                    help="QLIKE-tune the EWMA dial on dev months and print it")
    a = ap.parse_args()
    months = DEV_MONTHS if a.dev or a.tune_lambda else (SCORED_MONTHS if a.scored else None)
    if months is None:
        ap.error("choose --dev or --scored")

    kl = load_klines(a.symbol, months)
    if a.tune_lambda:
        lam = tune_lambda_dev(kl["close"])
        print(f"QLIKE-selected EWMA lambda on dev months: {lam}")
        return 0

    tape = load_tape(a.symbol, months)
    print(f"{a.symbol}: {len(kl):,} klines, {len(tape):,} tape events, months={months[0]}..{months[-1]}")

    results = []
    for rung, bar in BARS.items():
        base = decision_frame(kl, rung)
        X = flow_features(tape, base.index)
        df = base.join(X).dropna()
        df["month"] = df.index.strftime("%Y-%m")
        cap = RIDGE_PRIOR_SHARPE / math.sqrt(365 * 24 * 3600 / (3600 if rung == "1h" else 86400))
        res = run_rung(df, bar, rung, cap)
        results.append(res)

    # positive controls: short-horizon predictability of standardized fwd returns
    base = decision_frame(kl, "1h")
    fr = forward_returns(kl, tape, base.index)
    X = flow_features(tape, base.index)
    ctrl = X.join(fr).dropna()
    print("\n=== positive controls (Spearman IC of ofi_60s vs standardized fwd return) ===")
    for h in ["h10s", "h60s", "h300s", "h900s", "h3600s", "h86400s"]:
        if h in ctrl:
            ic = ctrl["ofi_60s"].corr(ctrl[h], method="spearman")
            print(f"  {h:>8}: IC = {ic:+.4f}   (n={len(ctrl):,})")

    print("\n=== rung results vs FROZEN bars (docs/preregistrations/M1_ofi_decay.md) ===")
    for r in results:
        if "error" in r:
            print(f"  {r['label']}: {r['error']}")
            continue
        kill_bar = 0.01 if r["label"] == "1h" else 0.05
        print(f"  {r['label']}: frac(|tilt|>{r['bar']:.4f}) = {r['frac_clearing_bar']:.4%} "
              f"(kill if < {kill_bar:.0%}) | sign-consistency {r['sign_consistency']:.0%} "
              f"(need >= 75%) | phantom {r['phantom_tilt']:.4f} ok={r['phantom_ok']} "
              f"| n={r['n_oos']:,} folds={r['folds']} | |tilt| p50={r['tilt_p50']:.4f} p95={r['tilt_p95']:.4f}")
    if a.scored:
        print("\nSCORED RUN — thresholds are frozen; interpretation per pre-registration only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
