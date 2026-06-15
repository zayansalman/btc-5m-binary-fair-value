"""One-time live-trading onboarding (issues #32, #33).

There is NO "export private key" in the Polymarket product. The documented
path for API trading is a wallet you control:

1. A fresh EOA private key (this script generates one if you don't have one).
2. A relayer-created DEPOSIT WALLET owned by that key — deterministic
   address, gasless approvals, orders signed with signature type 3
   (POLY_1271). This is the flow Polymarket documents for API users.
3. Funding: move USDC/pUSD to the deposit wallet (withdrawing from an
   existing Polymarket UI account to it works — no key export needed).

SECURITY: the generated private key is written straight into ``.env`` and is
NEVER printed to the terminal — so it cannot leak into logs, scrollback, or
an assistant transcript. Only the PUBLIC signer/deposit addresses are shown.
``BTC_LIVE_CONFIRM`` is deliberately NOT written: you add that line yourself
as the final, conscious "I accept the risk" step before launching.

Requires the official py-sdk for the one-time setup only:

    .venv/bin/pip install --pre polymarket-client

Day-to-day trading uses py-clob-client-v2 (already a core dependency);
verify the result with tools/live_preflight.py before launching live.

Usage:
    .venv/bin/python tools/live_setup.py
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

# Keys this script manages in .env. BTC_LIVE_CONFIRM is intentionally absent.
_LIVE_KEYS = (
    "BTC_BOT_MODE",
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_FUNDER",
    "POLYMARKET_SIGNATURE_TYPE",
    "BTC_LIVE_MAX_TRADE_USD",
    "BTC_PAPER_MIN_TRADE_USD",
    "BTC_PAPER_MAX_TRADE_USD",
)


def _merge_env(text: str, updates: dict[str, str]) -> str:
    """Update existing KEY= lines in place; append any new keys. Comments kept."""
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key in _LIVE_KEYS:
        if key in updates and key not in seen:
            out.append(f"{key}={updates[key]}")
    return "\n".join(out).rstrip("\n") + "\n"


def _write_0600(path: Path, text: str) -> None:
    """Write a secret file created 0600 from the start (no world-readable window)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # enforce 0600 even if pre-existing


def _write_env_secure(updates: dict[str, str]) -> None:
    """Write updates into .env (backing up first). Both files are 0600 — a
    backup of a key file is itself a secret and must not be world-readable."""
    existing = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    if existing:
        _write_0600(PROJECT_ROOT / ".env.bak", existing)
    _write_0600(ENV_PATH, _merge_env(existing, updates))


def _generate_key() -> str:
    from eth_account import Account

    return Account.create().key.hex()


def main() -> int:
    key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    generated = not key
    if generated:
        key = _generate_key()
    if not key.startswith("0x"):
        key = "0x" + key

    from eth_account import Account

    eoa = Account.from_key(key).address

    try:
        from polymarket import SecureClient
        from polymarket.auth import BuilderApiKey
        from py_clob_client_v2 import ClobClient
    except ImportError:
        print("py-sdk is not installed. One-time setup needs it:")
        print("    .venv/bin/pip install --pre polymarket-client")
        print("then re-run this script.")
        return 1

    print(f"signer EOA (public): {eoa}")

    # The gasless deposit-wallet deploy is a relayed transaction, which needs a
    # Builder API Key. It is minted self-serve from the signer key (L1 -> L2 ->
    # builder), used only for this one-time deploy, and then discarded — trading
    # and allowance calls do NOT need it.
    try:
        clob = ClobClient("https://clob.polymarket.com", chain_id=137, key=key)
        clob.set_api_creds(clob.create_or_derive_api_key())
        bk_raw = clob.create_builder_api_key()
        builder = BuilderApiKey(
            key=bk_raw["key"], secret=bk_raw["secret"], passphrase=bk_raw["passphrase"]
        )
    except Exception as e:  # noqa: BLE001
        print(f"FAILED to mint builder API key: {type(e).__name__}: {e}")
        print("The private key was NOT written. Nothing changed.")
        return 1

    print("deploying the deposit wallet (gasless, signature type 3)...")
    try:
        with SecureClient.create(private_key=key, api_key=builder) as client:
            funder = str(client.wallet)
    except Exception as e:  # noqa: BLE001
        print(f"FAILED to deploy deposit wallet: {type(e).__name__}: {e}")
        print("The private key was NOT written. Nothing changed.")
        return 1
    print(f"deposit wallet / FUNDER (public): {funder}")

    _write_env_secure({
        "BTC_BOT_MODE": "live",
        "POLYMARKET_PRIVATE_KEY": key,
        "POLYMARKET_FUNDER": funder,
        "POLYMARKET_SIGNATURE_TYPE": "3",
        "BTC_LIVE_MAX_TRADE_USD": "5",
        "BTC_PAPER_MIN_TRADE_USD": "5",
        "BTC_PAPER_MAX_TRADE_USD": "5",
    })

    print("\n.env updated (key written, not shown; perms 0600; .env.bak saved).")
    print("\n*** BACK UP .env NOW — the key is the only way to control these funds. ***")
    print("\nThe collateral allowance is set automatically the first time the bot")
    print("connects to a FUNDED wallet (update_balance_allowance), so there is no")
    print("separate approval step. NEXT:")
    print(f"  1. Fund the deposit wallet with USDC/pUSD on Polygon: {funder}")
    print("     (From a Polymarket UI account: Withdraw -> paste that address.)")
    print("  2. Add this line to .env yourself (the conscious go-live step):")
    print("        BTC_LIVE_CONFIRM=YES_I_UNDERSTAND")
    print("  3. Verify: .venv/bin/python tools/live_preflight.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
