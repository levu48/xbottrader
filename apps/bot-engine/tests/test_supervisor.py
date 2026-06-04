from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest

from app.events.publisher import EventPublisher
from app.runtime.supervisor import BotState, Supervisor
from app.strategies.base import Bar, OrderIntent, Strategy, StrategyState


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


class FakeRouter:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, OrderIntent]] = []

    async def submit(self, bot_id: str, intent: OrderIntent) -> None:
        self.submitted.append((bot_id, intent))


class FiniteBars:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        for b in self._bars:
            yield b
            await asyncio.sleep(0)


class InfiniteBars:
    def __init__(self, bar: Bar) -> None:
        self._bar = bar

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        while True:
            yield self._bar
            await asyncio.sleep(0.001)


class AlwaysBuy(Strategy):
    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        state.last_action_ts_ms = bar.ts_ms
        return [
            OrderIntent(
                symbol=bar.symbol,
                side="buy",
                type="market",
                quantity=Decimal("1"),
            )
        ]


class BlowUp(Strategy):
    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        raise RuntimeError("strategy exploded")


def _bar(ts: int = 0, sym: str = "BTC/USDT") -> Bar:
    return Bar(
        ts_ms=ts,
        symbol=sym,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


async def test_supervisor_runs_through_finite_bars() -> None:
    redis = FakeRedis()
    router = FakeRouter()
    sup = Supervisor(EventPublisher(redis))

    await sup.start(
        bot_id="b1",
        user_id="u1",
        strategy=AlwaysBuy(),
        bars=FiniteBars([_bar(0), _bar(1), _bar(2)]),
        router=router,
    )
    handle_task = sup._handles["b1"].task  # type: ignore[attr-defined]
    assert handle_task is not None
    await handle_task

    assert sup.get_state("b1") == BotState.STOPPED
    assert len(router.submitted) == 3
    event_types = [f["event_type"] for _, f in redis.entries]
    assert event_types == ["bot_started", "bot_stopped"]


async def test_supervisor_stop_cancels_running_bot() -> None:
    redis = FakeRedis()
    router = FakeRouter()
    sup = Supervisor(EventPublisher(redis))

    await sup.start(
        bot_id="b2",
        user_id="u1",
        strategy=AlwaysBuy(),
        bars=InfiniteBars(_bar()),
        router=router,
    )
    await asyncio.sleep(0.01)
    await sup.stop("b2")

    assert sup.get_state("b2") in (BotState.STOPPED, BotState.STOPPING)


async def test_supervisor_emits_bot_error_on_strategy_crash() -> None:
    redis = FakeRedis()
    router = FakeRouter()
    sup = Supervisor(EventPublisher(redis))

    await sup.start(
        bot_id="b3",
        user_id="u1",
        strategy=BlowUp(),
        bars=FiniteBars([_bar()]),
        router=router,
    )
    await sup._handles["b3"].task  # type: ignore[attr-defined]

    assert sup.get_state("b3") == BotState.ERRORED
    event_types = [f["event_type"] for _, f in redis.entries]
    assert "bot_error" in event_types


async def test_supervisor_rejects_double_start() -> None:
    redis = FakeRedis()
    router = FakeRouter()
    sup = Supervisor(EventPublisher(redis))
    await sup.start(
        bot_id="b4",
        user_id="u1",
        strategy=AlwaysBuy(),
        bars=InfiniteBars(_bar()),
        router=router,
    )
    with pytest.raises(RuntimeError, match="already running"):
        await sup.start(
            bot_id="b4",
            user_id="u1",
            strategy=AlwaysBuy(),
            bars=InfiniteBars(_bar()),
            router=router,
        )
    await sup.stop("b4")
