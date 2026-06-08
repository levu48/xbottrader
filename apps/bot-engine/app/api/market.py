"""Public market-data routes: OHLCV candles for the dashboard chart.

The candles are public, but the endpoint still rides the internal HMAC auth like
the rest of the control plane — only the Gateway can reach it.

Two fetch paths, by asset class:

- **Crypto** venues serve OHLCV keylessly via ccxt's ``fetch_ohlcv``.
- **US equities** (Alpaca) can't use ccxt here: ccxt's ``alpaca.fetch_ohlcv`` is
  hardcoded to Alpaca's *crypto* bars endpoint. So we call Alpaca's stock bars
  REST API (``v2/stocks/{symbol}/bars``) directly, authenticated with the
  server-level data keys (``XBT_ALPACA_DATA_KEY`` / ``XBT_ALPACA_DATA_SECRET``).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from xbt_core.market.session import AssetClass, asset_class_for

from ..exchanges.ccxt_live import build_public_ccxt_client
from .auth import InternalIdentity
from .bots import internal_identity  # reuse the signed-request dependency
from .schemas import OhlcvResponse, validate_symbol

router = APIRouter(prefix="/market", tags=["market"])

PublicClientFactory = Callable[[str], Any]
# (symbol, timeframe, limit) -> candles [[ts_ms, o, h, l, c, v], ...]
EquityBarsFetcher = Callable[[str, str, int], Awaitable[list[list[float]]]]

_MAX_LIMIT = 1000

# Our timeframe ids → Alpaca's. Only these are exposed by the dashboard selector.
_ALPACA_TIMEFRAMES = {"1m": "1Min", "5m": "5Min", "1h": "1Hour", "1d": "1Day"}


@router.get("/ohlcv", response_model=OhlcvResponse)
async def get_ohlcv(
    request: Request,
    exchange: str = Query("binance"),
    symbol: str = Query(...),
    timeframe: str = Query("1m"),
    limit: int = Query(200, ge=1, le=_MAX_LIMIT),
    _ident: InternalIdentity = Depends(internal_identity),
) -> OhlcvResponse:
    # Reject a symbol that doesn't match the venue's asset class (e.g. AAPL on
    # binance) before we ever touch the network.
    try:
        validate_symbol(exchange, symbol)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    if asset_class_for(exchange) is AssetClass.US_EQUITY:
        candles = await _fetch_equity(request, symbol, timeframe, limit)
    else:
        candles = await _fetch_crypto(request, exchange, symbol, timeframe, limit)

    return OhlcvResponse(
        exchange=exchange, symbol=symbol, timeframe=timeframe, candles=candles
    )


async def _fetch_crypto(
    request: Request, exchange: str, symbol: str, timeframe: str, limit: int
) -> list[list[float]]:
    factory: PublicClientFactory = getattr(
        request.app.state, "public_client_factory", build_public_ccxt_client
    )
    try:
        client = factory(exchange)
    except ValueError as e:  # unknown exchange id
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    try:
        raw = await client.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:  # noqa: BLE001 — ccxt network/exchange errors are opaque
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"market data unavailable for {symbol} on {exchange}",
        ) from e
    finally:
        await client.close()
    return [[float(v) for v in row[:6]] for row in raw]


async def _fetch_equity(
    request: Request, symbol: str, timeframe: str, limit: int
) -> list[list[float]]:
    fetcher: EquityBarsFetcher = getattr(
        request.app.state, "equity_bars_fetcher", fetch_alpaca_stock_bars
    )
    try:
        return await fetcher(symbol, timeframe, limit)
    except ValueError as e:  # unsupported timeframe, etc. — a client error
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except EquityDataError as e:  # missing keys / upstream — actionable detail
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — don't leak internals
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"market data unavailable for {symbol}",
        ) from e


class EquityDataError(RuntimeError):
    """Equity bars couldn't be fetched (missing keys or upstream error).

    The message is surfaced to the dashboard, so keep it user-actionable and
    free of secrets.
    """


async def fetch_alpaca_stock_bars(
    symbol: str, timeframe: str, limit: int
) -> list[list[float]]:
    """Fetch stock OHLCV from Alpaca's Market Data v2 REST API.

    Uses the server-level data keys. Free Alpaca plans only serve the IEX feed
    (``feed=iex``), which is sufficient for a chart.
    """
    tf = _ALPACA_TIMEFRAMES.get(timeframe)
    if tf is None:
        raise ValueError(f"unsupported timeframe {timeframe!r} for equities")

    key = os.environ.get("XBT_ALPACA_DATA_KEY")
    secret = os.environ.get("XBT_ALPACA_DATA_SECRET")
    if not key or not secret:
        raise EquityDataError(
            "Alpaca market-data keys not configured "
            "(set XBT_ALPACA_DATA_KEY / XBT_ALPACA_DATA_SECRET)"
        )

    import httpx

    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params = {"timeframe": tf, "limit": limit, "feed": "iex", "sort": "asc"}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise EquityDataError(f"could not reach Alpaca data API for {symbol}") from e
    if resp.status_code >= 400:
        raise EquityDataError(
            f"Alpaca data API returned {resp.status_code} for {symbol}"
        )

    bars = resp.json().get("bars") or []
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


def _iso_to_ms(t: str) -> int:
    """RFC-3339 timestamp (``2024-01-02T15:30:00Z``) → epoch milliseconds."""
    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)
