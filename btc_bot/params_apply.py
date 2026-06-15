"""Layer 2 — operator-gated promotion of proposed -> active strategy params.

Reads ``$DATA_DIR/params_proposed.json`` (written by ``params_propose``) and,
when ``--confirm`` is passed, writes the same set to
``$DATA_DIR/params_active.json``. The live bot reloads from active per window
roll. Refuses without ``--confirm`` so accidental invocation is impossible.

Run::

    python -m btc_bot.params_apply           # prints the proposal, does NOT apply
    python -m btc_bot.params_apply --confirm # applies; live bot picks it up
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from dataclasses import replace

from btc_bot.params import load_active, load_proposed, save_active


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--confirm",
        action="store_true",
        help="REQUIRED to actually promote proposed -> active",
    )
    args = ap.parse_args()

    proposed = load_proposed()
    if proposed is None:
        print("no proposal found at params_proposed.json — run params_propose first.")
        return 2

    active = load_active()
    print("=== currently active ===")
    print(
        f"  entry_edge_min={active.entry_edge_min:.3f} "
        f"min_confidence={active.min_confidence:.2f} "
        f"min_remaining_seconds={active.min_remaining_seconds} "
        f"max_entry_price={active.max_entry_price:.2f} "
        f"source={active.source}"
    )
    print()
    print("=== proposed (pending) ===")
    print(
        f"  entry_edge_min={proposed.entry_edge_min:.3f} "
        f"min_confidence={proposed.min_confidence:.2f} "
        f"min_remaining_seconds={proposed.min_remaining_seconds} "
        f"max_entry_price={proposed.max_entry_price:.2f} "
        f"proposed_at={proposed.proposed_at}"
    )
    m = proposed.backtest_meta or {}
    if m:
        print(
            f"  backtest: current pnl=${m.get('current_pnl', 0.0):+.2f} -> "
            f"proposed pnl=${m.get('recommended_pnl', 0.0):+.2f} "
            f"(trades {m.get('current_trades')} -> {m.get('recommended_trades')})"
        )

    if not args.confirm:
        print()
        print("Refusing to apply without --confirm. Re-run with --confirm to promote.")
        return 1

    to_persist = replace(
        proposed,
        source="applied",
        applied_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    path = save_active(to_persist)
    print()
    print(f"PROMOTED -> {path}")
    print("Live bot reads from this file at the next window roll.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
