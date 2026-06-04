"""Paper-trading adapter — simulates fills against a mark-price source.

Required by every new user before live keys (per the MVP plan). Market orders
fill instantly at the current mark price, adjusted by configurable slippage and
fee. Limit orders are tracked in-memory and fill when the mark crosses the
limit on the next price update.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from ..strategies.base import OrderIntent
from .base import (
    ExchangeAdapter,
    FillEvent,
    PlacementResult,
    SubmittedOrder,
    new_order_id,
    utcnow,
)

MarkPriceFn = Callable[[str], Decimal | None]


@dataclass(slots=True)
class _RestingOrder:
    exchange_order_id: str
    intent: OrderIntent


@dataclass(slots=True)
class PaperConfig:
    slippage_bps: int = 5  # 0.05% — 5 basis points
    fee_bps: int = 10  # 0.10% — typical CEX taker fee
    fee_currency: str = "USDT"


class PaperExchangeAdapter(ExchangeAdapter):
    """In-memory paper exchange.

    ``mark_price_fn`` returns the current price for a symbol. Strategies' bar
    handler supplies the mark via :meth:`update_mark` before placing orders,
    OR the caller maintains an external price feed. Either way, no network.
    """

    def __init__(
        self,
        *,
        venue: str = "paper",
        config: PaperConfig | None = None,
        mark_price_fn: MarkPriceFn | None = None,
    ) -> None:
        self._venue = venue
        self._cfg = config or PaperConfig()
        self._mark_fn = mark_price_fn or self._mark_from_table
        self._marks: dict[str, Decimal] = {}
        self._resting: dict[str, _RestingOrder] = {}
        self._lock = asyncio.Lock()

    @property
    def venue(self) -> str:
        return self._venue

    def update_mark(self, symbol: str, price: Decimal) -> list[FillEvent]:
        """Update the mark and trigger any limit orders that should fill."""
        self._marks[symbol] = price
        return self._match_resting(symbol, price)

    async def place_order(self, intent: OrderIntent) -> PlacementResult:
        async with self._lock:
            order_id = new_order_id()
            if intent.type == "market":
                mark = self._mark_fn(intent.symbol)
                if mark is None or mark <= 0:
                    rejected = SubmittedOrder(
                        exchange_order_id=order_id,
                        intent=intent,
                        submitted_at=utcnow(),
                        status="rejected",
                    )
                    return PlacementResult(order=rejected)
                fill_price = self._apply_slippage(mark, intent.side)
                fill = self._build_fill(order_id, intent, fill_price, intent.quantity)
                return PlacementResult(
                    order=SubmittedOrder(
                        exchange_order_id=order_id,
                        intent=intent,
                        submitted_at=utcnow(),
                        status="filled",
                    ),
                    immediate_fills=(fill,),
                )

            self._resting[order_id] = _RestingOrder(order_id, intent)
            return PlacementResult(
                order=SubmittedOrder(
                    exchange_order_id=order_id,
                    intent=intent,
                    submitted_at=utcnow(),
                    status="submitted",
                )
            )

    async def cancel_order(self, exchange_order_id: str) -> None:
        async with self._lock:
            self._resting.pop(exchange_order_id, None)

    async def close(self) -> None:
        self._resting.clear()
        self._marks.clear()

    # ----- internals -----

    def _mark_from_table(self, symbol: str) -> Decimal | None:
        return self._marks.get(symbol)

    def _apply_slippage(self, mark: Decimal, side: str) -> Decimal:
        adj = mark * Decimal(self._cfg.slippage_bps) / Decimal(10_000)
        return mark + adj if side == "buy" else mark - adj

    def _build_fill(
        self, order_id: str, intent: OrderIntent, price: Decimal, qty: Decimal
    ) -> FillEvent:
        notional = qty * price
        fee = notional * Decimal(self._cfg.fee_bps) / Decimal(10_000)
        return FillEvent(
            exchange_fill_id=new_order_id(),
            exchange_order_id=order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=qty,
            price=price,
            fee=fee,
            fee_currency=self._cfg.fee_currency,
            filled_at=utcnow(),
        )

    def _match_resting(self, symbol: str, mark: Decimal) -> list[FillEvent]:
        fills: list[FillEvent] = []
        filled_ids: list[str] = []
        for oid, resting in self._resting.items():
            intent = resting.intent
            if intent.symbol != symbol:
                continue
            limit = intent.limit_price
            if limit is None:
                continue
            crossed = (intent.side == "buy" and mark <= limit) or (
                intent.side == "sell" and mark >= limit
            )
            if not crossed:
                continue
            fill_price = self._apply_slippage(min(mark, limit) if intent.side == "buy" else max(mark, limit), intent.side)
            fills.append(self._build_fill(oid, intent, fill_price, intent.quantity))
            filled_ids.append(oid)
        for oid in filled_ids:
            self._resting.pop(oid, None)
        return fills
