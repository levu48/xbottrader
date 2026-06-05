from __future__ import annotations

from decimal import Decimal

import pytest

from app.exchanges.ccxt_live import (
    CcxtBarSource,
    ExchangeCredentials,
    _latest_closed,
)


class FakeMarketData:
    """Returns one [closed, forming] OHLCV page per call from a scripted list."""

    def __init__(self, pages: list[list[list]]) -> None:
        self._pages = pages
        self.calls = 0
        self.closed = False

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", *, limit: int = 2) -> list[list]:
        page = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        return page

    async def close(self) -> None:
        self.closed = True


def _page(closed_ts: int, closed_close: str, forming_ts: int) -> list[list]:
    c = float(Decimal(closed_close))
    return [
        [closed_ts, c, c, c, c, 1.0],
        [forming_ts, c, c, c, c, 0.0],  # in-progress candle (ignored)
    ]


async def _take(source: CcxtBarSource, n: int) -> list:
    it = source.__aiter__()
    return [await it.__anext__() for _ in range(n)]


def test_latest_closed_prefers_second_to_last() -> None:
    assert _latest_closed([]) is None
    assert _latest_closed([[1]]) == [1]  # only one → use it
    assert _latest_closed([[1], [2]]) == [1]  # last is forming → use prior


async def test_yields_closed_bars_and_fires_mark_sink() -> None:
    pages = [_page(60_000, "50000", 120_000), _page(120_000, "50100", 180_000)]
    client = FakeMarketData(pages)
    marks: list[tuple[str, Decimal]] = []
    src = CcxtBarSource(
        client,
        symbol="BTC/USDT",
        poll_interval_s=0,
        mark_sink=lambda s, p: marks.append((s, p)),
    )

    bars = await _take(src, 2)

    assert [b.ts_ms for b in bars] == [60_000, 120_000]
    assert [b.close for b in bars] == [Decimal("50000"), Decimal("50100")]
    assert bars[0].symbol == "BTC/USDT"
    assert marks == [("BTC/USDT", Decimal("50000")), ("BTC/USDT", Decimal("50100"))]


async def test_deduplicates_unchanged_timestamp() -> None:
    # Same closed candle twice, then it advances → second pull skips the repeat.
    pages = [
        _page(60_000, "50000", 120_000),
        _page(60_000, "50000", 120_000),  # unchanged
        _page(120_000, "50100", 180_000),
    ]
    client = FakeMarketData(pages)
    src = CcxtBarSource(client, symbol="BTC/USDT", poll_interval_s=0)

    bars = await _take(src, 2)

    assert [b.ts_ms for b in bars] == [60_000, 120_000]
    assert client.calls == 3  # the repeated page was polled but not yielded


def test_credentials_from_plaintext() -> None:
    creds = ExchangeCredentials.from_plaintext('{"apiKey": "ak", "secret": "sk"}')
    assert creds.api_key == "ak" and creds.secret == "sk" and creds.password is None

    withpass = ExchangeCredentials.from_plaintext(
        '{"apiKey": "ak", "secret": "sk", "password": "pw"}'
    )
    assert withpass.password == "pw"

    with pytest.raises(ValueError):
        ExchangeCredentials.from_plaintext('{"apiKey": "ak"}')  # missing secret
