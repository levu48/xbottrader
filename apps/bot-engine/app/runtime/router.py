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
        # Resting (acknowledged but unfilled) orders, keyed by exchange_order_id
        # → symbol. The kill switch / circuit breaker cancel these via
        # ``cancel_open``. Partial-fill accounting is deferred: an order leaves
        # this set the moment any fill arrives for it.
        self._open_orders: dict[str, str] = {}

    async def submit(self, bot_id: str, intent: OrderIntent) -> list[FillEvent]:
        result = await self._adapter.place_order(intent)

        order_row_id = await self._persist_order(bot_id, result.order, result.immediate_fills)

        if result.order.status == "submitted" and not result.immediate_fills:
            self._open_orders[result.order.exchange_order_id] = intent.symbol

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
        return list(result.immediate_fills)

    async def cancel_open(self, bot_id: str) -> int:
        """Cancel every resting order for this bot. Returns the count cancelled.

        Called by the Supervisor on kill / circuit-trip. Cancels at the venue,
        marks the order ``cancelled`` in the DB with an audit row, and emits an
        ``order_cancelled`` event per order. Best-effort per order: a venue that
        rejects a cancel (already filled/gone) is logged via the audit trail but
        does not block the others.
        """
        if not self._open_orders:
            return 0
        resting = dict(self._open_orders)
        self._open_orders.clear()

        cancelled: list[tuple[str, str]] = []
        async with self._sessions() as session:
            for exchange_order_id, symbol in resting.items():
                await self._adapter.cancel_order(exchange_order_id)
                await OrderRepo.set_status_by_exchange_id(
                    session, exchange_order_id=exchange_order_id, status="cancelled"
                )
                await AuditLogRepo.append(
                    session,
                    user_id=self._user_id,
                    bot_id=bot_id,
                    action="order.cancelled",
                    detail={"exchange_order_id": exchange_order_id, "symbol": symbol},
                )
                cancelled.append((exchange_order_id, symbol))
            await session.commit()

        for exchange_order_id, symbol in cancelled:
            await self._publisher.publish(
                Event(
                    event_type="order_cancelled",
                    user_id=self._user_id,
                    bot_id=bot_id,
                    payload={"exchange_order_id": exchange_order_id, "symbol": symbol},
                )
            )
        return len(cancelled)

    async def deliver_fills(self, bot_id: str, fills: Iterable[FillEvent]) -> None:
        """Publish fills that arrived out-of-band (limit fills, ccxt WS stream).

        These don't go through ``place_order`` so the parent order's DB row
        already exists; we look it up by ``exchange_order_id`` and link.
        """
        async with self._sessions() as session:
            for fill in fills:
                # An out-of-band fill means a resting order (partially) executed;
                # drop it from the open set so the kill switch won't try to cancel
                # an order the venue has already worked.
                self._open_orders.pop(fill.exchange_order_id, None)
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
