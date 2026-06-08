"""Supervisor: the companion task runs alongside a bot and dies with it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest

from app.events.publisher import EventPublisher
from app.runtime.supervisor import Supervisor
from app.strategies.base import Bar, OrderIntent, Strategy, StrategyState


class FakeRedis:
    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        return "1-0"


class FakeRouter:
    async def submit(self, bot_id: str, intent: OrderIntent) -> list:
        return []


class NoopStrategy(Strategy):
    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        return []


class BlockingBars:
    """Yields one bar, then blocks forever — keeps the bot RUNNING until killed."""

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        yield Bar(
            ts_ms=0, symbol="BTC/USDT",
            open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
            close=Decimal("1"), volume=Decimal("0"),
        )
        await asyncio.Event().wait()


class RecordingCompanion:
    def __init__(self) -> None:
        self.started = False
        self.cancelled = False
        self.saw_state: StrategyState | None = None

    async def run(self, state: StrategyState) -> None:
        self.started = True
        self.saw_state = state
        try:
            while True:
                await asyncio.sleep(0.001)
        finally:
            self.cancelled = True


@pytest.mark.asyncio
async def test_companion_runs_and_is_cancelled_on_kill() -> None:
    sup = Supervisor(EventPublisher(FakeRedis()))
    companion = RecordingCompanion()
    await sup.start(
        bot_id="u1:bot-1",
        user_id="u1",
        strategy=NoopStrategy(),
        bars=BlockingBars(),
        router=FakeRouter(),
        companion=companion,
    )

    await asyncio.sleep(0.02)  # let the bot loop + companion spin up
    assert companion.started is True

    await sup.kill("u1:bot-1")
    assert companion.cancelled is True


@pytest.mark.asyncio
async def test_companion_shares_the_bots_strategy_state() -> None:
    sup = Supervisor(EventPublisher(FakeRedis()))
    companion = RecordingCompanion()
    await sup.start(
        bot_id="u1:bot-2",
        user_id="u1",
        strategy=NoopStrategy(),
        bars=BlockingBars(),
        router=FakeRouter(),
        companion=companion,
    )
    await asyncio.sleep(0.02)
    handle = sup._handles["u1:bot-2"]  # type: ignore[attr-defined]
    assert companion.saw_state is handle.strategy_state
    await sup.kill("u1:bot-2")
