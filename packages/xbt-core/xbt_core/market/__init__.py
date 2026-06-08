"""Market-session / trading-calendar layer.

Pure, offline, deterministic session logic so the same ``is_open`` answer is
available to live execution (bar source + circuit breaker) and to tests without
any network call. Crypto is always open; US equities follow NYSE regular hours.
"""

from .session import (
    AssetClass,
    CryptoSession,
    MarketSession,
    UsEquitySession,
    asset_class_for,
    session_for,
)

__all__ = [
    "AssetClass",
    "CryptoSession",
    "MarketSession",
    "UsEquitySession",
    "asset_class_for",
    "session_for",
]
