from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditLogTradeRow, FillRow, OrderRow
from app.db.repositories import BotConfigRepo
from app.events.publisher import EventPublisher
from app.exchanges.paper import PaperConfig, PaperExchangeAdapter
from app.runtime.router import ExchangeOrderRouter
from app.strategies.base import OrderIntent


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


async def _seed_bot(session_factory: async_sessionmaker[AsyncSession]) -> str:
    async with session_factory() as s:
        row = await BotConfigRepo.create(
            s, user_id="u1", name="t", exchange="paper", mode="paper", strategy={}
        )
        await s.commit()
        return row.id


async def test_router_persists_order_and_fill_then_publishes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot_id = await _seed_bot(session_factory)
    redis = FakeRedis()
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=10))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    router = ExchangeOrderRouter(
        user_id="u1",
        adapter=adapter,
        publisher=EventPublisher(redis),
        session_factory=session_factory,
    )

    await router.submit(
        bot_id,
        OrderIntent(symbol="BTC/USDT", side="buy", type="market", quantity=Decimal("0.1")),
    )

    # Events: order_submitted, fill
    types = [f["event_type"] for _, f in redis.entries]
    assert types == ["order_submitted", "fill"]

    # DB: 1 order, 1 fill, 1 audit row
    async with session_factory() as s:
        orders = list((await s.execute(select(OrderRow))).scalars().all())
        fills = list((await s.execute(select(FillRow))).scalars().all())
        audits = list((await s.execute(select(AuditLogTradeRow))).scalars().all())
        assert len(orders) == 1
        assert orders[0].status == "filled"
        assert orders[0].symbol == "BTC/USDT"
        assert len(fills) == 1
        assert fills[0].order_id == orders[0].id
        assert fills[0].price == Decimal("50000")
        assert len(audits) == 1
        assert audits[0].action == "order.submitted"


async def test_router_persists_submitted_order_for_resting_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot_id = await _seed_bot(session_factory)
    redis = FakeRedis()
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    router = ExchangeOrderRouter(
        user_id="u1",
        adapter=adapter,
        publisher=EventPublisher(redis),
        session_factory=session_factory,
    )

    await router.submit(
        bot_id,
        OrderIntent(
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            quantity=Decimal("1"),
            limit_price=Decimal("49000"),
        ),
    )

    types = [f["event_type"] for _, f in redis.entries]
    assert types == ["order_submitted"]

    async with session_factory() as s:
        orders = list((await s.execute(select(OrderRow))).scalars().all())
        assert len(orders) == 1
        assert orders[0].status == "submitted"
        fills = list((await s.execute(select(FillRow))).scalars().all())
        assert fills == []


async def test_router_publishes_event_payload_with_order_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot_id = await _seed_bot(session_factory)
    redis = FakeRedis()
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    router = ExchangeOrderRouter(
        user_id="u1",
        adapter=adapter,
        publisher=EventPublisher(redis),
        session_factory=session_factory,
    )
    await router.submit(
        bot_id,
        OrderIntent(symbol="BTC/USDT", side="buy", type="market", quantity=Decimal("0.1")),
    )

    order_event = next(
        json.loads(f["payload"]) for _, f in redis.entries if f["event_type"] == "order_submitted"
    )
    assert "order_id" in order_event
    async with session_factory() as s:
        orders = list((await s.execute(select(OrderRow))).scalars().all())
        assert order_event["order_id"] == orders[0].id
