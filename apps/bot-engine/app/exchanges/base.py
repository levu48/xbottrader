"""Exchange adapter interface.

The Supervisor and OrderRouter target this Protocol; concrete adapters wrap
ccxt (live) or simulate fills locally (paper). New venues plug in by adding
adapters — strategies and the supervisor remain unchanged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from ..strategies.base import OrderIntent


@dataclass(frozen=True, slots=True)
class SubmittedOrder:
    """Result of place_order — the order is acknowledged by the venue."""

    exchange_order_id: str
    intent: OrderIntent
    submitted_at: datetime
    status: str  # "submitted" | "filled" | "partially_filled" | "rejected"


@dataclass(frozen=True, slots=True)
class FillEvent:
    """A trade has executed against an order. Multiple fills per order are allowed."""

    exchange_fill_id: str
    exchange_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class PlacementResult:
    """Returned by `place_order`: the submission plus any immediate fills.

    Market orders typically come back with one immediate fill. Limit orders
    usually return zero fills and stream them later via `fill_stream`.
    """

    order: SubmittedOrder
    immediate_fills: tuple[FillEvent, ...] = field(default_factory=tuple)


class ExchangeAdapter(Protocol):
    @property
    def venue(self) -> str:
        ...

    async def place_order(self, intent: OrderIntent) -> PlacementResult:
        ...

    async def cancel_order(self, exchange_order_id: str) -> None:
        ...

    async def close(self) -> None:
        ...


def new_order_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)
