from __future__ import annotations

import json
from decimal import Decimal

from app.events.publisher import EventPublisher
from app.exchanges.paper import PaperConfig, PaperExchangeAdapter
from app.runtime.router import ExchangeOrderRouter
from app.strategies.base import OrderIntent


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


async def test_router_emits_order_submitted_and_fill_for_market_buy() -> None:
    redis = FakeRedis()
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=10))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    router = ExchangeOrderRouter(
        user_id="u1", adapter=adapter, publisher=EventPublisher(redis)
    )

    await router.submit(
        "b1",
        OrderIntent(symbol="BTC/USDT", side="buy", type="market", quantity=Decimal("0.1")),
    )

    types = [f["event_type"] for _, f in redis.entries]
    assert types == ["order_submitted", "fill"]

    submitted_payload = json.loads(redis.entries[0][1]["payload"])
    assert submitted_payload["symbol"] == "BTC/USDT"
    assert submitted_payload["status"] == "filled"
    assert submitted_payload["quantity"] == "0.1"

    fill_payload = json.loads(redis.entries[1][1]["payload"])
    assert fill_payload["price"] == "50000"
    assert fill_payload["quantity"] == "0.1"
    assert fill_payload["fee_currency"] == "USDT"

    for _, fields in redis.entries:
        assert fields["user_id"] == "u1"
        assert fields["bot_id"] == "b1"


async def test_router_emits_only_order_submitted_for_resting_limit() -> None:
    redis = FakeRedis()
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    router = ExchangeOrderRouter(
        user_id="u1", adapter=adapter, publisher=EventPublisher(redis)
    )

    await router.submit(
        "b1",
        OrderIntent(
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            quantity=Decimal("1"),
            limit_price=Decimal("49000"),
        ),
    )

    types = [f["event_type"] for _, f in redis.entries]
    assert types == ["order_submitted"]


async def test_deliver_fills_publishes_out_of_band_fills() -> None:
    redis = FakeRedis()
    adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
    adapter.update_mark("BTC/USDT", Decimal("50000"))

    router = ExchangeOrderRouter(
        user_id="u1", adapter=adapter, publisher=EventPublisher(redis)
    )

    # Place a limit then drop the mark to trigger fills out-of-band
    await router.submit(
        "b1",
        OrderIntent(
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            quantity=Decimal("1"),
            limit_price=Decimal("49000"),
        ),
    )
    redis.entries.clear()
    fills = adapter.update_mark("BTC/USDT", Decimal("48800"))
    assert len(fills) == 1

    await router.deliver_fills("b1", fills)
    types = [f["event_type"] for _, f in redis.entries]
    assert types == ["fill"]
