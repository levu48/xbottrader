"""Live market-data feed and ccxt client construction.

Two concerns, both kept out of the order-placing adapter:

- :class:`CcxtBarSource` polls public OHLCV candles and yields :class:`Bar`s to
  the supervisor (MVP: polling ``fetch_ohlcv``, not ccxt.pro websockets).
- :func:`build_ccxt_client` / :func:`build_public_ccxt_client` construct ccxt
  ``async_support`` exchange instances. ccxt is imported lazily so the app boots
  without it and tests can inject fakes.

Credentials handling: :class:`ExchangeCredentials` carries the *decrypted* key
only as long as the ccxt client lives. Never log it, never persist it.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from ..strategies.base import Bar

# Venue ids that map to ccxt's "alpaca" exchange (US equities). Both the live and
# paper venue ids resolve to the same ccxt class; paper is selected via sandbox
# mode, not a different class.
_ALPACA_VENUES = frozenset({"alpaca", "alpaca-paper"})

# A single ccxt OHLCV row: [ts_ms, open, high, low, close, volume].
Candle = Sequence[Any]
MarkSink = Callable[[str, Decimal], None]


class MarketDataClient(Protocol):
    """The slice of a ccxt exchange the bar source needs. Lets tests fake it."""

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", *, limit: int = 2
    ) -> list[Candle]:
        ...

    async def close(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class ExchangeCredentials:
    api_key: str
    secret: str
    password: str | None = None

    @classmethod
    def from_plaintext(cls, plaintext: str) -> "ExchangeCredentials":
        """Parse the decrypted ``{"apiKey","secret","password"?}`` JSON."""
        d = json.loads(plaintext)
        api_key = d.get("apiKey") or d.get("api_key")
        secret = d.get("secret")
        if not api_key or not secret:
            raise ValueError("credentials must contain apiKey and secret")
        password = d.get("password") or d.get("passphrase")
        return cls(api_key=str(api_key), secret=str(secret), password=str(password) if password else None)


class CcxtBarSource:
    """Polls ``fetch_ohlcv`` and yields each newly-closed bar.

    Uses the last *closed* candle (ccxt returns the still-forming candle last),
    de-duplicated by timestamp so the same bar is never emitted twice. An optional
    ``mark_sink`` is invoked before each yield — paper mode passes
    ``PaperExchangeAdapter.update_mark`` so simulated fills price off real data.
    """

    def __init__(
        self,
        client: MarketDataClient,
        *,
        symbol: str,
        timeframe: str = "1m",
        poll_interval_s: float = 2.0,
        limit: int = 2,
        mark_sink: MarkSink | None = None,
    ) -> None:
        self._client = client
        self._symbol = symbol
        self._timeframe = timeframe
        self._poll_interval_s = poll_interval_s
        self._limit = limit
        self._mark_sink = mark_sink

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        last_ts: int | None = None
        while True:
            candles = await self._client.fetch_ohlcv(
                self._symbol, self._timeframe, limit=self._limit
            )
            candle = _latest_closed(candles)
            if candle is not None:
                ts = int(candle[0])
                if last_ts is None or ts > last_ts:
                    last_ts = ts
                    bar = _to_bar(candle, self._symbol)
                    if self._mark_sink is not None:
                        self._mark_sink(self._symbol, bar.close)
                    yield bar
            await asyncio.sleep(self._poll_interval_s)


def _latest_closed(candles: list[Candle]) -> Candle | None:
    if not candles:
        return None
    # ccxt's last row is the in-progress candle; prefer the previous (closed) one.
    return candles[-2] if len(candles) >= 2 else candles[-1]


def _to_bar(candle: Candle, symbol: str) -> Bar:
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


def build_ccxt_client(exchange: str, credentials: ExchangeCredentials) -> Any:
    """Construct an authed ccxt async client. The key lives only in this client.

    Only reached on the live path (paper bots use the PaperExchangeAdapter), so
    orders placed here are real. For Alpaca the venue id chooses the endpoint:
    ``alpaca-paper`` routes to ``paper-api.alpaca.markets`` via sandbox mode,
    while ``alpaca`` hits the live ``api.alpaca.markets`` — so a live Alpaca bot
    needs a *live* Alpaca API key (paper keys won't authenticate against it).
    Crypto venues are always live here.
    """
    cls = _exchange_class(exchange)
    config: dict[str, Any] = {
        "apiKey": credentials.api_key,
        "secret": credentials.secret,
        "enableRateLimit": True,
    }
    if credentials.password:
        config["password"] = credentials.password
    client = cls(config)
    if exchange == "alpaca-paper":
        client.set_sandbox_mode(True)  # → paper-api.alpaca.markets
    return client


def build_public_ccxt_client(exchange: str) -> Any:
    """Construct a ccxt async client for public market data.

    Crypto venues serve OHLCV without keys. Alpaca's market-data API requires
    authentication, so for Alpaca we source a server-level data key from the
    environment (``XBT_ALPACA_DATA_KEY`` / ``XBT_ALPACA_DATA_SECRET``). If those
    are unset the client is still constructed (so boot/tests don't fail), but
    live data fetches will error until the keys are provided.
    """
    cls = _exchange_class(exchange)
    config: dict[str, Any] = {"enableRateLimit": True}
    if _is_alpaca(exchange):
        key = os.environ.get("XBT_ALPACA_DATA_KEY")
        secret = os.environ.get("XBT_ALPACA_DATA_SECRET")
        if key and secret:
            config["apiKey"] = key
            config["secret"] = secret
    return cls(config)


def _is_alpaca(exchange: str) -> bool:
    return exchange in _ALPACA_VENUES


def _exchange_class(exchange: str) -> Any:
    import ccxt.async_support as ccxt_async  # lazy: keeps app boot ccxt-free

    # Both "alpaca" and "alpaca-paper" map to ccxt's single "alpaca" class.
    ccxt_id = "alpaca" if _is_alpaca(exchange) else exchange
    cls = getattr(ccxt_async, ccxt_id, None)
    if cls is None:
        raise ValueError(f"unknown exchange: {exchange!r}")
    return cls
