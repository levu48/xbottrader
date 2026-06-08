"""ccxt-backed live exchange adapter.

Wraps any ccxt ``async_support`` exchange instance (Binance, Coinbase, Bybit,
OKX, …) behind the common :class:`ExchangeAdapter` interface. The adapter
expects the caller to instantiate ccxt with the user's decrypted API keys (see
``app/security/keys.py``) — those keys never live outside this process.

Note on numeric precision: ccxt uses Python ``float``. Production code should
round amounts/prices through ``client.amount_to_precision`` /
``price_to_precision`` to match each venue's filter rules. The adapter does the
basic float bridge; per-venue precision normalization is a TODO for the first
real-money deploy.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any, Protocol

from ..strategies.base import OrderIntent
from .base import FillEvent, PlacementResult, SubmittedOrder, new_order_id, utcnow
from .ccxt_live import _is_alpaca

# Alpaca supports fractional equity quantities to 9 decimal places. Round DOWN so
# a quote-sized order never rounds *up* into spending more than intended.
_FRACTIONAL_QTY_STEP = Decimal("0.000000001")

# ccxt status strings → our adapter's normalized status
_STATUS_MAP: dict[str, str] = {
    "closed": "filled",
    "filled": "filled",
    "open": "submitted",
    "partial": "partially_filled",
    "canceled": "rejected",
    "cancelled": "rejected",
    "rejected": "rejected",
    "expired": "rejected",
}


class CcxtClient(Protocol):
    """The subset of ccxt.async_support we use. Lets tests pass a fake."""

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    async def cancel_order(
        self, id: str, symbol: str | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        ...

    async def close(self) -> None:
        ...


class CcxtExchangeAdapter:
    def __init__(self, client: CcxtClient, venue: str) -> None:
        self._client = client
        self._venue = venue

    @property
    def venue(self) -> str:
        return self._venue

    async def place_order(self, intent: OrderIntent) -> PlacementResult:
        qty = intent.quantity
        if _is_alpaca(self._venue):
            qty = qty.quantize(_FRACTIONAL_QTY_STEP, rounding=ROUND_DOWN)
            if qty <= 0:
                # Rounded below the minimum tradeable size — reject locally.
                return self._rejected(intent)
            # Alpaca does not accept fractional *limit* orders.
            if intent.type == "limit" and qty != qty.to_integral_value():
                return self._rejected(intent)

        price_arg = float(intent.limit_price) if intent.limit_price is not None else None
        raw = await self._client.create_order(
            symbol=intent.symbol,
            type=intent.type,
            side=intent.side,
            amount=float(qty),
            price=price_arg,
        )
        exchange_order_id = str(raw.get("id") or new_order_id())
        status = _STATUS_MAP.get(str(raw.get("status", "")).lower(), "submitted")

        order = SubmittedOrder(
            exchange_order_id=exchange_order_id,
            intent=intent,
            submitted_at=utcnow(),
            status=status,
        )
        return PlacementResult(order=order, immediate_fills=tuple(_extract_fills(raw, intent)))

    async def cancel_order(self, exchange_order_id: str) -> None:
        await self._client.cancel_order(exchange_order_id)

    async def close(self) -> None:
        await self._client.close()

    def _rejected(self, intent: OrderIntent) -> PlacementResult:
        """A locally-rejected order (never sent to the venue). Reuses 'rejected'."""
        return PlacementResult(
            order=SubmittedOrder(
                exchange_order_id=new_order_id(),
                intent=intent,
                submitted_at=utcnow(),
                status="rejected",
            )
        )


def _extract_fills(raw: dict[str, Any], intent: OrderIntent) -> list[FillEvent]:
    trades = raw.get("trades")
    if isinstance(trades, list) and trades:
        return [_fill_from_trade(t, raw, intent) for t in trades]

    filled = _as_decimal(raw.get("filled"))
    average = _as_decimal(raw.get("average") or raw.get("price"))
    if filled is None or average is None or filled <= 0:
        return []

    fee_data = raw.get("fee") or {}
    return [
        FillEvent(
            exchange_fill_id=str(raw.get("id") or new_order_id()),
            exchange_order_id=str(raw.get("id") or new_order_id()),
            symbol=intent.symbol,
            side=intent.side,
            quantity=filled,
            price=average,
            fee=_as_decimal(fee_data.get("cost")) or Decimal("0"),
            fee_currency=str(fee_data.get("currency") or ""),
            filled_at=utcnow(),
        )
    ]


def _fill_from_trade(
    trade: dict[str, Any], parent: dict[str, Any], intent: OrderIntent
) -> FillEvent:
    fee_data = trade.get("fee") or {}
    return FillEvent(
        exchange_fill_id=str(trade.get("id") or new_order_id()),
        exchange_order_id=str(parent.get("id") or new_order_id()),
        symbol=intent.symbol,
        side=str(trade.get("side") or intent.side),
        quantity=_as_decimal(trade.get("amount")) or Decimal("0"),
        price=_as_decimal(trade.get("price")) or Decimal("0"),
        fee=_as_decimal(fee_data.get("cost")) or Decimal("0"),
        fee_currency=str(fee_data.get("currency") or ""),
        filled_at=utcnow(),
    )


def _as_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (ArithmeticError, ValueError):
        return None
