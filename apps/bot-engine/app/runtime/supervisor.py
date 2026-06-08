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
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

from xbt_core.market.session import MarketSession

from ..events.publisher import Event, EventPublisher
from ..exchanges.base import FillEvent
from ..strategies.base import Bar, OrderIntent, Strategy, StrategyState

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


class BotState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERRORED = "errored"
    # A risk limit tripped — the bot stopped trading but did not error or exit
    # cleanly. It will not resume without an explicit restart.
    PAUSED = "paused"
    # Force-stopped via the kill switch.
    KILLED = "killed"
    # Alive but not trading because the venue's market is closed (equities).
    # Resumes to RUNNING on the first bar after the market reopens.
    IDLE = "idle"


class BarSource(Protocol):
    """Yields bars indefinitely. Backtest → finite source; live → exchange WS stream."""

    def __aiter__(self) -> AsyncIterator[Bar]:
        ...


class Companion(Protocol):
    """A side task that runs alongside a bot, sharing its StrategyState.

    Used by the ai_signal strategy: the companion consults the model on an
    interval and writes decisions into ``state.custom`` for ``on_bar`` to act on.
    It loops until cancelled; the Supervisor cancels it when the bot's bar loop
    ends (stop, kill, error, or stream exhaustion).
    """

    async def run(self, state: StrategyState) -> None:
        ...


class OrderRouter(Protocol):
    """Submits an order intent. Live → exchange client; paper → in-memory simulator.

    ``submit`` returns the fills that executed immediately (empty for a resting
    limit order). The supervisor feeds these into the per-bot PnL ledger that
    drives the circuit breaker. A router MAY also implement
    ``async def cancel_open(self, bot_id: str) -> int`` to cancel resting orders
    on kill / circuit-trip; the supervisor calls it defensively if present.
    """

    async def submit(self, bot_id: str, intent: OrderIntent) -> list[FillEvent]:
        ...


@dataclass(slots=True)
class PnLLedger:
    """Mark-to-market PnL from the fills a bot has executed.

    Single-quote-currency, average-cost accounting: every buy spends cash and
    grows the position; every sell returns cash and shrinks it. PnL at a given
    mark is ``position * mark - net_cash_out`` — zero before the first fill, and
    ``-fees`` right after a fill at the fill price. Good enough to enforce a loss
    cap; full per-symbol position accounting lives in the (deferred) positions
    table. Single-symbol strategies only — the MVP set qualifies.
    """

    position: Decimal = Decimal("0")
    cash_out: Decimal = Decimal("0")

    def apply(self, fill: FillEvent) -> None:
        notional = fill.quantity * fill.price
        if fill.side == "buy":
            self.position += fill.quantity
            self.cash_out += notional + fill.fee
        else:
            self.position -= fill.quantity
            self.cash_out -= notional - fill.fee

    def pnl(self, mark: Decimal) -> Decimal:
        return self.position * mark - self.cash_out


