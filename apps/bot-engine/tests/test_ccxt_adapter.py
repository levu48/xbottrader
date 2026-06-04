from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.exchanges.ccxt_adapter import CcxtExchangeAdapter
from app.strategies.base import OrderIntent


class FakeCcxt:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.cancels: list[str] = []
        self.closed = False

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {"symbol": symbol, "type": type, "side": side, "amount": amount, "price": price}
        )
        return self.response

    async def cancel_order(
        self, id: str, symbol: str | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.cancels.append(id)
        return {"id": id, "status": "canceled"}

    async def close(self) -> None:
        self.closed = True


async def test_market_order_filled_response_yields_one_fill() -> None:
    client = FakeCcxt(
        {
            "id": "abc123",
            "status": "closed",
            "filled": 0.5,
            "average": 50100.0,
            "fee": {"cost": 25.05, "currency": "USDT"},
        }
    )
    adapter = CcxtExchangeAdapter(client, venue="binance")
    result = await adapter.place_order(
        OrderIntent(symbol="BTC/USDT", side="buy", type="market", quantity=Decimal("0.5"))
    )

    assert client.calls[0] == {
        "symbol": "BTC/USDT",
        "type": "market",
        "side": "buy",
        "amount": 0.5,
        "price": None,
    }
    assert result.order.exchange_order_id == "abc123"
    assert result.order.status == "filled"
    assert len(result.immediate_fills) == 1
    f = result.immediate_fills[0]
    assert f.price == Decimal("50100.0")
    assert f.quantity == Decimal("0.5")
    assert f.fee == Decimal("25.05")
    assert f.fee_currency == "USDT"


async def test_limit_order_open_response_yields_no_fills() -> None:
    client = FakeCcxt({"id": "ord-7", "status": "open", "filled": 0, "average": None})
    adapter = CcxtExchangeAdapter(client, venue="binance")
    result = await adapter.place_order(
        OrderIntent(
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            quantity=Decimal("1"),
            limit_price=Decimal("49000"),
        )
    )

    assert client.calls[0]["price"] == 49000.0
    assert result.order.status == "submitted"
    assert result.immediate_fills == ()


async def test_trades_field_takes_precedence_over_aggregate() -> None:
    client = FakeCcxt(
        {
            "id": "ord-9",
            "status": "closed",
            "filled": 2.0,
            "average": 100.0,
            "trades": [
                {
                    "id": "t1",
                    "side": "buy",
                    "amount": 0.7,
                    "price": 99.5,
                    "fee": {"cost": 0.07, "currency": "USDT"},
                },
                {
                    "id": "t2",
                    "side": "buy",
                    "amount": 1.3,
                    "price": 100.3,
                    "fee": {"cost": 0.13, "currency": "USDT"},
                },
            ],
        }
    )
    adapter = CcxtExchangeAdapter(client, venue="binance")
    result = await adapter.place_order(
        OrderIntent(symbol="ETH/USDT", side="buy", type="market", quantity=Decimal("2"))
    )

    assert len(result.immediate_fills) == 2
    assert result.immediate_fills[0].quantity == Decimal("0.7")
    assert result.immediate_fills[1].price == Decimal("100.3")


async def test_unknown_status_defaults_to_submitted() -> None:
    client = FakeCcxt({"id": "x", "status": "weird-thing", "filled": 0})
    adapter = CcxtExchangeAdapter(client, venue="binance")
    result = await adapter.place_order(
        OrderIntent(symbol="BTC/USDT", side="sell", type="market", quantity=Decimal("1"))
    )
    assert result.order.status == "submitted"


async def test_cancel_and_close_forwarded() -> None:
    client = FakeCcxt({})
    adapter = CcxtExchangeAdapter(client, venue="binance")
    await adapter.cancel_order("abc")
    await adapter.close()
    assert client.cancels == ["abc"]
    assert client.closed is True


async def test_propagates_create_order_exceptions() -> None:
    class Boom:
        async def create_order(self, *a: Any, **kw: Any) -> dict[str, Any]:
            raise RuntimeError("InsufficientFunds")

        async def cancel_order(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {}

        async def close(self) -> None:
            return None

    adapter = CcxtExchangeAdapter(Boom(), venue="binance")
    with pytest.raises(RuntimeError, match="InsufficientFunds"):
        await adapter.place_order(
            OrderIntent(symbol="BTC/USDT", side="buy", type="market", quantity=Decimal("1"))
        )
