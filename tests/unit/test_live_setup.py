"""Unit tests for tools/live_setup.py .env merge (issue #33).

The merge writes a real private key, so its behavior is safety-critical:
update keys in place, preserve everything else, and NEVER auto-write the
go-live confirmation phrase.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from live_setup import _merge_env  # noqa: E402

_UPDATES = {
    "BTC_BOT_MODE": "live",
    "POLYMARKET_PRIVATE_KEY": "0xSECRET",
    "POLYMARKET_FUNDER": "0xFUND",
    "POLYMARKET_SIGNATURE_TYPE": "3",
    "BTC_LIVE_MAX_TRADE_USD": "5",
    "BTC_PAPER_MIN_TRADE_USD": "5",
    "BTC_PAPER_MAX_TRADE_USD": "5",
}


def test_updates_existing_keys_in_place():
    out = _merge_env("BTC_BOT_MODE=paper\nBTC_PAPER_MIN_TRADE_USD=1\n", _UPDATES)
    assert "BTC_BOT_MODE=live" in out
    assert "BTC_BOT_MODE=paper" not in out
    assert out.count("BTC_PAPER_MIN_TRADE_USD=") == 1
    assert "BTC_PAPER_MIN_TRADE_USD=5" in out


def test_preserves_comments_and_unmanaged_keys():
    out = _merge_env("# header\nDB_PATH=./data/x.db\n", _UPDATES)
    assert "# header" in out
    assert "DB_PATH=./data/x.db" in out


def test_appends_new_managed_keys():
    out = _merge_env("DB_PATH=./x\n", _UPDATES)
    assert "POLYMARKET_PRIVATE_KEY=0xSECRET" in out
    assert "POLYMARKET_FUNDER=0xFUND" in out


def test_never_auto_writes_confirm_phrase():
    # BTC_LIVE_CONFIRM is the operator's conscious go-live step — the setup
    # script must never write it, even if it somehow appears in updates.
    out = _merge_env("", {**_UPDATES, "BTC_LIVE_CONFIRM": "YES_I_UNDERSTAND"})
    assert "BTC_LIVE_CONFIRM" not in out


def test_idempotent_on_already_live_env():
    once = _merge_env("BTC_BOT_MODE=paper\n", _UPDATES)
    twice = _merge_env(once, _UPDATES)
    assert once == twice
