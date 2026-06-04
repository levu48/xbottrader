"""Kill switch + max-loss circuit breaker — the safety rails.

Covers the Supervisor's ``kill`` / ``kill_all`` and the circuit breaker that
auto-pauses a bot when mark-to-market PnL hits the loss cap, plus the router's
``cancel_open`` that unwinds resting orders on either path.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import OrderRow
from app.events.publisher import EventPublisher
from app.exchanges.paper import PaperConfig, PaperExchangeAdapter
from app.runtime.router import ExchangeOrderRouter
from app.runtime.supervisor import BotState, PnLLedger, Supervisor
from app.strategies.base import Bar, OrderIntent, Strategy, StrategyState


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


class FakeRouter:
    """Minimal OrderRouter: records submits, executes no fills, no cancel_open."""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, OrderIntent]] = []

    async def submit(self, bot_id: str, intent: OrderIntent) -> list:
        self.submitted.append((bot_id, intent))
        return []


class InfiniteBars:
    def __init__(self, bar: Bar) -> None:
        self._bar = bar

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        while True:
            yield self._bar
            await asyncio.sleep(0.001)


class MarkingBars:
    """Yields a finite list of bars, pushing each close to the adapter's mark."""

    def __init__(self, bars: list[Bar], adapter: PaperExchangeAdapter) -> None:
        self._bars = bars
        self._adapter = adapter
        self.consumed = 0

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        for b in self._bars:
            self._adapter.update_mark(b.symbol, b.close)
            self.consumed += 1
            yield b
            await asyncio.sleep(0)


class BuyMarketOnce(Strategy):
    """Buys a fixed quantity at market on the first bar, then nothing."""

    def __init__(self, qty: Decimal = Decimal("1")) -> None:
        self._qty = qty

    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        if state.last_action_ts_ms is not None:
            return []
        state.last_action_ts_ms = bar.ts_ms
        return [OrderIntent(symbol=bar.symbol, side="buy", type="market", quantity=self._qty)]


class BuyLimitOnce(Strategy):
    """Places one resting buy-limit on the first bar, then nothing."""

    def __init__(self, limit_price: Decimal) -> None:
        self._limit = limit_price

    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        if state.last_action_ts_ms is not None:
            return []
        state.last_action_ts_ms = bar.ts_ms
        return [
            OrderIntent(
                symbol=bar.symbol,
                side="buy",
                type="limit",
                quantity=Decimal("1"),
                limit_price=self._limit,
            )
        ]


def _bar(ts: int, close: str, sym: str = "BTC/USDT") -> Bar:
    p = Decimal(close)
    return Bar(ts_ms=ts, symbol=sym, open=p, high=p, low=p, close=p, volume=Decimal("0"))


# --------------------------------------------------------------------------- #
# PnL ledger (unit)
# --------------------------------------------------------------------------- #


def test_pnl_ledger_marks_to_market() -> None:
    led = PnLLedger()
    assert led.pnl(Decimal("50000")) == Decimal("0")  # flat → no PnL

    led.apply(
        _fill(side="buy", qty=Decimal("1"), price=Decimal("50000"), fee=Decimal("0"))
    )
    # At the buy price, PnL is just the (zero) fee; drop the mark and it goes red.
    assert led.pnl(Decimal("50000")) == Decimal("0")
    assert led.pnl(Decimal("49900")) == Decimal("-100")

    led.apply(
        _fill(side="sell", qty=Decimal("1"), price=Decimal("49900"), fee=Decimal("0"))
    )
    assert led.position == Decimal("0")
    assert led.pnl(Decimal("1")) == Decimal("-100")  # realized loss locked in


def _fill(*, side: str, qty: Decimal, price: Decimal, fee: Decimal):
    from app.exchanges.base import FillEvent, new_order_id, utcnow

    return FillEvent(
        exchange_fill_id=new_order_id(),
        exchange_order_id=new_order_id(),
        symbol="BTC/USDT",
        side=side,
        quantity=qty,
        price=price,
        fee=fee,
        fee_currency="USDT",
        filled_at=utcnow(),
    )


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #


