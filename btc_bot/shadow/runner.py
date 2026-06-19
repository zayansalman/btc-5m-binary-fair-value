"""Shadow forward-tester runner.

Each tick, log what each candidate strategy *would* trade this window to
``btc_model_shadow_positions`` (idempotent per window/model), so the candidates
accumulate an out-of-sample record alongside the live v0 strategy. Settlement is
independent (see ``paper._settle_due_shadows``) and PnL is booked NET of the
Polymarket 7% taker fee. No real orders are ever placed from here — this is a
pure paper comparison harness.

Candidates:
- ``fair_value_v0``      — the live strategy, logged as the control baseline.
- ``cushion_favorite_v2``— v0 + a cushion gate (spot clearly on the favoured
  side of the strike): the only entry-knowable taker lean that survived.
- ``late_convergence_v3``— enters the final 5-30s on near-certainties the book
  under-prices (a regime v0's >=60s filter never trades).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

from btc_bot import strategy
from btc_bot.shadow import ledger, signals
from btc_bot.shadow.types import ShadowSignal, SnapshotView

if TYPE_CHECKING:
    # Import only for typing — paper.py imports this module, so a runtime import
    # here would be circular. Under TYPE_CHECKING there is no runtime import.
    from btc_bot.paper import PaperSnapshot

log = structlog.get_logger()

# Flat sizing — confidence-weighting was anti-predictive; flat is the robust
# choice. Shares match the bot's share-denominated default so notionals compare.
SHADOW_SHARES = 5.0


def build_view(snapshot: PaperSnapshot) -> SnapshotView:
    """Map a ``PaperSnapshot`` onto the minimal view the signals consume.

    ``market_up_price`` is the Up token MID (not the ask) so the favoured-side
    determination in late-convergence is unbiased; the executable asks are kept
    separately for entry pricing.
    """
    up_bid = snapshot.up_best_bid
    up_ask = snapshot.up_best_ask
    if up_bid is not None and up_ask is not None:
        market_up = (up_bid + up_ask) / 2.0
    elif snapshot.market_up_price is not None:
        market_up = float(snapshot.market_up_price)
    else:
        market_up = 0.5
    return SnapshotView(
        window_slug=snapshot.window_slug,
        remaining_seconds=snapshot.remaining_seconds,
        spot=snapshot.spot_price,
        reference=snapshot.reference_price,
        up_ask=up_ask,
        down_ask=snapshot.down_best_ask,
        market_up_price=market_up,
        fair_up=snapshot.fair_up_prob,
        sigma_per_second=snapshot.sigma_per_second,
        feed_source=snapshot.feed_source,
        quote_source=snapshot.quote_source,
    )


def _v0_control(
    view: SnapshotView, params: strategy.StrategyParams
) -> ShadowSignal | None:
    """v0 logged as the baseline — signal_from_executable_edges, no extra gate."""
    up_ask, down_ask = view.up_ask, view.down_ask
    edge_up = (view.fair_up - up_ask) if up_ask is not None else None
    edge_down = ((1.0 - view.fair_up) - down_ask) if down_ask is not None else None
    side, confidence, _notional, reason = strategy.signal_from_executable_edges(
        edge_up, edge_down, view.remaining_seconds, up_ask, down_ask, params
    )
    if side is None:
        return None
    entry = up_ask if side == "Up" else down_ask
    if entry is None:
        return None
    edge = edge_up if side == "Up" else edge_down
    fair = view.fair_up if side == "Up" else (1.0 - view.fair_up)
    return ShadowSignal(
        side=side,
        entry_price=float(entry),
        fair_prob=float(fair),
        edge=float(edge or 0.0),
        confidence=float(confidence),
        reason=reason,
    )


# Ordered so the control is logged first. Each candidate is callable as
# fn(view, params); cushion/late carry their own defaulted thresholds.
_MODELS: dict[
    str, Callable[[SnapshotView, strategy.StrategyParams], ShadowSignal | None]
] = {
    "fair_value_v0": _v0_control,
    "cushion_favorite_v2": signals.cushion_favorite_v2,
    "late_convergence_v3": signals.late_convergence_v3,
    "down_skeptic_v4": signals.down_skeptic_v4,
}


# --- Live model selection (operator-switchable active trading model) ----------
# The active model is stored in config under ACTIVE_MODEL_KEY and read every tick
# by the loop, so switching it from the dashboard takes effect with no restart,
# in paper AND live. v0 is the default and uses the loop's native signal path;
# the others dispatch through CANDIDATE_SIGNALS.
ACTIVE_MODEL_KEY = "btc_model.active"
DEFAULT_MODEL = "fair_value_v0"
MODEL_IDS: list[str] = list(_MODELS.keys())

MODEL_LABELS: dict[str, str] = {
    "fair_value_v0": "Fair-Value · Settle",
    "cushion_favorite_v2": "Cushion Favorite",
    "late_convergence_v3": "Late Convergence",
    "down_skeptic_v4": "Down-Skeptic",
}
MODEL_DESCRIPTIONS: dict[str, str] = {
    "fair_value_v0": "v0 baseline · edge 0.045–0.07 · favorites ≥0.50 · hold→resolution",
    "cushion_favorite_v2": "v0 + cushion: spot clearly on the favoured side of the strike",
    "late_convergence_v3": "final 5–45s · buy near-certainties (book ≥0.85)",
    "down_skeptic_v4": "v0 but Down needs +0.02 extra edge (prices the ≥-tie Up bias)",
}

# Candidate signal fns for the LIVE dispatch. v0 is intentionally absent — it
# uses the loop's native signal_from_executable_edges path.
CANDIDATE_SIGNALS: dict[
    str, Callable[[SnapshotView, strategy.StrategyParams], ShadowSignal | None]
] = {
    "cushion_favorite_v2": signals.cushion_favorite_v2,
    "late_convergence_v3": signals.late_convergence_v3,
    "down_skeptic_v4": signals.down_skeptic_v4,
}


def candidate_signal(
    model_id: str, view: SnapshotView, params: strategy.StrategyParams
) -> ShadowSignal | None:
    """Live-dispatch helper: the selected candidate's would-be trade, or None.

    Returns None for ``fair_value_v0`` / unknown ids — the caller falls back to
    the native v0 path for those.
    """
    fn = CANDIDATE_SIGNALS.get(model_id)
    return fn(view, params) if fn else None


async def record_shadow(
    snapshot: PaperSnapshot, params: strategy.StrategyParams
) -> None:
    """Log each candidate's would-be entry for this tick's window (idempotent)."""
    if snapshot.feed_degraded:
        return
    if not snapshot.has_executable_quote:
        return
    view = build_view(snapshot)
    for model_id, fn in _MODELS.items():
        try:
            sig = fn(view, params)
        except Exception as exc:  # noqa: BLE001 — a candidate must never break the loop
            log.warning("shadow.signal_error", model_id=model_id, error=str(exc))
            continue
        if sig is None:
            continue
        await ledger.record_shadow_signal(
            created_at=snapshot.created_at,
            window_slug=view.window_slug,
            model_id=model_id,
            side=sig.side,
            entry_price=sig.entry_price,
            fair_prob=sig.fair_prob,
            edge=sig.edge,
            confidence=sig.confidence,
            reason=sig.reason,
            notional_usd=SHADOW_SHARES * sig.entry_price,
            shares=SHADOW_SHARES,
            quote_source=view.quote_source,
            feed_source=view.feed_source,
        )
