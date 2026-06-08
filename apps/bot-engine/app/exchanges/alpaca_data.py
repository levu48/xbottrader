"""Direct Alpaca stock market-data access.

ccxt's ``alpaca.fetch_ohlcv`` only serves *crypto* bars (it's hardcoded to
Alpaca's ``v1beta3/crypto/.../bars`` endpoint), so equity OHLCV is fetched
straight from Alpaca's Market Data v2 REST API instead. Shared by the dashboard
chart endpoint (one-shot) and the live bar feed (a polled
:class:`~app.exchanges.ccxt_live.MarketDataClient`).

Authenticated with the server-level data keys (``XBT_ALPACA_DATA_KEY`` /
``XBT_ALPACA_DATA_SECRET``); the free plan's IEX feed is sufficient for charts
and marks. Never log the keys.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

_DATA_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"

# Our timeframe ids → Alpaca's. Only these are exposed by the dashboard selector
# and used by the live feed.
_ALPACA_TIMEFRAMES = {"1m": "1Min", "5m": "5Min", "1h": "1Hour", "1d": "1Day"}


class EquityDataError(RuntimeError):
    """Equity bars couldn't be fetched (missing keys or upstream error).

    The message is surfaced to the dashboard, so keep it user-actionable and
    free of secrets.
    """


def _alpaca_timeframe(timeframe: str) -> str:
    tf = _ALPACA_TIMEFRAMES.get(timeframe)
    if tf is None:
        raise ValueError(f"unsupported timeframe {timeframe!r} for equities")
    return tf


def _alpaca_credentials() -> tuple[str, str]:
    key = os.environ.get("XBT_ALPACA_DATA_KEY")
    secret = os.environ.get("XBT_ALPACA_DATA_SECRET")
    if not key or not secret:
        raise EquityDataError(
            "Alpaca market-data keys not configured "
            "(set XBT_ALPACA_DATA_KEY / XBT_ALPACA_DATA_SECRET)"
        )
    return key, secret


def _iso_to_ms(t: str) -> int:
    """RFC-3339 timestamp (``2024-01-02T15:30:00Z``) → epoch milliseconds."""
    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _parse_bars(payload: dict[str, Any]) -> list[list[float]]:
    """Alpaca bars JSON → ccxt-style rows ``[ts_ms, o, h, l, c, v]``."""
    bars = payload.get("bars") or []
    return [
        [
            float(_iso_to_ms(b["t"])),
            float(b["o"]),
            float(b["h"]),
            float(b["l"]),
            float(b["c"]),
            float(b["v"]),
        ]
        for b in bars
    ]


async def _request_bars(
    client: httpx.AsyncClient, symbol: str, timeframe: str, limit: int
) -> list[list[float]]:
    """GET the most recent ``limit`` bars for ``symbol``, oldest-first.

    Fetches newest-first (``sort=desc``) so we always get the most recent window
    regardless of the data subscription's start bound, then reverses to the
    oldest-first order that charts and :class:`CcxtBarSource` expect (the last
    row being the in-progress candle).
    """
    tf = _alpaca_timeframe(timeframe)
    key, secret = _alpaca_credentials()
    params = {"timeframe": tf, "limit": limit, "feed": "iex", "sort": "desc"}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    try:
        resp = await client.get(
            _DATA_URL.format(symbol=symbol), params=params, headers=headers
        )
    except httpx.HTTPError as e:
        raise EquityDataError(f"could not reach Alpaca data API for {symbol}") from e
    if resp.status_code >= 400:
        raise EquityDataError(f"Alpaca data API returned {resp.status_code} for {symbol}")
    rows = _parse_bars(resp.json())
    rows.reverse()  # desc → asc (oldest-first)
    return rows


async def fetch_alpaca_stock_bars(
    symbol: str, timeframe: str, limit: int
) -> list[list[float]]:
    """One-shot fetch of stock OHLCV (oldest-first). Used by the chart endpoint."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await _request_bars(client, symbol, timeframe, limit)


class AlpacaStockDataClient:
    """A :class:`MarketDataClient` for equities, backed by Alpaca's REST API.

    Implements the same ``fetch_ohlcv`` / ``close`` slice that
    :class:`~app.exchanges.ccxt_live.CcxtBarSource` polls, so the live bar feed
    works for stocks despite ccxt only supporting Alpaca *crypto* bars. Holds one
    persistent httpx client across polls; ``close`` releases it.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", *, limit: int = 2
    ) -> list[list[float]]:
        return await _request_bars(self._client, symbol, timeframe, limit)

    async def close(self) -> None:
        await self._client.aclose()


def build_alpaca_stock_data_client(exchange: str) -> AlpacaStockDataClient:
    """Factory matching the launcher's public-client-factory shape."""
    return AlpacaStockDataClient()
