"""Probability calibration for the side-relative model output (#37).

The Black-Scholes-with-tie-mass fair-value model produces a raw probability
P(chosen side wins). Across the closed-trade journal this estimate has a
non-trivial Brier score, meaning the predicted probabilities are systematically
off — too confident in some bands, not confident enough in others. Calibration
corrects this without changing the underlying model: a monotonic map fitted from
``(raw_p, side_won)`` pairs.

We use pool-adjacent-violators (PAV) isotonic regression — monotonic by
construction, no hyperparameters, robust on samples in the low hundreds.

The live path applies the calibrator to BOTH ``fair_up`` and ``1 - fair_up``
and renormalises so they sum to 1 (we know exactly one outcome resolves).

When no calibration file exists, ``load()`` returns an ``IdentityCalibrator``,
so the bot's behaviour is unchanged until a fit runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _pav(xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
    """Pool-adjacent-violators on points sorted by *xs*.

    Returns the unique x-block boundaries and their fitted y-values.
    Implements the standard L2 isotonic regression as a stack of (sum, count, x)
    blocks merged whenever the running mean would violate monotonicity.
    """
    if not xs:
        return [], []
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    sx = [xs[i] for i in order]
    sy = [ys[i] for i in order]

    stack: list[list[float]] = []
    for x, y in zip(sx, sy):
        cur_sum, cur_n, cur_max_x = y, 1, x
        while stack and stack[-1][0] / stack[-1][1] >= cur_sum / cur_n:
            top_sum, top_n, _top_max_x = stack.pop()
            cur_sum += top_sum
            cur_n += top_n
        stack.append([cur_sum, cur_n, cur_max_x])

    block_x = [b[2] for b in stack]
    block_y = [b[0] / b[1] for b in stack]
    return block_x, block_y


@dataclass
class IsotonicCalibrator:
    """Piecewise-constant monotonic map from raw probability to calibrated.

    ``block_x`` are the right-edge x-coordinates of each PAV block in ascending
    order; ``block_y`` are the fitted means. ``transform`` does a binary search
    over ``block_x`` and falls back to the boundary y-values for out-of-range
    inputs (the calibrator is only fit where the strategy has decided to trade,
    so extrapolation is intentional and conservative).
    """

    block_x: list[float]
    block_y: list[float]
    n_samples: int = 0
    brier_raw: float | None = None
    brier_cal: float | None = None
    fit_at: str = ""
    meta: dict = field(default_factory=dict)

    def transform(self, p: float) -> float:
        if not self.block_x:
            return p
        if p <= self.block_x[0]:
            return self.block_y[0]
        if p >= self.block_x[-1]:
            return self.block_y[-1]
        lo, hi = 0, len(self.block_x) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.block_x[mid] < p:
                lo = mid + 1
            else:
                hi = mid
        return self.block_y[lo]

    def to_dict(self) -> dict:
        return {
            "kind": "isotonic_pav_v1",
            "block_x": self.block_x,
            "block_y": self.block_y,
            "n_samples": self.n_samples,
            "brier_raw": self.brier_raw,
            "brier_cal": self.brier_cal,
            "fit_at": self.fit_at,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IsotonicCalibrator":
        return cls(
            block_x=list(d.get("block_x") or []),
            block_y=list(d.get("block_y") or []),
            n_samples=int(d.get("n_samples") or 0),
            brier_raw=d.get("brier_raw"),
            brier_cal=d.get("brier_cal"),
            fit_at=d.get("fit_at") or "",
            meta=dict(d.get("meta") or {}),
        )

    @classmethod
    def fit(
        cls,
        probs: list[float],
        outcomes: list[float],
        *,
        fit_at: str = "",
        meta: dict | None = None,
    ) -> "IsotonicCalibrator":
        """Fit on parallel ``(predicted_prob, realised_outcome)`` lists."""
        if len(probs) != len(outcomes):
            raise ValueError("probs and outcomes must have equal length")
        block_x, block_y = _pav(probs, outcomes)
        cal = cls(
            block_x=block_x,
            block_y=block_y,
            n_samples=len(probs),
            fit_at=fit_at,
            meta=dict(meta or {}),
        )
        if probs:
            cal.brier_raw = sum(
                (p - y) ** 2 for p, y in zip(probs, outcomes)
            ) / len(probs)
            cal.brier_cal = sum(
                (cal.transform(p) - y) ** 2 for p, y in zip(probs, outcomes)
            ) / len(probs)
        return cal


@dataclass
class IdentityCalibrator:
    """Pass-through used when no calibration file exists or it is stale."""

    n_samples: int = 0
    fit_at: str = ""

    def transform(self, p: float) -> float:
        return p

    def to_dict(self) -> dict:
        return {"kind": "identity"}


CALIBRATION_FILENAME = "calibration.json"


def _default_path() -> Path:
    data_dir = os.environ.get("DATA_DIR", "./data")
    return Path(data_dir) / CALIBRATION_FILENAME


def load(path: Path | None = None) -> IsotonicCalibrator | IdentityCalibrator:
    """Load the persisted calibrator; identity fallback if unreadable/missing."""
    p = path or _default_path()
    try:
        with open(p) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return IdentityCalibrator()
    if d.get("kind") == "isotonic_pav_v1":
        return IsotonicCalibrator.from_dict(d)
    return IdentityCalibrator()


def save(cal: IsotonicCalibrator, path: Path | None = None) -> Path:
    p = path or _default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(cal.to_dict(), f, indent=2, sort_keys=True)
    os.replace(tmp, p)
    return p


def apply_to_pair(
    cal: IsotonicCalibrator | IdentityCalibrator, fair_up: float
) -> tuple[float, float]:
    """Calibrate ``fair_up`` and ``1 - fair_up`` and renormalise.

    Calibration is a non-linear map, so ``C(p) + C(1-p)`` is not guaranteed to
    equal 1 — but exactly one outcome resolves, so we renormalise. If both
    calibrated values are zero (degenerate), fall back to the raw pair.
    """
    p_up = cal.transform(fair_up)
    p_down = cal.transform(1.0 - fair_up)
    total = p_up + p_down
    if total <= 0:
        return fair_up, 1.0 - fair_up
    return p_up / total, p_down / total
