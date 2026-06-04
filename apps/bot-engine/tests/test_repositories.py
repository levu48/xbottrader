from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import AuditLogRepo, BotConfigRepo, FillRepo, OrderRepo


async def test_bot_config_create_and_get(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        row = await BotConfigRepo.create(
            s,
            user_id="u1",
            name="my-dca",
            exchange="binance",
            mode="paper",
            strategy={"strategy_type": "dca", "symbol": "BTC/USDT", "quote_amount": "100"},
        )
        await s.commit()
        bot_id = row.id

    async with session_factory() as s:
        fetched = await BotConfigRepo.get(s, bot_id)
        assert fetched is not None
        assert fetched.user_id == "u1"
        assert fetched.strategy["strategy_type"] == "dca"
        assert fetched.status == "draft"


async def test_bot_config_get_scopes_to_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        row = await BotConfigRepo.create(
            s, user_id="u1", name="x", exchange="binance", mode="paper", strategy={}
        )
        await s.commit()
        bot_id = row.id

    async with session_factory() as s:
        assert await BotConfigRepo.get(s, bot_id, user_id="u1") is not None
        assert await BotConfigRepo.get(s, bot_id, user_id="u2") is None


async def test_bot_config_list_for_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        for i in range(3):
            await BotConfigRepo.create(
                s, user_id="u1", name=f"b{i}", exchange="binance", mode="paper", strategy={}
            )
        await BotConfigRepo.create(
            s, user_id="u2", name="other", exchange="binance", mode="paper", strategy={}
        )
        await s.commit()

    async with session_factory() as s:
        u1_bots = await BotConfigRepo.list_for_user(s, "u1")
        u2_bots = await BotConfigRepo.list_for_user(s, "u2")
        assert len(u1_bots) == 3
        assert len(u2_bots) == 1
        assert {b.name for b in u1_bots} == {"b0", "b1", "b2"}


async def test_order_and_fill_persist_and_link(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        bot = await BotConfigRepo.create(
            s, user_id="u1", name="x", exchange="binance", mode="paper", strategy={}
        )
        order = await OrderRepo.create(
            s,
            bot_id=bot.id,
            user_id="u1",
            exchange="binance",
            exchange_order_id="xo-1",
            symbol="BTC/USDT",
            side="buy",
            type="market",
            quantity=Decimal("0.1"),
            limit_price=None,
            status="filled",
            submitted_at=datetime.now(UTC),
        )
        fill = await FillRepo.create(
            s,
            order_id=order.id,
            bot_id=bot.id,
            user_id="u1",
            exchange="binance",
            exchange_fill_id="xf-1",
            symbol="BTC/USDT",
            side="buy",
            quantity=Decimal("0.1"),
            price=Decimal("50000"),
            fee=Decimal("5"),
            fee_currency="USDT",
            filled_at=datetime.now(UTC),
        )
        await s.commit()

        assert order.id is not None
        assert fill.order_id == order.id


async def test_audit_log_append(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as s:
        await AuditLogRepo.append(
            s,
            user_id="u1",
            bot_id="b1",
            action="bot.start",
            detail={"mode": "paper"},
        )
        await s.commit()

    from sqlalchemy import select

    from app.db.models import AuditLogTradeRow

    async with session_factory() as s:
        result = await s.execute(select(AuditLogTradeRow))
        rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].action == "bot.start"
        assert rows[0].detail["mode"] == "paper"
