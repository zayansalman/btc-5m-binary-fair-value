"""One-time live-trading onboarding (issue #32).

There is NO "export private key" in the Polymarket product. The documented
path for API trading is a wallet you control:

1. A fresh EOA private key (this script generates one if you don't have one).
2. A relayer-created DEPOSIT WALLET owned by that key — deterministic
   address, gasless approvals, orders signed with signature type 3
   (POLY_1271). This is the flow Polymarket documents for API users.
3. Funding: move USDC/pUSD to the deposit wallet (withdrawing from an
   existing Polymarket UI account to it works — no key export needed).

Requires the official py-sdk for the one-time setup only:

    .venv/bin/pip install --pre polymarket-client

Day-to-day trading uses py-clob-client-v2 (already a core dependency);
verify the result with tools/live_preflight.py before launching live.

Usage:
    .venv/bin/python tools/live_setup.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")


def _generate_key() -> str:
    from eth_account import Account

    return Account.create().key.hex()


def main() -> int:
    key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    generated = False
    if not key:
        key = _generate_key()
        generated = True

    from eth_account import Account

    eoa = Account.from_key(key).address

    try:
        from polymarket import SecureClient
    except ImportError:
        print("py-sdk is not installed. One-time setup needs it:")
        print("    .venv/bin/pip install --pre polymarket-client")
        print("then re-run this script.")
        return 1

    print(f"signer EOA: {eoa}")
    print("creating SecureClient (deploys the default deposit wallet if needed)...")
    with SecureClient.create(private_key=key) as client:
        funder = str(client.wallet)
        print(f"deposit wallet (FUNDER): {funder}")
        print("running setup_trading_approvals() (idempotent, gasless)...")
        client.setup_trading_approvals()
        print("approvals OK")

    print("\n=== put this in .env (key shown ONCE — keep it safe) ===")
    print("BTC_BOT_MODE=live")
    if generated:
        print(f"POLYMARKET_PRIVATE_KEY={key}")
    else:
        print("POLYMARKET_PRIVATE_KEY=<already set>")
    print(f"POLYMARKET_FUNDER={funder}")
    print("POLYMARKET_SIGNATURE_TYPE=3")
    print("BTC_LIVE_CONFIRM=YES_I_UNDERSTAND")
    print("BTC_LIVE_MAX_TRADE_USD=5")
    print("BTC_PAPER_MIN_TRADE_USD=5")
    print("BTC_PAPER_MAX_TRADE_USD=5")
    print("\n=== funding ===")
    print(f"Send USDC/pUSD (Polygon) to the deposit wallet: {funder}")
    print("From an existing Polymarket UI account: Withdraw -> paste that address.")
    print("Then verify with: .venv/bin/python tools/live_preflight.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
