"""Bridges Supervisor's OrderRouter protocol to an ExchangeAdapter.

Each running bot owns one router, which owns one exchange adapter, which owns
the connection (and decrypted API key, for live) for that bot's user. Routing
is per-bot so a buggy adapter for one user cannot affect another.
"""

from __future__ import annotations

from typing import Iterable

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
    ) -> None:
        self._user_id = user_id
        self._adapter = adapter
        self._publisher = publisher

    async def submit(self, bot_id: str, intent: OrderIntent) -> None:
        result = await self._adapter.place_order(intent)
        await self._publisher.publish(
            Event(
                event_type="order_submitted",
                user_id=self._user_id,
                bot_id=bot_id,
                payload=_serialize_order(result.order),
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
        """Publish fills that arrived out-of-band (e.g., paper limits crossing,
        ccxt WebSocket fill stream).
        """
        for fill in fills:
            await self._publisher.publish(
                Event(
                    event_type="fill",
                    user_id=self._user_id,
                    bot_id=bot_id,
                    payload=_serialize_fill(fill),
                )
            )


def _serialize_order(order: SubmittedOrder) -> dict[str, object]:
    return {
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
