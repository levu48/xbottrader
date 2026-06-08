"""Tests for the public OHLCV market-data endpoint."""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from app.api.auth import InternalAuthenticator
from app.events.publisher import EventPublisher
from app.main import create_app

SECRET = "shared-internal-secret-test"


class FakeRedis:
    async def xadd(self, *a: Any, **k: Any) -> str:
        return "1-0"


class FakePublicClient:
    """Stands in for a ccxt async client: scripted candles, records close()."""

    def __init__(self, candles: list[list[float]]) -> None:
        self._candles = candles
        self.closed = False

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", *, limit: int = 2) -> list[list[float]]:
        return self._candles[:limit]

    async def close(self) -> None:
        self.closed = True


class RaisingPublicClient(FakePublicClient):
    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", *, limit: int = 2) -> list[list[float]]:
        raise RuntimeError("exchange rejected the request")


_CANDLES = [
    [1_700_000_000_000, 50000.0, 50100.0, 49900.0, 50050.0, 12.5],
    [1_700_000_060_000, 50050.0, 50200.0, 50000.0, 50150.0, 8.0],
]


def _signed(method: str, path: str, user_id: str, body: bytes) -> dict[str, str]:
    auth = InternalAuthenticator(SECRET.encode())
    ts = int(time.time())
    sig = auth.sign(method=method, path=path, ts=ts, user_id=user_id, body=body)
    return {"x-xbt-user": user_id, "x-xbt-ts": str(ts), "x-xbt-sig": sig}


def _client(factory: Any) -> TestClient:
    publisher = EventPublisher(FakeRedis())
    auth = InternalAuthenticator(SECRET.encode())
    app = create_app(
        launcher=object(),  # unused by the market route
        publisher=publisher,
        internal_auth=auth,
        public_client_factory=factory,
    )
    return TestClient(app)


def test_ohlcv_returns_candles() -> None:
    created: list[FakePublicClient] = []

    def factory(exchange: str) -> FakePublicClient:
        c = FakePublicClient(_CANDLES)
        created.append(c)
        return c

    client = _client(factory)
    # The signature covers only the path, not the query string.
    resp = client.get(
        "/market/ohlcv?exchange=binance&symbol=BTC/USDT&timeframe=1m&limit=200",
        headers=_signed("GET", "/market/ohlcv", "u1", b""),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["candles"] == _CANDLES
    # The per-request client must be closed (ccxt holds an aiohttp session).
    assert created and created[0].closed


def test_ohlcv_rejects_symbol_asset_class_mismatch() -> None:
    client = _client(lambda ex: FakePublicClient(_CANDLES))
    # AAPL (equity ticker) is invalid on a crypto venue → 400 before any fetch.
    resp = client.get(
        "/market/ohlcv?exchange=binance&symbol=AAPL&timeframe=1m",
        headers=_signed("GET", "/market/ohlcv", "u1", b""),
    )
    assert resp.status_code == 400, resp.text


def test_ohlcv_maps_fetch_failure_to_502() -> None:
    created: list[RaisingPublicClient] = []

    def factory(exchange: str) -> RaisingPublicClient:
        c = RaisingPublicClient(_CANDLES)
        created.append(c)
        return c

    client = _client(factory)
    resp = client.get(
        "/market/ohlcv?exchange=binance&symbol=BTC/USDT",
        headers=_signed("GET", "/market/ohlcv", "u1", b""),
    )
    assert resp.status_code == 502, resp.text
    # Even on failure the client is closed.
    assert created and created[0].closed


def test_ohlcv_requires_auth() -> None:
    client = _client(lambda ex: FakePublicClient(_CANDLES))
    resp = client.get("/market/ohlcv?symbol=BTC/USDT")
    assert resp.status_code == 401
