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

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from xbt_core.market.session import AssetClass, asset_class_for

# EquityDataError / fetch_alpaca_stock_bars are re-exported here for backwards
# compatibility (and tests); the implementation lives in the exchanges layer,
# shared with the live bar feed.
from ..exchanges.alpaca_data import (  # noqa: F401
    EquityDataError,
    fetch_alpaca_stock_bars,
)
from ..exchanges.ccxt_live import build_public_ccxt_client
from .auth import InternalIdentity
from .bots import internal_identity  # reuse the signed-request dependency
from .schemas import OhlcvResponse, validate_symbol

router = APIRouter(prefix="/market", tags=["market"])

PublicClientFactory = Callable[[str], Any]
# (symbol, timeframe, limit) -> candles [[ts_ms, o, h, l, c, v], ...]
EquityBarsFetcher = Callable[[str, str, int], Awaitable[list[list[float]]]]

_MAX_LIMIT = 1000


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
