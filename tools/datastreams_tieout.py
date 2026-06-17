"""Tie-out Chainlink Data Streams vs Polymarket's settlement prints (issue #91).

READ-ONLY. Places no trades and touches no live state. Decides whether the
operator's first-party Data Streams BTC/USD feed reproduces the exact open/close
prints Polymarket settles the 5-minute market on — the gate before we'd ever swap
the fragile Polymarket scrape for the direct feed.

For each of the last N completed 5-minute windows it pulls:
  * Polymarket: openPrice(t) / closePrice(t+300)  (ChainlinkSettlementConnector)
  * Data Streams: the signed report's `price` at t and t+300, decoded from the
    V3 report blob and scaled by the stream's decimals.
and reports the cent-for-cent match rate. Acceptance (issue #91): >=99% exact
match after scaling.

SECRETS: the API key + secret are read from the environment and never logged.
Claude never handles them — you run this yourself:

    export DATASTREAMS_API_KEY=<uuid>
    export DATASTREAMS_API_SECRET=<secret>
    .venv/bin/python tools/datastreams_tieout.py --windows 100

Defaults target mainnet and the BTC/USD reference stream Polymarket settles on.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Pick up DATASTREAMS_* from the gitignored .env (same store as the wallet key),
# so the operator can persist creds there instead of exporting each shell.
load_dotenv()

from btc_5m_fv.connectors.chainlink_settlement import (  # noqa: E402
    ChainlinkSettlementConnector,
)

# Polymarket settles the BTC Up/Down 5m market on this Data Streams feed
# (BTC/USD-RefPrice-DS-Premium-Global-003); confirm against your dashboard.
DEFAULT_FEED_ID = (
    "0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8"
)
DEFAULT_DECIMALS = 8
WINDOW_SECONDS = 300
REST_BASE = {
    "mainnet": "https://api.dataengine.chain.link",
    "testnet": "https://api.testnet-dataengine.chain.link",
}


def sign_headers(
    method: str,
    full_path: str,
    body: bytes,
    api_key: str,
    api_secret: str,
    timestamp_ms: int,
) -> dict[str, str]:
    """Chainlink Data Streams HMAC auth headers (pure → unit-testable).

    Signature = HMAC-SHA256(secret, "METHOD FULL_PATH BODY_HASH API_KEY TS"),
    where BODY_HASH is the SHA-256 hex of the (possibly empty) body and
    FULL_PATH includes the query string exactly as sent.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    string_to_sign = f"{method} {full_path} {body_hash} {api_key} {timestamp_ms}"
    signature = hmac.new(
        api_secret.encode(), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "Authorization": api_key,
        "X-Authorization-Timestamp": str(timestamp_ms),
        "X-Authorization-Signature-SHA256": signature,
    }


def decode_v3_price(full_report_hex: str, decimals: int = DEFAULT_DECIMALS) -> float:
    """Decode the benchmark ``price`` from a Data Streams V3 ``fullReport``.

    ``fullReport`` is ``abi.encode(bytes32[3] context, bytes reportBlob, bytes32[]
    rs, bytes32[] ss, bytes32 rawVs)``. The blob is 9 static 32-byte words:
    feedId, validFrom, observations, nativeFee, linkFee, expiresAt, price, bid,
    ask — so ``price`` is word index 6 (int192, sign-extended in 32 bytes).
    """
    raw = bytes.fromhex(full_report_hex[2:] if full_report_hex.startswith("0x") else full_report_hex)
    # Head layout: context(3*32) + offset(reportBlob) + offset(rs) + offset(ss) + rawVs.
    blob_offset = int.from_bytes(raw[96:128], "big")
    blob_len = int.from_bytes(raw[blob_offset:blob_offset + 32], "big")
    blob = raw[blob_offset + 32:blob_offset + 32 + blob_len]
    price_word = blob[6 * 32:7 * 32]
    price_int = int.from_bytes(price_word, "big", signed=True)
    return price_int / (10 ** decimals)


