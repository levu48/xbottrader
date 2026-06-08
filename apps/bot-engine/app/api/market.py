"""Public market-data routes: OHLCV candles for the dashboard chart.

The candles are public (keyless for crypto venues), but the endpoint still rides
the internal HMAC auth like the rest of the control plane — only the Gateway can
reach it. The public ccxt client is built per-request and always closed, since
ccxt's async client holds an aiohttp session.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..exchanges.ccxt_live import build_public_ccxt_client
from .auth import InternalIdentity
from .bots import internal_identity  # reuse the signed-request dependency
from .schemas import OhlcvResponse, validate_symbol

router = APIRouter(prefix="/market", tags=["market"])

PublicClientFactory = Callable[[str], Any]

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
        # Don't leak ccxt internals; a 502 tells the Gateway it's an upstream
        # data problem (bad timeframe, market closed, Alpaca key missing, etc.).
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"market data unavailable for {symbol} on {exchange}",
        ) from e
    finally:
        await client.close()

    candles = [[float(v) for v in row[:6]] for row in raw]
    return OhlcvResponse(
        exchange=exchange, symbol=symbol, timeframe=timeframe, candles=candles
    )
