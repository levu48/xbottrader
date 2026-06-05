"""Historical OHLCV fetching for backtests (public ccxt, no keys).

A thin Protocol + ccxt-backed implementation, plus a candle→Bar converter. The
fetcher is injected so tests use a scripted fake instead of the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Protocol

from xbt_core.strategies.base import Bar

Candle = Sequence[Any]  # [ts_ms, open, high, low, close, volume]


class HistoricalDataClient(Protocol):
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", *, since: int | None = None, limit: int = 500
    ) -> list[Candle]:
        ...

    async def close(self) -> None:
        ...


def build_ccxt_data_client(exchange: str) -> HistoricalDataClient:
    """Construct a keyless ccxt async client for historical candles."""
    import ccxt.async_support as ccxt_async  # lazy: keeps boot ccxt-free

    cls = getattr(ccxt_async, exchange, None)
    if cls is None:
        raise ValueError(f"unknown exchange: {exchange!r}")
    return cls({"enableRateLimit": True})


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
