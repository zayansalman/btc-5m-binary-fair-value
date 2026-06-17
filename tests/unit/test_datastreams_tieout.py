"""Pure, cred-free parts of the Data Streams tie-out tool (issue #91):
the HMAC signing-string format and the V3 report price decoder.
"""
from __future__ import annotations

import hashlib
import hmac

from tools.datastreams_tieout import decode_v3_price, sign_headers


def test_sign_headers_matches_documented_format() -> None:
    headers = sign_headers(
        "GET", "/api/v1/reports?feedID=0xabc&timestamp=100",
        b"", "key-uuid", "secret", timestamp_ms=1700000000000,
    )
    assert headers["Authorization"] == "key-uuid"
    assert headers["X-Authorization-Timestamp"] == "1700000000000"
    # Independently reconstruct: HMAC-SHA256(secret, "METHOD PATH BODYHASH KEY TS").
    body_hash = hashlib.sha256(b"").hexdigest()
    expected = hmac.new(
        b"secret",
        f"GET /api/v1/reports?feedID=0xabc&timestamp=100 {body_hash} key-uuid 1700000000000".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-Authorization-Signature-SHA256"] == expected


def _v3_full_report(price_scaled: int) -> str:
    """Build a synthetic Data Streams V3 fullReport with ``price`` at blob word 6."""
    context = b"\x00" * 96
    head = (
        context
        + (224).to_bytes(32, "big")  # offset -> reportBlob (right after the head)
        + (0).to_bytes(32, "big")    # offset rs (unused here)
        + (0).to_bytes(32, "big")    # offset ss
        + (0).to_bytes(32, "big")    # rawVs
    )
    words = [
        b"\x11" * 32,                       # 0 feedId
        (1).to_bytes(32, "big"),            # 1 validFromTimestamp
        (2).to_bytes(32, "big"),            # 2 observationsTimestamp
        (0).to_bytes(32, "big"),            # 3 nativeFee
        (0).to_bytes(32, "big"),            # 4 linkFee
        (3).to_bytes(32, "big"),            # 5 expiresAt
        price_scaled.to_bytes(32, "big", signed=True),  # 6 price (int192)
        (0).to_bytes(32, "big"),            # 7 bid
        (0).to_bytes(32, "big"),            # 8 ask
    ]
    blob_data = b"".join(words)
    blob = len(blob_data).to_bytes(32, "big") + blob_data
    return "0x" + (head + blob).hex()


def test_decode_v3_price_roundtrips() -> None:
    # 65000.12345678 at 8 decimals.
    full = _v3_full_report(6_500_012_345_678)
    assert decode_v3_price(full, decimals=8) == 65000.12345678


def test_decode_v3_price_handles_no_0x_prefix() -> None:
    full = _v3_full_report(5_000_000_000_000)  # 50000.0 @ 8dp
    assert decode_v3_price(full[2:], decimals=8) == 50000.0
