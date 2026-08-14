"""Tests for the copier's fill feed (#182).

The feed is where the copier's edge is won or lost: the data-api transport is
~20s stale and median slippage there is 9.56c against a ~1c/share edge, so
silently falling back to it produces numbers that look plausible and are
inverted. These tests pin the refusal.
"""

from __future__ import annotations

import pytest

from btc_bot.pairarb.feed import FeedFill, FeedUnavailable, open_feed


def test_refuses_to_open_a_stale_feed_by_default(monkeypatch):
    monkeypatch.delenv("POLYGON_RPC_WSS", raising=False)
    with pytest.raises(FeedUnavailable) as exc:
        open_feed("0xabc")
    assert "20s stale" in str(exc.value)


def test_stale_feed_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("POLYGON_RPC_WSS", raising=False)
    gen = open_feed("0xabc", allow_api_fallback=True)
    assert gen is not None
    gen.aclose()


def test_prefers_onchain_when_configured(monkeypatch):
    monkeypatch.setenv("POLYGON_RPC_WSS", "wss://example.invalid/v2/key")
    gen = open_feed("0xabc")
    assert gen is not None
    gen.aclose()


def test_every_fill_carries_its_own_observed_lag():
    """Lag must travel with the fill so a consumer can drop stale ones."""
    f = FeedFill(token_id="t", price=0.5, shares=5.0, observed_lag=21.4, source="api")
    assert f.observed_lag == 21.4
    assert f.is_maker is None  # the API transport cannot tell


def test_onchain_fill_can_report_maker_status():
    f = FeedFill(
        token_id="t", price=0.5, shares=5.0, observed_lag=0.0,
        source="onchain", is_maker=True,
    )
    assert f.is_maker is True
