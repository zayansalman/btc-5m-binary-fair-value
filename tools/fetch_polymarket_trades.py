"""Pull this account's Polymarket trade history via the CLOB API → CSV.

Replaces the manual "export from Polymarket → drop CSV in data/" flow. Uses
the same auth the live executor uses (POLYMARKET_PRIVATE_KEY etc.) so no
extra credentials are needed.

The output CSV matches the schema ``btc_bot.backtest.build_opportunities``
expects, so the existing backtest tool runs against it unchanged:

    timestamp, action, marketName, tokenName, usdcAmount, tokenAmount, hash

Usage:
    .venv/bin/python tools/fetch_polymarket_trades.py \\
        --since 30d \\
        --output data/polymarket_history_$(date +%F).csv

    .venv/bin/python tools/backtest_btc_strategy.py \\
        --history data/polymarket_history_2026-06-15.csv \\
        --output data/backtests/refresh_2026-06-15.json
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as _config  # noqa: E402
from btc_5m_fv.execution.live import assert_live_boot_allowed  # noqa: E402


# ---------------------------------------------------------------------------
# Time-range parsing
# ---------------------------------------------------------------------------


def _parse_since(spec: str) -> int:
    """Accept '30d', '7d', '24h', or an ISO date; return unix-seconds boundary."""
    spec = spec.strip().lower()
    now = datetime.now(UTC)
    if spec.endswith("d"):
        return int((now - timedelta(days=int(spec[:-1]))).timestamp())
    if spec.endswith("h"):
        return int((now - timedelta(hours=int(spec[:-1]))).timestamp())
    if spec.endswith("w"):
        return int((now - timedelta(weeks=int(spec[:-1]))).timestamp())
    # ISO date fallback (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ).
    dt = datetime.fromisoformat(spec.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Gamma resolver — condition_id / asset_id → market metadata
# ---------------------------------------------------------------------------


class GammaResolver:
    """Caches market lookups so we don't hit the gamma API once per trade."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._by_condition: dict[str, dict[str, Any]] = {}
        # Map outcome token_id → (market_name, side_label).
        self._by_token: dict[str, tuple[str, str]] = {}

    def lookup_token(self, token_id: str) -> tuple[str | None, str | None]:
        """Resolve (market_name, side_label) for an outcome token."""
        if token_id in self._by_token:
            return self._by_token[token_id]
        try:
            r = self._client.get(
                f"{_config.POLYMARKET_GAMMA_API}/markets",
                params={"clob_token_ids": token_id, "limit": 1},
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"gamma lookup failed for token {token_id[:16]}…: {e}", file=sys.stderr)
            self._by_token[token_id] = (None, None)
            return (None, None)
        markets = data if isinstance(data, list) else data.get("data", [])
        if not markets:
            self._by_token[token_id] = (None, None)
            return (None, None)
        m = markets[0]
        # Gamma returns clobTokenIds as a JSON-encoded string list, and a
        # parallel outcomes list. Position 0 is "Up" / "Yes", 1 is "Down" / "No".
        token_ids_raw = m.get("clobTokenIds") or "[]"
        try:
            token_ids = (
                eval(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
            )
        except Exception:  # noqa: BLE001
            token_ids = []
        outcomes_raw = m.get("outcomes") or "[]"
        try:
            outcomes = (
                eval(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            )
        except Exception:  # noqa: BLE001
            outcomes = []
        market_name = m.get("question") or m.get("groupItemTitle") or ""
        side = None
        for i, tid in enumerate(token_ids):
            if str(tid) == token_id and i < len(outcomes):
                side = outcomes[i]
                break
        self._by_token[token_id] = (market_name, side)
        return (market_name, side)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_trades(after_ts: int, before_ts: int | None) -> list[dict[str, Any]]:
    """Pull every fill for this account between *after_ts* and *before_ts*."""
    assert_live_boot_allowed()  # same gates as live boot
    from py_clob_client_v2 import ClobClient
    from py_clob_client_v2.clob_types import TradeParams

    client = ClobClient(
        _config.POLYMARKET_CLOB_API,
        chain_id=_config.POLYMARKET_CHAIN_ID,
        key=_config.POLYMARKET_PRIVATE_KEY,
        signature_type=_config.POLYMARKET_SIGNATURE_TYPE,
        funder=_config.POLYMARKET_FUNDER or None,
    )
    creds = client.create_or_derive_api_key()
    if creds is None:
        raise RuntimeError("Could not derive CLOB API credentials.")
    client.set_api_creds(creds)
    params = TradeParams(after=after_ts, before=before_ts)
    print(
        f"Pulling trades after={datetime.fromtimestamp(after_ts, UTC).isoformat()}"
        + (
            f" before={datetime.fromtimestamp(before_ts, UTC).isoformat()}"
            if before_ts
            else ""
        ),
        file=sys.stderr,
    )
    trades = client.get_trades(params=params)
    print(f"  fetched {len(trades)} trades", file=sys.stderr)
    return trades


# ---------------------------------------------------------------------------
# Map CLOB trade → backtest CSV row
# ---------------------------------------------------------------------------


def _trade_action(t: dict[str, Any]) -> str:
    """CLOB trades have a 'side' (BUY/SELL) at the taker level. Maker side is opposite."""
    # The user perspective: are they maker or taker? maker_address vs taker_address.
    user_addr = (_config.POLYMARKET_FUNDER or "").lower()
    if not user_addr:
        # Fall back to whatever the trade reports — usually correct.
        return (t.get("side") or "").lower()
    if (t.get("maker_address") or "").lower() == user_addr:
        # We're the maker. Maker side is the opposite of the taker side.
        taker = (t.get("side") or "").upper()
        return "sell" if taker == "BUY" else "buy"
    return (t.get("side") or "").lower()


def trade_to_row(t: dict[str, Any], resolver: GammaResolver) -> dict[str, Any] | None:
    """Convert one CLOB trade record to a CSV row compatible with build_opportunities.

    Returns ``None`` when the trade isn't a BTC 5m buy we can backtest.
    """
    action = _trade_action(t)
    if action != "buy":
        return None
    token_id = str(t.get("asset_id") or "")
    if not token_id:
        return None
    market_name, side_label = resolver.lookup_token(token_id)
    if not market_name or "Bitcoin Up or Down" not in market_name:
        return None
    if side_label not in ("Up", "Down"):
        return None
    try:
        price = float(t.get("price") or 0.0)
        size = float(t.get("size") or 0.0)
    except (TypeError, ValueError):
        return None
    if price <= 0 or size <= 0:
        return None
    notional = price * size
    match_time = t.get("match_time") or t.get("matched_time") or t.get("timestamp")
    try:
        ts = int(match_time)
    except (TypeError, ValueError):
        return None
    return {
        "timestamp": ts,
        "action": "BUY",
        "marketName": market_name,
        "tokenName": side_label,
        "usdcAmount": round(notional, 6),
        "tokenAmount": round(size, 6),
        "hash": t.get("transaction_hash") or t.get("tx_hash") or "",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


CSV_FIELDS = [
    "marketName",
    "action",
    "usdcAmount",
    "tokenAmount",
    "tokenName",
    "timestamp",
    "hash",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="30d",
        help="Pull trades since this point: '30d', '24h', '2w', or ISO date.",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="Optional upper bound (same format as --since).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / f"polymarket_history_{datetime.now(UTC).date()}.csv",
    )
    args = parser.parse_args()

    after_ts = _parse_since(args.since)
    before_ts = _parse_since(args.until) if args.until else None

    trades = fetch_trades(after_ts, before_ts)
    if not trades:
        print("No trades returned. Nothing to write.", file=sys.stderr)
        sys.exit(0)

    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=10.0, follow_redirects=True) as http:
        resolver = GammaResolver(http)
        for i, t in enumerate(trades):
            row = trade_to_row(t, resolver)
            if row is not None:
                rows.append(row)
            if (i + 1) % 100 == 0:
                print(f"  processed {i + 1}/{len(trades)}", file=sys.stderr)

    rows.sort(key=lambda r: r["timestamp"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Wrote {len(rows)} BTC-5m buy rows to {args.output} "
        f"(filtered from {len(trades)} total trades).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
