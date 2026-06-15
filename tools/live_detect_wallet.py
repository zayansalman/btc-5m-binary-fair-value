"""Detect the Polymarket funder wallet + signature type from a signer key (#34).

For accounts created by connecting an existing wallet (e.g. MetaMask), the
trading funds live in a deterministic proxy wallet controlled by that key —
NOT in the EOA itself. This script derives every candidate the key could
control (EOA, POLY_PROXY, Gnosis Safe), reads each one's on-chain collateral
(pUSD) balance on Polygon, picks the funded one, and writes the matching
POLYMARKET_FUNDER + POLYMARKET_SIGNATURE_TYPE into .env.

Deterministic, not guessed: the funded address's derivation IS its signature
type (EOA=0, POLY_PROXY=1, GNOSIS_SAFE=2).

Prereq: put your signer private key in .env as POLYMARKET_PRIVATE_KEY first
(MetaMask: Account details -> Show private key). The key is read locally and
never printed. Then:

    .venv/bin/python tools/live_detect_wallet.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from dotenv import load_dotenv  # noqa: E402

from live_setup import _write_env_secure  # noqa: E402

ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

# Public Polygon RPCs (no key), tried in order — endpoints rotate auth/limits.
POLYGON_RPCS = (
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
    "https://polygon.drpc.org",
)
COLLATERAL_TOKEN = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # pUSD, Polygon
DECIMALS = 6


def _balance_of(address: str) -> float:
    """On-chain collateral balance of an address (human units).

    Tries each public RPC until one answers; raises if they all fail so a
    transient RPC outage is never mistaken for a zero balance.
    """
    data = "0x70a08231" + address[2:].lower().rjust(64, "0")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": COLLATERAL_TOKEN, "data": data}, "latest"],
    }
    last_err: Exception | None = None
    for rpc in POLYGON_RPCS:
        try:
            r = httpx.post(rpc, json=payload, timeout=15)
            r.raise_for_status()
            result = r.json().get("result")
            if result is None:
                raise ValueError(r.json())
            return int(result, 16) / (10**DECIMALS)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"all Polygon RPCs failed: {last_err}")


def main() -> int:
    import os

    key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    if not key:
        print("Set POLYMARKET_PRIVATE_KEY in .env first "
              "(MetaMask: Account details -> Show private key).")
        return 1
    if not key.startswith("0x"):
        key = "0x" + key

    from eth_account import Account
    from polymarket.environments import PRODUCTION
    from polymarket._internal.wallet import (
        derive_proxy_wallet_address,
        derive_safe_wallet_address,
    )

    signer = Account.from_key(key).address
    cfg = PRODUCTION.wallet_derivation
    candidates = [
        (0, signer, "EOA"),
        (1, derive_proxy_wallet_address(signer, cfg), "POLY_PROXY (email/proxy)"),
        (2, derive_safe_wallet_address(signer, cfg), "GNOSIS_SAFE (MetaMask/browser)"),
    ]

    print(f"signer EOA (public): {signer}")
    print("checking on-chain pUSD balance of each candidate wallet...\n")
    funded = []
    try:
        for sig_type, addr, label in candidates:
            bal = _balance_of(addr)
            marker = "  <-- FUNDED" if bal > 0 else ""
            print(f"  type {sig_type} {label}: {addr}  ${bal:.2f}{marker}")
            if bal > 0:
                funded.append((sig_type, addr, label, bal))
    except RuntimeError as e:
        print(f"\nCould not read balances: {e}\nRe-run in a moment; .env unchanged.")
        return 1

    if not funded:
        print("\nNo candidate holds collateral. Either the wallet is unfunded, or "
              "this is not the key that controls your Polymarket balance.")
        return 1
    if len(funded) > 1:
        funded.sort(key=lambda x: -x[3])
        print(f"\nMultiple funded candidates; picking the largest (${funded[0][3]:.2f}).")

    sig_type, funder, label, bal = funded[0]
    print(f"\nDetected wallet: {label}")
    print(f"  FUNDER = {funder}")
    print(f"  SIGNATURE_TYPE = {sig_type}")
    print(f"  balance = ${bal:.2f}")

    _write_env_secure({
        "BTC_BOT_MODE": "live",
        "POLYMARKET_PRIVATE_KEY": key,
        "POLYMARKET_FUNDER": funder,
        "POLYMARKET_SIGNATURE_TYPE": str(sig_type),
        "BTC_LIVE_MAX_TRADE_USD": "5",
        "BTC_PAPER_MIN_TRADE_USD": "5",
        "BTC_PAPER_MAX_TRADE_USD": "5",
    })
    print("\n.env updated (funder + signature type written; key untouched; 0600).")
    print("\nNEXT:")
    print("  1. Add this line to .env yourself (the conscious go-live step):")
    print("        BTC_LIVE_CONFIRM=YES_I_UNDERSTAND")
    print("  2. Verify: .venv/bin/python tools/live_preflight.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
