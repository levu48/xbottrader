"""Bridges Supervisor's OrderRouter protocol to an ExchangeAdapter.

Each running bot owns one router, which owns one exchange adapter, which owns
the connection (and decrypted API key, for live) for that bot's user. Routing
is per-bot so a buggy adapter for one user cannot affect another.

Every order goes through ``ExchangeOrderRouter`` and writes:
    1. An ``orders`` row (one per place_order call).
    2. ``fills`` rows for any immediate fills.
    3. An ``audit_log_trade`` row for each significant action.
Then publishes the matching Redis Stream events. Persistence happens BEFORE
publish so a downstream consumer can never see an event without a paper trail.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.repositories import AuditLogRepo, FillRepo, OrderRepo
from ..events.publisher import Event, EventPublisher
from ..exchanges.base import ExchangeAdapter, FillEvent, SubmittedOrder
from ..strategies.base import OrderIntent


class ExchangeOrderRouter:
    """Implements the supervisor's :class:`OrderRouter` Protocol."""

    def __init__(
        self,
        *,
        user_id: str,
        adapter: ExchangeAdapter,
        publisher: EventPublisher,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._user_id = user_id
        self._adapter = adapter
        self._publisher = publisher
        self._sessions = session_factory

    async def submit(self, bot_id: str, intent: OrderIntent) -> None:
        result = await self._adapter.place_order(intent)

        order_row_id = await self._persist_order(bot_id, result.order, result.immediate_fills)

        await self._publisher.publish(
            Event(
                event_type="order_submitted",
                user_id=self._user_id,
                bot_id=bot_id,
                payload=_serialize_order(result.order, order_row_id=order_row_id),
            )
        )
        for fill in result.immediate_fills:
            await self._publisher.publish(
                Event(
                    event_type="fill",
                    user_id=self._user_id,
                    bot_id=bot_id,
                    payload=_serialize_fill(fill),
                )
            )

    async def deliver_fills(self, bot_id: str, fills: Iterable[FillEvent]) -> None:
        """Publish fills that arrived out-of-band (limit fills, ccxt WS stream).

        These don't go through ``place_order`` so the parent order's DB row
        already exists; we look it up by ``exchange_order_id`` and link.
        """
        async with self._sessions() as session:
            for fill in fills:
                # The order row was written when we submitted; we can join via
                # exchange_order_id when we need it. For now we still want a
                # fills row even if we can't resolve the parent yet.
                await FillRepo.create(
                    session,
                    order_id=fill.exchange_order_id,  # best-effort; resolved later
                    bot_id=bot_id,
                    user_id=self._user_id,
                    exchange=self._adapter.venue,
                    exchange_fill_id=fill.exchange_fill_id,
                    symbol=fill.symbol,
                    side=fill.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    fee=fill.fee,
                    fee_currency=fill.fee_currency,
                    filled_at=fill.filled_at,
                )
            await session.commit()

        for fill in fills:
            await self._publisher.publish(
                Event(
                    event_type="fill",
                    user_id=self._user_id,
                    bot_id=bot_id,
                    payload=_serialize_fill(fill),
                )
            )

    async def _persist_order(
        self,
        bot_id: str,
        order: SubmittedOrder,
        fills: tuple[FillEvent, ...],
    ) -> str:
        async with self._sessions() as session:
            order_row = await OrderRepo.create(
                session,
                bot_id=bot_id,
                user_id=self._user_id,
                exchange=self._adapter.venue,
                exchange_order_id=order.exchange_order_id,
                symbol=order.intent.symbol,
                side=order.intent.side,
                type=order.intent.type,
                quantity=order.intent.quantity,
                limit_price=order.intent.limit_price,
                status=order.status,
                submitted_at=order.submitted_at,
            )
            for fill in fills:
                await FillRepo.create(
                    session,
                    order_id=order_row.id,
                    bot_id=bot_id,
                    user_id=self._user_id,
                    exchange=self._adapter.venue,
                    exchange_fill_id=fill.exchange_fill_id,
                    symbol=fill.symbol,
                    side=fill.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    fee=fill.fee,
                    fee_currency=fill.fee_currency,
                    filled_at=fill.filled_at,
                )
            await AuditLogRepo.append(
                session,
                user_id=self._user_id,
                bot_id=bot_id,
                action="order.submitted",
                detail={
                    "exchange_order_id": order.exchange_order_id,
                    "symbol": order.intent.symbol,
                    "side": order.intent.side,
                    "type": order.intent.type,
                    "quantity": str(order.intent.quantity),
                    "status": order.status,
                    "fill_count": len(fills),
                },
            )
            await session.commit()
            return order_row.id


def _serialize_order(order: SubmittedOrder, *, order_row_id: str) -> dict[str, object]:
    return {
        "order_id": order_row_id,
        "exchange_order_id": order.exchange_order_id,
        "symbol": order.intent.symbol,
        "side": order.intent.side,
        "type": order.intent.type,
        "quantity": str(order.intent.quantity),
        "limit_price": str(order.intent.limit_price) if order.intent.limit_price is not None else None,
        "status": order.status,
        "submitted_at": order.submitted_at.isoformat(),
    }


def _serialize_fill(fill: FillEvent) -> dict[str, object]:
    return {
        "exchange_fill_id": fill.exchange_fill_id,
        "exchange_order_id": fill.exchange_order_id,
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "fee": str(fill.fee),
        "fee_currency": fill.fee_currency,
        "filled_at": fill.filled_at.isoformat(),
    }
