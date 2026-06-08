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


def _client(factory: Any, equity_fetcher: Any = None) -> TestClient:
    publisher = EventPublisher(FakeRedis())
    auth = InternalAuthenticator(SECRET.encode())
    kwargs: dict[str, Any] = {
        "launcher": object(),  # unused by the market route
        "publisher": publisher,
        "internal_auth": auth,
        "public_client_factory": factory,
    }
    if equity_fetcher is not None:
        kwargs["equity_bars_fetcher"] = equity_fetcher
    app = create_app(**kwargs)
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


# --- equities (Alpaca) take a separate fetch path, not ccxt ---

_EQUITY_CANDLES = [
    [1_700_000_000_000.0, 190.0, 191.0, 189.5, 190.5, 1000.0],
    [1_700_000_060_000.0, 190.5, 192.0, 190.0, 191.5, 800.0],
]


def test_ohlcv_equity_uses_equity_fetcher_not_ccxt() -> None:
    seen: list[tuple[str, str, int]] = []

    async def equity_fetcher(symbol: str, timeframe: str, limit: int) -> list[list[float]]:
        seen.append((symbol, timeframe, limit))
        return _EQUITY_CANDLES

    def crypto_factory(exchange: str) -> FakePublicClient:
        raise AssertionError("crypto path must not run for an equity venue")

    client = _client(crypto_factory, equity_fetcher)
    resp = client.get(
        "/market/ohlcv?exchange=alpaca&symbol=AAPL&timeframe=5m&limit=50",
        headers=_signed("GET", "/market/ohlcv", "u1", b""),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["candles"] == _EQUITY_CANDLES
    assert seen == [("AAPL", "5m", 50)]


def test_ohlcv_equity_missing_keys_maps_to_502() -> None:
    from app.api.market import EquityDataError

    async def equity_fetcher(symbol: str, timeframe: str, limit: int) -> list[list[float]]:
        raise EquityDataError("Alpaca market-data keys not configured (set ...)")

    client = _client(lambda ex: FakePublicClient(_CANDLES), equity_fetcher)
    resp = client.get(
        "/market/ohlcv?exchange=alpaca&symbol=AAPL",
        headers=_signed("GET", "/market/ohlcv", "u1", b""),
    )
    assert resp.status_code == 502, resp.text
    # The actionable message is surfaced to the dashboard.
    assert "keys not configured" in resp.json()["detail"]


def test_alpaca_stock_bars_requires_keys(monkeypatch: Any) -> None:
    import asyncio

    from app.api.market import EquityDataError, fetch_alpaca_stock_bars

    monkeypatch.delenv("XBT_ALPACA_DATA_KEY", raising=False)
    monkeypatch.delenv("XBT_ALPACA_DATA_SECRET", raising=False)
    try:
        asyncio.run(fetch_alpaca_stock_bars("AAPL", "1m", 10))
    except EquityDataError as e:
        assert "not configured" in str(e)
    else:
        raise AssertionError("expected EquityDataError when keys are unset")


def test_alpaca_stock_bars_parses_response(monkeypatch: Any) -> None:
    import asyncio

    import httpx

    from app.api import market

    monkeypatch.setenv("XBT_ALPACA_DATA_KEY", "k")
    monkeypatch.setenv("XBT_ALPACA_DATA_SECRET", "s")

    captured: dict[str, Any] = {}

    class FakeResp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            # Alpaca returns newest-first for sort=desc; the fetcher reverses it.
            return {
                "symbol": "AAPL",
                "bars": [
                    {"t": "2023-11-14T20:01:00Z", "o": 190.5, "h": 192.0, "l": 190.0, "c": 191.5, "v": 800},
                    {"t": "2023-11-14T20:00:00Z", "o": 190.0, "h": 191.0, "l": 189.5, "c": 190.5, "v": 1000},
                ],
            }

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...
        async def __aenter__(self) -> "FakeClient":
            return self
        async def __aexit__(self, *_: Any) -> None: ...
        async def get(self, url: str, params: Any, headers: Any) -> FakeResp:
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    candles = asyncio.run(market.fetch_alpaca_stock_bars("AAPL", "1m", 200))

    assert captured["url"].endswith("/v2/stocks/AAPL/bars")
    assert captured["params"]["timeframe"] == "1Min"
    assert captured["params"]["sort"] == "desc"
    # An explicit start window is sent (avoids Alpaca's empty-on-weekend default).
    assert captured["params"]["start"].endswith("Z")
    assert captured["headers"]["APCA-API-KEY-ID"] == "k"
    # Returned oldest-first: first candle is the 20:00 bar [ts_ms, o, h, l, c, v].
    assert candles[0] == [1_699_992_000_000.0, 190.0, 191.0, 189.5, 190.5, 1000.0]
    assert candles[-1][0] == 1_699_992_060_000.0  # 20:01 bar last
    assert len(candles) == 2


def test_alpaca_stock_bars_rejects_bad_timeframe(monkeypatch: Any) -> None:
    import asyncio

    from app.api.market import fetch_alpaca_stock_bars

    monkeypatch.setenv("XBT_ALPACA_DATA_KEY", "k")
    monkeypatch.setenv("XBT_ALPACA_DATA_SECRET", "s")
    try:
        asyncio.run(fetch_alpaca_stock_bars("AAPL", "2m", 10))
    except ValueError as e:
        assert "timeframe" in str(e)
    else:
        raise AssertionError("expected ValueError for an unsupported timeframe")
