"""Maker-mode backtest: would resting limit orders beat taking the spread? (#130)

Offline replay over recorded data with a REALISTIC fill model — the whole point
is to NOT recreate the paper-vs-live mirage (see
docs/superpowers/specs/2026-06-24-maker-strategy-design.md).

Two execution arms on the SAME ``fair_value_v0`` opportunities:
  * taker  = the recorded settled ``btc_model_shadow_positions`` rows (fill@ask,
             hold-to-settle, already net of the taker fee).
  * maker  = a resting BUY limit at price L; filled forward-only when the bet-side
             mid trades through L (conservative), held to the real settlement.

Verdict metric = expectancy per OPPORTUNITY (unfilled = $0), plus adverse-selection
accounting. Realism rules: forward-only fills, conservative mid-cross, a settlement
cutoff, a queue/fill-rate haircut, net of fees (maker fee default = taker; fee=0
sensitivity).

    .venv/bin/python tools/maker_backtest.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as _config  # noqa: E402,F401  (loads .env)
from btc_bot.shadow.fees import maker_net_pnl_per_share  # noqa: E402
from db import connect  # noqa: E402

MODEL = "fair_value_v0"
POLICIES = ("join_bid", "mid", "fair")
CUTOFF_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Pure functions (unit-tested)
# ---------------------------------------------------------------------------


def compute_limit(policy: str, opp: dict) -> float | None:
    """Resting-buy limit price for the bet side, set from the book at signal time.

    ``join_bid`` = best bid (most passive), ``mid`` = (bid+ask)/2, ``fair`` =
    the model's fair probability (post at fair value). Returns None when the
    required inputs are missing.
    """
    bid, ask, fair = opp.get("signal_bid"), opp.get("signal_ask"), opp.get("fair_prob")
    if policy == "join_bid":
        return bid
    if policy == "mid":
        return None if bid is None or ask is None else (bid + ask) / 2.0
    if policy == "fair":
        return fair
    raise ValueError(f"unknown policy {policy!r}")


def simulate_fill(
    limit: float | None, forward_mids: list[tuple[float, float]], cutoff: float
) -> bool:
    """A resting BUY at ``limit`` fills iff a LATER tick's bet-side mid trades
    through it (``mid <= limit``) while there is still ``>= cutoff`` seconds left.

    ``forward_mids`` are (remaining_seconds, mid) for ticks strictly AFTER the
    signal tick (forward-only is the caller's contract). Conservative: a single
    sub-limit mid with time on the clock is the fill signal; otherwise no fill.
    """
    if limit is None:
        return False
    for rs, mid in forward_mids:
        if mid is not None and rs is not None and rs >= cutoff and mid <= limit:
            return True
    return False


def _haircut_keep(window: str, haircut: float) -> bool:
    """Deterministic queue haircut: keep a fill for ~``haircut`` fraction of
    windows, stable across runs (md5 of the slug, no per-process salt)."""
    if haircut >= 1.0:
        return True
    if haircut <= 0.0:
        return False
    h = int(hashlib.md5(window.encode()).hexdigest(), 16) % 1000 / 1000.0
    return h < haircut


def run_backtest(
    opps: list[dict],
    *,
    policy: str,
    cutoff: float = CUTOFF_SECONDS,
    maker_fee_rate: float = 0.07,
    haircut: float = 1.0,
) -> dict:
    """Aggregate maker vs taker over the opportunity set for one limit policy.

    Verdict is per OPPORTUNITY: an unfilled maker contributes $0 (it simply did
    not trade), so ``exp_per_opp_maker`` is comparable to the taker baseline.
    """
    n_opp = len(opps)
    taker_total = sum(o["taker_pnl"] for o in opps)
    n_filled = filled_wins = 0
    winners = losers = filled_winners = filled_losers = 0
    maker_total = 0.0
    improvements: list[float] = []
    missed_winners_n = 0
    missed_winners_forgone = 0.0

    for o in opps:
        won = o["won"]
        winners += won
        losers += not won
        limit = compute_limit(policy, o)
        filled = simulate_fill(limit, o["forward_mids"], cutoff)
        if filled and not _haircut_keep(o["window"], haircut):
            filled = False
        if filled:
            assert limit is not None  # simulate_fill returns False for a None limit
            n_filled += 1
            filled_wins += won
            filled_winners += won
            filled_losers += not won
            maker_total += o["shares"] * maker_net_pnl_per_share(limit, won, maker_fee_rate)
            if o.get("taker_ask") is not None:
                improvements.append(o["taker_ask"] - limit)
        elif won:
            missed_winners_n += 1
            missed_winners_forgone += o["taker_pnl"]

    return {
        "policy": policy,
        "n_opportunities": n_opp,
        "n_filled": n_filled,
        "fill_rate": n_filled / n_opp if n_opp else 0.0,
        "avg_improvement": sum(improvements) / len(improvements) if improvements else 0.0,
        "maker_total": maker_total,
        "taker_total": taker_total,
        "exp_per_opp_maker": maker_total / n_opp if n_opp else 0.0,
        "exp_per_opp_taker": taker_total / n_opp if n_opp else 0.0,
        "win_rate_filled": filled_wins / n_filled if n_filled else 0.0,
        "fill_rate_winners": filled_winners / winners if winners else 0.0,
        "fill_rate_losers": filled_losers / losers if losers else 0.0,
        "missed_winners_n": missed_winners_n,
        "missed_winners_forgone_taker_pnl": missed_winners_forgone,
    }


# ---------------------------------------------------------------------------
# DB load (read-only)
# ---------------------------------------------------------------------------


async def load_opportunities() -> list[dict]:
    """Build the opportunity set from settled fair_value_v0 shadow rows joined to
    the recorded tick book paths (signal book + forward mids)."""
    async with connect() as db:
        async with db.execute(
            "SELECT window_slug, created_at, side, entry_price, shares, fair_prob, "
            "realized_pnl_usd FROM btc_model_shadow_positions "
            "WHERE model_id=? AND state IN ('settled','closed') "
            "AND realized_pnl_usd IS NOT NULL",
            (MODEL,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        slugs = tuple({r["window_slug"] for r in rows})
        ticks_by_win: dict[str, list[dict]] = {}
        if slugs:
            q = (
                "SELECT window_slug, created_at, remaining_seconds, up_best_bid, "
                "up_best_ask, down_best_bid, down_best_ask, market_up_price, "
                "market_down_price FROM btc_paper_ticks WHERE window_slug IN (%s)"
                % ",".join("?" * len(slugs))
            )
            async with db.execute(q, slugs) as cur:
                for t in await cur.fetchall():
                    ticks_by_win.setdefault(t["window_slug"], []).append(dict(t))

    for _ts in ticks_by_win.values():
        _ts.sort(key=lambda t: t["created_at"])

    opps: list[dict] = []
    for r in rows:
        ts = ticks_by_win.get(r["window_slug"])
        if not ts:
            continue
        up = r["side"] == "Up"
        sig = r["created_at"]
        # signal tick = latest tick at/before the signal time (the book it saw)
        before = [t for t in ts if t["created_at"] <= sig]
        signal_tick = before[-1] if before else ts[0]
        bid = signal_tick["up_best_bid"] if up else signal_tick["down_best_bid"]
        ask = signal_tick["up_best_ask"] if up else signal_tick["down_best_ask"]
        forward = [
            (t["remaining_seconds"], t["market_up_price"] if up else t["market_down_price"])
            for t in ts
            if t["created_at"] > sig
        ]
        opps.append(
            {
                "window": r["window_slug"],
                "side": r["side"],
                "taker_ask": r["entry_price"],
                "shares": r["shares"] or 0.0,
                "fair_prob": r["fair_prob"],
                "signal_bid": bid,
                "signal_ask": ask,
                "won": (r["realized_pnl_usd"] or 0.0) > 0,
                "taker_pnl": r["realized_pnl_usd"] or 0.0,
                "forward_mids": forward,
            }
        )
    return opps


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _fmt(r: dict) -> str:
    return (
        f"  {r['policy']:9} fill {r['fill_rate']*100:4.0f}% ({r['n_filled']:4}/{r['n_opportunities']:<4}) "
        f"improve {r['avg_improvement']*100:+5.2f}c | "
        f"maker/opp {r['exp_per_opp_maker']:+.4f}  taker/opp {r['exp_per_opp_taker']:+.4f}  "
        f"maker$ {r['maker_total']:+8.2f} vs taker$ {r['taker_total']:+8.2f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", type=float, default=CUTOFF_SECONDS)
    args = ap.parse_args()
    opps = asyncio.run(load_opportunities())
    print(f"=== Maker backtest (#130): {MODEL}, {len(opps)} opportunities, cutoff {args.cutoff:.0f}s ===")
    if not opps:
        print("  no opportunities — nothing to test.")
        return 0
    print("\n-- per-opportunity EV (maker fee = taker 0.07, full fills) --")
    base = [run_backtest(opps, policy=p, cutoff=args.cutoff) for p in POLICIES]
    for r in base:
        print(_fmt(r))
    print("\n-- adverse selection (fill rate winners vs losers; missed winners) --")
    for r in base:
        print(
            f"  {r['policy']:9} fill-winners {r['fill_rate_winners']*100:4.0f}%  "
            f"fill-losers {r['fill_rate_losers']*100:4.0f}%  "
            f"missed-winners {r['missed_winners_n']:4} "
            f"(forgone taker ${r['missed_winners_forgone_taker_pnl']:+.2f})"
        )
    print("\n-- sensitivities (per-opp maker EV) --")
    for p in POLICIES:
        fee0 = run_backtest(opps, policy=p, cutoff=args.cutoff, maker_fee_rate=0.0)
        hc = run_backtest(opps, policy=p, cutoff=args.cutoff, haircut=0.5)
        print(
            f"  {p:9} fee=0: {fee0['exp_per_opp_maker']:+.4f}   "
            f"50% fills: {hc['exp_per_opp_maker']:+.4f} (n={hc['n_filled']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
