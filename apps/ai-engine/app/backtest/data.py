"""Historical OHLCV fetching for backtests (public ccxt, no keys).

A thin Protocol + ccxt-backed implementation, plus a candle→Bar converter. The
fetcher is injected so tests use a scripted fake instead of the network.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Protocol

from xbt_core.strategies.base import Bar

Candle = Sequence[Any]  # [ts_ms, open, high, low, close, volume]

# Venue ids that map to ccxt's "alpaca" exchange (US equities).
_ALPACA_VENUES = frozenset({"alpaca", "alpaca-paper"})


class HistoricalDataClient(Protocol):
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", *, since: int | None = None, limit: int = 500
    ) -> list[Candle]:
        ...

    async def close(self) -> None:
        ...


def build_ccxt_data_client(exchange: str) -> HistoricalDataClient:
    """Construct a ccxt async client for historical candles.

    Crypto venues need no keys. Alpaca's data API requires auth, so for Alpaca we
    source a server-level data key from the environment (``XBT_ALPACA_DATA_KEY`` /
    ``XBT_ALPACA_DATA_SECRET``); the client still constructs without them so boot
    and tests don't fail, but fetches will error until they're set.
    """
    import ccxt.async_support as ccxt_async  # lazy: keeps boot ccxt-free

    is_alpaca = exchange in _ALPACA_VENUES
    ccxt_id = "alpaca" if is_alpaca else exchange
    cls = getattr(ccxt_async, ccxt_id, None)
    if cls is None:
        raise ValueError(f"unknown exchange: {exchange!r}")
    config: dict[str, Any] = {"enableRateLimit": True}
    if is_alpaca:
        key = os.environ.get("XBT_ALPACA_DATA_KEY")
        secret = os.environ.get("XBT_ALPACA_DATA_SECRET")
        if key and secret:
            config["apiKey"] = key
            config["secret"] = secret
    return cls(config)


def candle_to_bar(candle: Candle, symbol: str) -> Bar:
    ts, o, h, low, c, v = candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]
    return Bar(
        ts_ms=int(ts),
        symbol=symbol,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )
