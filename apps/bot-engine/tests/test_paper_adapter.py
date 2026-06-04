from __future__ import annotations

from decimal import Decimal

import pytest

from app.exchanges.paper import PaperConfig, PaperExchangeAdapter
from app.strategies.base import OrderIntent


def _market(side: str = "buy", qty: str = "1") -> OrderIntent:
    return OrderIntent(symbol="BTC/USDT", side=side, type="market", quantity=Decimal(qty))


def _limit(price: str, side: str = "buy", qty: str = "1") -> OrderIntent:
    return OrderIntent(
        symbol="BTC/USDT", side=side, type="limit", quantity=Decimal(qty), limit_price=Decimal(price)
    )


async def test_market_buy_fills_immediately_with_slippage_and_fee() -> None:
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=10, fee_bps=20))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    result = await adapter.place_order(_market("buy", "1"))

    assert result.order.status == "filled"
    assert len(result.immediate_fills) == 1
    fill = result.immediate_fills[0]
    assert fill.price == Decimal("50050")  # 50000 * (1 + 10/10_000)
    assert fill.quantity == Decimal("1")
    # 20 bps fee on notional
    assert fill.fee == Decimal("50050") * Decimal("1") * Decimal("20") / Decimal("10000")


async def test_market_sell_slippage_goes_other_direction() -> None:
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=10, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    result = await adapter.place_order(_market("sell", "1"))
    assert result.immediate_fills[0].price == Decimal("49950")


async def test_market_order_rejected_without_mark() -> None:
    adapter = PaperExchangeAdapter()
    result = await adapter.place_order(_market("buy", "1"))
    assert result.order.status == "rejected"
    assert result.immediate_fills == ()


async def test_limit_order_rests_and_fills_on_cross() -> None:
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    result = await adapter.place_order(_limit("49000", side="buy"))
    assert result.order.status == "submitted"
    assert result.immediate_fills == ()

    # mark drops below limit → fills
    fills = adapter.update_mark("BTC/USDT", Decimal("48800"))
    assert len(fills) == 1
    assert fills[0].exchange_order_id == result.order.exchange_order_id
    assert fills[0].price <= Decimal("49000")


async def test_limit_order_does_not_fill_when_no_cross() -> None:
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))
    await adapter.place_order(_limit("49000", side="buy"))
    fills = adapter.update_mark("BTC/USDT", Decimal("49500"))
    assert fills == []


async def test_cancel_order_removes_resting() -> None:
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))
    result = await adapter.place_order(_limit("49000", side="buy"))
    await adapter.cancel_order(result.order.exchange_order_id)
    fills = adapter.update_mark("BTC/USDT", Decimal("48000"))
    assert fills == []


async def test_close_clears_state() -> None:
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))
    await adapter.place_order(_limit("49000", side="buy"))
    await adapter.close()
    fills = adapter.update_mark("BTC/USDT", Decimal("48000"))
    assert fills == []


def test_intent_validation() -> None:
    # market with limit_price set
    with pytest.raises(ValueError):
        OrderIntent(symbol="X/Y", side="buy", type="market", quantity=Decimal("1"), limit_price=Decimal("1"))
    # limit without limit_price
    with pytest.raises(ValueError):
        OrderIntent(symbol="X/Y", side="buy", type="limit", quantity=Decimal("1"))
