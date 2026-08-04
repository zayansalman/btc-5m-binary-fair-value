"""Re-export abstract base classes and exceptions for connector authors."""

from __future__ import annotations

from btc_5m_exec.core.exceptions import FeedError, MarketDiscoveryError
from btc_5m_exec.core.interfaces import AbstractMarketConnector, AbstractPriceConnector

__all__ = [
    "AbstractMarketConnector",
    "AbstractPriceConnector",
    "FeedError",
    "MarketDiscoveryError",
]
