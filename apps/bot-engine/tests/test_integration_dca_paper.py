"""End-to-end: Supervisor + DCA strategy + PaperExchangeAdapter + ExchangeOrderRouter.

No fakes for the things under test — only the Redis transport is faked. This is
the test that proves the live trading loop closes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.events.publisher import EventPublisher
from app.exchanges.paper import PaperConfig, PaperExchangeAdapter
from app.runtime.router import ExchangeOrderRouter
from app.runtime.supervisor import BotState, Supervisor
from app.strategies.base import Bar
from app.strategies.dca import DcaParams, DcaStrategy


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


class BarFeedWithMarkUpdate:
    """Yields bars AND updates the paper adapter's mark before each yield.

    Mirrors the real architecture: live, the exchange WS pushes the bar AND the
    fresh mark together; here we do the same so the strategy and the adapter
    agree on price.
    """

    def __init__(self, bars: list[Bar], adapter: PaperExchangeAdapter) -> None:
        self._bars = bars
        self._adapter = adapter

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        for bar in self._bars:
            self._adapter.update_mark(bar.symbol, bar.close)
            yield bar
            await asyncio.sleep(0)


def _bar(ts_ms: int, close: str) -> Bar:
    return Bar(
        ts_ms=ts_ms,
        symbol="BTC/USDT",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("0"),
    )


async def test_dca_runs_end_to_end_through_paper_exchange(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    MIN = 60_000
    redis = FakeRedis()
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=10))
    router = ExchangeOrderRouter(
        user_id="u1",
        adapter=adapter,
        publisher=EventPublisher(redis),
        session_factory=session_factory,
    )

    supervisor = Supervisor(EventPublisher(redis))
    bars = BarFeedWithMarkUpdate(
        bars=[
            _bar(0 * MIN, "50000"),
            _bar(1 * MIN, "50100"),
            _bar(2 * MIN, "50250"),
        ],
        adapter=adapter,
    )

    strategy = DcaStrategy(
        DcaParams(symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=1)
    )

    await supervisor.start(
        bot_id="b1",
        user_id="u1",
        strategy=strategy,
        bars=bars,
        router=router,
    )
    handle = supervisor._handles["b1"]  # type: ignore[attr-defined]
    assert handle.task is not None
    await handle.task

    assert supervisor.get_state("b1") == BotState.STOPPED

    types = [f["event_type"] for _, f in redis.entries]
    # bot_started, then per-bar (order_submitted, fill) x 3, then bot_stopped
    assert types[0] == "bot_started"
    assert types[-1] == "bot_stopped"

    order_submitted_count = types.count("order_submitted")
    fill_count = types.count("fill")
    assert order_submitted_count == 3
    assert fill_count == 3

    # Verify each fill was at the bar's close (zero slippage in test config)
    fill_prices = [
        Decimal(json.loads(fields["payload"])["price"])
        for _, fields in redis.entries
        if fields["event_type"] == "fill"
    ]
    assert fill_prices == [Decimal("50000"), Decimal("50100"), Decimal("50250")]

    # And the total bought = sum of quantities = 100/p for each bar
    qtys = [
        Decimal(json.loads(fields["payload"])["quantity"])
        for _, fields in redis.entries
        if fields["event_type"] == "fill"
    ]
    expected = [Decimal("100") / p for p in fill_prices]
    assert qtys == expected