@dataclass(slots=True)
class BotHandle:
    bot_id: str
    user_id: str
    state: BotState = BotState.STARTING
    task: asyncio.Task[None] | None = None
    last_error: str | None = None
    strategy_state: StrategyState = field(default_factory=StrategyState)
    router: OrderRouter | None = None
    max_loss_quote: Decimal | None = None
    ledger: PnLLedger = field(default_factory=PnLLedger)
    last_mark: Decimal | None = None
    # Wall-clock-aligned timestamp (epoch ms) of the bar that set last_mark.
    last_mark_ts_ms: int | None = None
    # Market-hours session. None = always-open / no gating (crypto path).
    session: MarketSession | None = None
    # If set, the circuit breaker ignores a mark older than this (defensive
    # belt-and-suspenders against a stale bar arriving across a market gap).
    max_mark_age_ms: int | None = None


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
        max_loss_quote: Decimal | None = None,
        session: MarketSession | None = None,
        max_mark_age_ms: int | None = None,
        companion: Companion | None = None,
    ) -> None:
        async with self._lock:
            if bot_id in self._handles and self._handles[bot_id].state in (
                BotState.STARTING,
                BotState.RUNNING,
                BotState.IDLE,
            ):
                raise RuntimeError(f"bot {bot_id} already running")
            handle = BotHandle(
                bot_id=bot_id,
                user_id=user_id,
                router=router,
                max_loss_quote=max_loss_quote,
                session=session,
                max_mark_age_ms=max_mark_age_ms,
            )
            self._handles[bot_id] = handle
            handle.task = asyncio.create_task(
                self._run(handle, strategy, bars, router, companion), name=f"bot:{bot_id}"
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

    async def kill(self, bot_id: str) -> bool:
        """Force-stop a bot NOW and cancel its open orders. Returns False if unknown.

        Unlike ``stop`` (which lets the current bar finish), kill cancels the task
        immediately, best-effort cancels resting orders via the router, and emits
        ``bot_killed``. Idempotent: killing an already-terminal bot is a no-op that
        still returns True.
        """
        handle = self._handles.get(bot_id)
        if handle is None:
            return False
        handle.state = BotState.KILLED
        if handle.task is not None:
            handle.task.cancel()
            try:
                await handle.task
            except (asyncio.CancelledError, Exception):
                pass
        await self._cancel_open(handle)
        await self._publisher.publish(
            Event("bot_killed", handle.user_id, handle.bot_id, {})
        )
        return True

    async def kill_all(self, *, user_id: str | None = None) -> list[str]:
        """Kill every live bot, optionally scoped to one user. Returns the ids killed."""
        targets = [
            bid
            for bid, h in self._handles.items()
            if h.state not in (BotState.STOPPED, BotState.ERRORED, BotState.KILLED)
            and (user_id is None or h.user_id == user_id)
        ]
        for bid in targets:
            await self.kill(bid)
        return targets

    async def _cancel_open(self, handle: BotHandle) -> None:
        """Best-effort cancel of the bot's resting orders. Never raises."""
        cancel_open = getattr(handle.router, "cancel_open", None)
        if cancel_open is None:
            return
        try:
            await cancel_open(handle.bot_id)
        except Exception:
            logger.exception("cancel_open failed for bot %s", handle.bot_id)

    async def _tripped(self, handle: BotHandle) -> bool:
        """Evaluate the max-loss circuit breaker against the latest mark.

        Returns True (and transitions the bot to PAUSED, emitting
        ``bot_circuit_tripped``) once mark-to-market PnL has fallen to the loss
        cap. Caller is responsible for unwinding (cancel open orders) and exiting.
        """
        cap = handle.max_loss_quote
        if cap is None or handle.last_mark is None:
            return False
        # Never act on a mark we can't trust. For an equity bot the mark goes
        # stale across the overnight/weekend gap, so: don't trip while the market
        # is closed, and (defensively) don't trip on a mark older than the
        # configured age. Both checks are no-ops on the crypto path (session is
        # None, marks are fresh), keeping crypto behavior identical.
        if handle.session is not None:
            now_ms = _now_ms()
            if not handle.session.is_open(now_ms):
                return False
            if (
                handle.max_mark_age_ms is not None
                and handle.last_mark_ts_ms is not None
                and now_ms - handle.last_mark_ts_ms > handle.max_mark_age_ms
            ):
                return False
        pnl = handle.ledger.pnl(handle.last_mark)
        if pnl > -cap:
            return False
        handle.state = BotState.PAUSED
        await self._publisher.publish(
            Event(
                "bot_circuit_tripped",
                handle.user_id,
                handle.bot_id,
                {
                    "reason": "max_loss",
                    "pnl_quote": str(pnl),
                    "max_loss_quote": str(cap),
                    "mark": str(handle.last_mark),
                    "position": str(handle.ledger.position),
                },
            )
        )
        logger.warning(
            "bot %s circuit-tripped: pnl=%s <= -%s", handle.bot_id, pnl, cap
        )
        return True

    def get_state(self, bot_id: str) -> BotState | None:
        h = self._handles.get(bot_id)
        return h.state if h else None

    def running_bots(self) -> list[str]:
        # "Live" = actively trading (RUNNING) or merely waiting for the market to
        # reopen (IDLE). Both need to be stopped on shutdown.
        return [
            bid
            for bid, h in self._handles.items()
            if h.state in (BotState.RUNNING, BotState.IDLE)
        ]

    async def _run(
        self,
        handle: BotHandle,
        strategy: Strategy,
        bars: BarSource,
        router: OrderRouter,
        companion: Companion | None = None,
    ) -> None:
        handle.state = BotState.RUNNING
        await self._publisher.publish(
            Event("bot_started", handle.user_id, handle.bot_id, {})
        )
        # The companion (ai_signal) runs as a sibling task, sharing this bot's
        # StrategyState, and is torn down with the bar loop in the finally below.
        companion_task: asyncio.Task[None] | None = None
        if companion is not None:
            companion_task = asyncio.create_task(
                companion.run(handle.strategy_state), name=f"companion:{handle.bot_id}"
            )
        try:
            async for bar in bars:
                if handle.state == BotState.STOPPING:
                    break
                # Equity bots: if a bar arrives while the market is closed (stale
                # / pre-market data), don't trade it — go IDLE and wait. No-op for
                # crypto (session None → always open).
                if handle.session is not None and not handle.session.is_open(_now_ms()):
                    if handle.state != BotState.IDLE:
                        handle.state = BotState.IDLE
                        await self._publisher.publish(
                            Event("bot_idle", handle.user_id, handle.bot_id,
                                  {"reason": "market_closed"})
                        )
                    continue
                if handle.state == BotState.IDLE:
                    handle.state = BotState.RUNNING
                    await self._publisher.publish(
                        Event("bot_resumed", handle.user_id, handle.bot_id,
                              {"reason": "market_open"})
                    )
                handle.last_mark = bar.close
                handle.last_mark_ts_ms = bar.ts_ms
                intents = strategy.on_bar(bar, handle.strategy_state)
                for intent in intents:
                    fills = await router.submit(handle.bot_id, intent) or []
                    for fill in fills:
                        handle.ledger.apply(fill)
                if await self._tripped(handle):
                    await self._cancel_open(handle)
                    return
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
        finally:
            # Tear down the companion on every exit path (stop, kill, error,
            # circuit trip, stream end) so no orphan task keeps consulting.
            if companion_task is not None:
                companion_task.cancel()
                try:
                    await companion_task
                except (asyncio.CancelledError, Exception):
                    pass
        handle.state = BotState.STOPPED
        await self._publisher.publish(
            Event("bot_stopped", handle.user_id, handle.bot_id, {})
        )
