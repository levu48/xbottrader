"""Asyncio supervisor that owns running bots.

One process hosts many bots as asyncio tasks. The supervisor handles lifecycle
(start/stop/kill), surfaces errors as events, and guarantees no two workers
ever run the same bot (caller is responsible for the distributed lock).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ..events.publisher import Event, EventPublisher
from ..strategies.base import Bar, OrderIntent, Strategy, StrategyState

logger = logging.getLogger(__name__)


class BotState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERRORED = "errored"


class BarSource(Protocol):
    """Yields bars indefinitely. Backtest → finite source; live → exchange WS stream."""

    def __aiter__(self) -> AsyncIterator[Bar]:
        ...


class OrderRouter(Protocol):
    """Submits an order intent. Live → exchange client; paper → in-memory simulator."""

    async def submit(self, bot_id: str, intent: OrderIntent) -> None:
        ...


@dataclass(slots=True)
class BotHandle:
    bot_id: str
    user_id: str
    state: BotState = BotState.STARTING
    task: asyncio.Task[None] | None = None
    last_error: str | None = None
    strategy_state: StrategyState = field(default_factory=StrategyState)


class Supervisor:
    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher
        self._handles: dict[str, BotHandle] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        bot_id: str,
        user_id: str,
        strategy: Strategy,
        bars: BarSource,
        router: OrderRouter,
    ) -> None:
        async with self._lock:
            if bot_id in self._handles and self._handles[bot_id].state in (
                BotState.STARTING,
                BotState.RUNNING,
            ):
                raise RuntimeError(f"bot {bot_id} already running")
            handle = BotHandle(bot_id=bot_id, user_id=user_id)
            self._handles[bot_id] = handle
            handle.task = asyncio.create_task(
                self._run(handle, strategy, bars, router), name=f"bot:{bot_id}"
            )

    async def stop(self, bot_id: str) -> None:
        handle = self._handles.get(bot_id)
        if handle is None or handle.task is None:
            return
        handle.state = BotState.STOPPING
        handle.task.cancel()
        try:
            await handle.task
        except (asyncio.CancelledError, Exception):
            pass

    def get_state(self, bot_id: str) -> BotState | None:
        h = self._handles.get(bot_id)
        return h.state if h else None

    def running_bots(self) -> list[str]:
        return [bid for bid, h in self._handles.items() if h.state == BotState.RUNNING]

    async def _run(
        self,
        handle: BotHandle,
        strategy: Strategy,
        bars: BarSource,
        router: OrderRouter,
    ) -> None:
        handle.state = BotState.RUNNING
        await self._publisher.publish(
            Event("bot_started", handle.user_id, handle.bot_id, {})
        )
        try:
            async for bar in bars:
                if handle.state == BotState.STOPPING:
                    break
                intents = strategy.on_bar(bar, handle.strategy_state)
                for intent in intents:
                    await router.submit(handle.bot_id, intent)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            handle.state = BotState.ERRORED
            handle.last_error = f"{type(e).__name__}: {e}"
            logger.exception("bot %s crashed", handle.bot_id)
            await self._publisher.publish(
                Event(
                    "bot_error",
                    handle.user_id,
                    handle.bot_id,
                    {"error_class": type(e).__name__, "message": str(e), "fatal": True},
                )
            )
            return
        handle.state = BotState.STOPPED
        await self._publisher.publish(
            Event("bot_stopped", handle.user_id, handle.bot_id, {})
        )
