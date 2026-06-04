"""Thin async repositories. Sessions are injected — repos are stateless."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLogTradeRow, BotConfigRow, FillRow, OrderRow


class BotConfigRepo:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        user_id: str,
        name: str,
        exchange: str,
        mode: str,
        strategy: dict[str, Any],
        bot_id: str | None = None,
    ) -> BotConfigRow:
        row = BotConfigRow(
            user_id=user_id,
            name=name,
            exchange=exchange,
            mode=mode,
            strategy=strategy,
            status="draft",
            **({"id": bot_id} if bot_id else {}),
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get(
        session: AsyncSession, bot_id: str, *, user_id: str | None = None
    ) -> BotConfigRow | None:
        stmt = select(BotConfigRow).where(BotConfigRow.id == bot_id)
        if user_id is not None:
            stmt = stmt.where(BotConfigRow.user_id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_user(session: AsyncSession, user_id: str) -> list[BotConfigRow]:
        stmt = (
            select(BotConfigRow)
            .where(BotConfigRow.user_id == user_id)
            .order_by(BotConfigRow.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_status(session: AsyncSession, bot_id: str, status: str) -> None:
        row = await BotConfigRepo.get(session, bot_id)
        if row is not None:
            row.status = status


class OrderRepo:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        bot_id: str,
        user_id: str,
        exchange: str,
        exchange_order_id: str,
        symbol: str,
        side: str,
        type: str,
        quantity: Decimal,
        limit_price: Decimal | None,
        status: str,
        submitted_at: Any,
    ) -> OrderRow:
        row = OrderRow(
            bot_id=bot_id,
            user_id=user_id,
            exchange=exchange,
            exchange_order_id=exchange_order_id,
            symbol=symbol,
            side=side,
            type=type,
            quantity=quantity,
            limit_price=limit_price,
            status=status,
            submitted_at=submitted_at,
        )
        session.add(row)
        await session.flush()
        return row


class FillRepo:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        order_id: str,
        bot_id: str,
        user_id: str,
        exchange: str,
        exchange_fill_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        fee_currency: str,
        filled_at: Any,
    ) -> FillRow:
        row = FillRow(
            order_id=order_id,
            bot_id=bot_id,
            user_id=user_id,
            exchange=exchange,
            exchange_fill_id=exchange_fill_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            fee=fee,
            fee_currency=fee_currency,
            filled_at=filled_at,
        )
        session.add(row)
        await session.flush()
        return row


class AuditLogRepo:
    @staticmethod
    async def append(
        session: AsyncSession,
        *,
        user_id: str,
        bot_id: str | None,
        action: str,
        detail: dict[str, Any],
    ) -> None:
        session.add(
            AuditLogTradeRow(user_id=user_id, bot_id=bot_id, action=action, detail=detail)
        )
        await session.flush()