async def test_circuit_breaker_pauses_bot_on_max_loss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = FakeRedis()
    publisher = EventPublisher(redis)
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    router = ExchangeOrderRouter(
        user_id="u1", adapter=adapter, publisher=publisher, session_factory=session_factory
    )
    sup = Supervisor(publisher)

    # Buy 1 @ 50000, then the mark slides down. Cap = 100 quote → trips at 49900.
    bars = MarkingBars(
        [_bar(0, "50000"), _bar(1, "49900"), _bar(2, "49800"), _bar(3, "49700")],
        adapter,
    )
    await sup.start(
        bot_id="b1",
        user_id="u1",
        strategy=BuyMarketOnce(),
        bars=bars,
        router=router,
        max_loss_quote=Decimal("100"),
    )
    await sup._handles["b1"].task  # type: ignore[attr-defined]

    assert sup.get_state("b1") == BotState.PAUSED
    # Tripped on bar index 1 → only the first two bars were consumed.
    assert bars.consumed == 2

    types = [f["event_type"] for _, f in redis.entries]
    assert "bot_circuit_tripped" in types
    assert "bot_stopped" not in types  # paused, not a clean stop
    import json

    trip = next(
        json.loads(f["payload"]) for _, f in redis.entries if f["event_type"] == "bot_circuit_tripped"
    )
    assert trip["reason"] == "max_loss"
    assert Decimal(trip["pnl_quote"]) <= Decimal("-100")


async def test_circuit_breaker_inactive_without_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = FakeRedis()
    publisher = EventPublisher(redis)
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    router = ExchangeOrderRouter(
        user_id="u1", adapter=adapter, publisher=publisher, session_factory=session_factory
    )
    sup = Supervisor(publisher)

    bars = MarkingBars([_bar(0, "50000"), _bar(1, "1")], adapter)  # 98% drawdown
    await sup.start(
        bot_id="b1",
        user_id="u1",
        strategy=BuyMarketOnce(),
        bars=bars,
        router=router,
        max_loss_quote=None,  # breaker disabled
    )
    await sup._handles["b1"].task  # type: ignore[attr-defined]

    assert sup.get_state("b1") == BotState.STOPPED  # ran to completion, never paused
    types = [f["event_type"] for _, f in redis.entries]
    assert "bot_circuit_tripped" not in types
    assert types[-1] == "bot_stopped"


# --------------------------------------------------------------------------- #
# Kill switch
# --------------------------------------------------------------------------- #


async def test_kill_force_stops_and_emits_event() -> None:
    redis = FakeRedis()
    router = FakeRouter()
    sup = Supervisor(EventPublisher(redis))

    await sup.start(
        bot_id="b1",
        user_id="u1",
        strategy=BuyMarketOnce(),
        bars=InfiniteBars(_bar(0, "50000")),
        router=router,
    )
    await asyncio.sleep(0.01)
    killed = await sup.kill("b1")

    assert killed is True
    assert sup.get_state("b1") == BotState.KILLED
    types = [f["event_type"] for _, f in redis.entries]
    assert types[-1] == "bot_killed"


async def test_kill_unknown_bot_returns_false() -> None:
    sup = Supervisor(EventPublisher(FakeRedis()))
    assert await sup.kill("nope") is False


async def test_kill_cancels_resting_orders(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = FakeRedis()
    publisher = EventPublisher(redis)
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))
    router = ExchangeOrderRouter(
        user_id="u1", adapter=adapter, publisher=publisher, session_factory=session_factory
    )
    sup = Supervisor(publisher)

    # A buy-limit far below the mark rests forever; killing must cancel it.
    await sup.start(
        bot_id="b1",
        user_id="u1",
        strategy=BuyLimitOnce(Decimal("49000")),
        bars=InfiniteBars(_bar(0, "50000")),
        router=router,
    )
    await asyncio.sleep(0.02)
    assert router._open_orders  # an order is resting  # type: ignore[attr-defined]

    await sup.kill("b1")

    assert sup.get_state("b1") == BotState.KILLED
    assert not router._open_orders  # type: ignore[attr-defined]
    types = [f["event_type"] for _, f in redis.entries]
    assert "order_cancelled" in types
    assert types[-1] == "bot_killed"

    # DB order row was flipped to cancelled.
    async with session_factory() as s:
        orders = list((await s.execute(select(OrderRow))).scalars().all())
        assert len(orders) == 1
        assert orders[0].status == "cancelled"


async def test_kill_all_scopes_to_user() -> None:
    redis = FakeRedis()
    sup = Supervisor(EventPublisher(redis))
    for bid, uid in [("a", "u1"), ("b", "u1"), ("c", "u2")]:
        await sup.start(
            bot_id=bid,
            user_id=uid,
            strategy=BuyMarketOnce(),
            bars=InfiniteBars(_bar(0, "50000")),
            router=FakeRouter(),
        )
    await asyncio.sleep(0.01)

    killed = await sup.kill_all(user_id="u1")

    assert sorted(killed) == ["a", "b"]
    assert sup.get_state("a") == BotState.KILLED
    assert sup.get_state("b") == BotState.KILLED
    assert sup.get_state("c") == BotState.RUNNING  # u2 untouched

    await sup.kill("c")  # cleanup