@dataclass
class Row:
    window_start: int
    pm_open: float | None
    pm_close: float | None
    ds_open: float | None
    ds_close: float | None
    error: str | None = None

    def _match(self, a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and round(a, 2) == round(b, 2)

    @property
    def open_match(self) -> bool:
        return self._match(self.pm_open, self.ds_open)

    @property
    def close_match(self) -> bool:
        return self._match(self.pm_close, self.ds_close)


async def _ds_price(
    client: httpx.AsyncClient, base: str, feed_id: str, ts: int,
    api_key: str, api_secret: str, decimals: int, debug: bool,
) -> float | None:
    path = f"/api/v1/reports?feedID={feed_id}&timestamp={ts}"
    headers = sign_headers("GET", path, b"", api_key, api_secret, int(time.time() * 1000))
    r = await client.get(base + path, headers=headers, timeout=15.0)
    if r.status_code != 200:
        if debug:
            print(f"  [ds {ts}] HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None
    data = r.json()
    report = data.get("report") or data.get("reports") or data
    if isinstance(report, list):
        report = report[0] if report else {}
    # Prefer a decoded numeric field if the API returns one; else decode the blob.
    for key in ("benchmarkPrice", "price"):
        if isinstance(report.get(key), (int, float)):
            return float(report[key]) / (10 ** decimals if report[key] > 1e6 else 1)
    full = report.get("fullReport") or report.get("full_report")
    if not full:
        if debug:
            print(f"  [ds {ts}] no price/fullReport in: {str(data)[:300]}", file=sys.stderr)
        return None
    return decode_v3_price(full, decimals)


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def resolve_credentials() -> tuple[list[tuple[str, str]], str | None]:
    """Map the operator's .env vars onto (key candidates, secret).

    Chainlink's dashboard labels are misleading: the 36-char UUID under
    'username' is the API key (Client ID); the long token under 'websocket' is
    the API secret. We try every UUID-shaped value as the key since which one
    authenticates isn't knowable from labels alone.
    """
    secret = _env("DATASTREAMS_API_SECRET") or _env("CHAINLINK_WEBSOCKET")
    sources = [
        ("DATASTREAMS_API_KEY", _env("DATASTREAMS_API_KEY")),
        ("CHAINLINK_USERNAME", _env("CHAINLINK_USERNAME")),
        ("CHAINLINK_CANDLESTICK_API", _env("CHAINLINK_CANDLESTICK_API")),
    ]
    seen: set[str] = set()
    candidates = []
    for name, val in sources:
        if val and val not in seen:
            seen.add(val)
            candidates.append((name, val))
    return candidates, secret


async def probe(args: argparse.Namespace, candidates: list[tuple[str, str]], secret: str) -> int:
    """Auth + shape check: try each key candidate against /reports/latest and
    report which one authenticates. Prints HTTP status + the decoded BTC/USD
    price; never prints the key or secret (only the last 4 chars of the key id).
    """
    base = REST_BASE[args.network]
    path = f"/api/v1/reports/latest?feedID={args.feed_id}"
    print(f"# Data Streams probe — {args.network} · feed {args.feed_id[:14]}…")
    print(f"- endpoint: {base}/api/v1/reports/latest")
    ok = False
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for name, key in candidates:
            headers = sign_headers("GET", path, b"", key, secret, int(time.time() * 1000))
            try:
                r = await client.get(base + path, headers=headers, timeout=15.0)
            except Exception as e:  # noqa: BLE001
                print(f"- key={name} (…{key[-4:]}): request failed: {str(e)[:120]}")
                continue
            if r.status_code != 200:
                print(f"- key={name} (…{key[-4:]}): HTTP {r.status_code} — {r.text[:120]}")
                continue
            data = r.json()
            report = data.get("report") or data
            if isinstance(report, list):
                report = report[0] if report else {}
            price = None
            full = report.get("fullReport") or report.get("full_report")
            if full:
                try:
                    price = decode_v3_price(full, args.decimals)
                except Exception as e:  # noqa: BLE001
                    print(f"    decode error: {str(e)[:120]}")
            print(f"- key={name} (…{key[-4:]}): HTTP 200 ✓  keys={list(data.keys())}")
            print(f"    observationsTimestamp={report.get('observationsTimestamp')}  BTC/USD={price}")
            if price is None:
                print(f"    raw report (first 400, no secret): {str(report)[:400]}")
            ok = True
    if not ok:
        print(
            "\nNo key authenticated. Most likely the sponsored mainnet grant "
            "(your pm-ds-request form) is still pending — a normal account can't "
            "read this BTC feed yet. Re-run with --network testnet to check plumbing."
        )
        return 1
    print("\nAUTH OK — share this output and I'll wire the working key, then tie-out.")
    return 0


async def main(args: argparse.Namespace) -> int:
    candidates, api_secret = resolve_credentials()
    if not candidates or not api_secret:
        print(
            "ERROR: need an API key (CHAINLINK_USERNAME or DATASTREAMS_API_KEY) "
            "AND a secret (CHAINLINK_WEBSOCKET or DATASTREAMS_API_SECRET) in .env "
            "or the environment. Nothing was sent.",
            file=sys.stderr,
        )
        return 2
    if _env("BTCUSD_FEED_ID"):
        args.feed_id = _env("BTCUSD_FEED_ID")
    if args.probe:
        return await probe(args, candidates, api_secret)
    api_key = candidates[0][1]  # probe first to confirm which key authenticates
    base = REST_BASE[args.network]
    now = int(time.time())
    last_complete = (now // WINDOW_SECONDS) * WINDOW_SECONDS - WINDOW_SECONDS
    starts = [last_complete - i * WINDOW_SECONDS for i in range(args.windows)]

    rows: list[Row] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        pm = ChainlinkSettlementConnector(client)
        for t in starts:
            try:
                w = await pm.fetch_window(t)
                ds_open = await _ds_price(
                    client, base, args.feed_id, t, api_key, api_secret,
                    args.decimals, args.debug,
                )
                ds_close = await _ds_price(
                    client, base, args.feed_id, t + WINDOW_SECONDS, api_key,
                    api_secret, args.decimals, args.debug,
                )
                rows.append(Row(t, w.open_price, w.close_price, ds_open, ds_close))
            except Exception as e:  # noqa: BLE001
                rows.append(Row(t, None, None, None, None, error=str(e)[:80]))
            await asyncio.sleep(args.sleep)

    usable = [r for r in rows if r.error is None and r.pm_open is not None and r.ds_open is not None]
    print(f"# Data Streams ↔ Polymarket tie-out  (feed {args.feed_id[:14]}…, {args.network})")
    print(f"- windows requested: {len(rows)} · usable (both sides priced): {len(usable)}")
    if not usable:
        print("- No comparable windows. Re-run with --debug to see Data Streams responses;")
        print("  share the raw shape and I'll finalize the decoder / endpoint.")
        return 1
    opens = sum(r.open_match for r in usable)
    closes = sum(r.close_match for r in usable)
    n = len(usable)
    print(f"- OPEN  match: {opens}/{n} = {opens / n:.1%}")
    print(f"- CLOSE match: {closes}/{n} = {closes / n:.1%}")
    worst = sorted(
        usable,
        key=lambda r: max(
            abs((r.pm_open or 0) - (r.ds_open or 0)),
            abs((r.pm_close or 0) - (r.ds_close or 0)),
        ),
        reverse=True,
    )[:5]
    print("- largest diffs (window · pm_open/ds_open · pm_close/ds_close):")
    for r in worst:
        print(
            f"    {r.window_start}  "
            f"{r.pm_open}/{r.ds_open}  {r.pm_close}/{r.ds_close}"
        )
    verdict = "TIE-OUT PASS — safe to wire Data Streams as primary" if (
        opens / n >= 0.99 and closes / n >= 0.99
    ) else "TIE-OUT FAIL — keep the Polymarket scrape; investigate before swapping"
    print(f"\n{verdict}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Tie-out Data Streams vs Polymarket prints (#91)")
    ap.add_argument("--feed-id", default=DEFAULT_FEED_ID)
    ap.add_argument("--windows", type=int, default=100)
    ap.add_argument("--decimals", type=int, default=DEFAULT_DECIMALS)
    ap.add_argument("--network", choices=("mainnet", "testnet"), default="mainnet")
    ap.add_argument("--sleep", type=float, default=0.3, help="seconds between windows (rate limit)")
    ap.add_argument("--debug", action="store_true", help="print raw Data Streams responses on miss")
    ap.add_argument("--probe", action="store_true", help="single auth+shape check (latest report); no Polymarket compare")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
