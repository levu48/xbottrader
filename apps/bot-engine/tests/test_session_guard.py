"""SessionGuardedAdapter — rejects orders while the market is closed."""

from __future__ import annotations

from decimal import Decimal

from xbt_core.exchanges.base import PlacementResult, SubmittedOrder, new_order_id, utcnow
from xbt_core.exchanges.session_guard import SessionGuardedAdapter
from xbt_core.strategies.base import OrderIntent


class FakeSession:
    def __init__(self, *, open_: bool) -> None:
        self._open = open_

    def is_open(self, ts_ms: int) -> bool:
        return self._open

    def next_open_ms(self, ts_ms: int) -> int | None:
        return None


class RecordingAdapter:
    """Inner ExchangeAdapter that records calls and always accepts orders."""

    def __init__(self) -> None:
        self.placed: list[OrderIntent] = []
        self.cancelled: list[str] = []
        self.closed = False

    @property
    def venue(self) -> str:
        return "alpaca"

    async def place_order(self, intent: OrderIntent) -> PlacementResult:
        self.placed.append(intent)
        return PlacementResult(
            order=SubmittedOrder(
                exchange_order_id=new_order_id(),
                intent=intent,
                submitted_at=utcnow(),
                status="submitted",
            )
        )

    async def cancel_order(self, exchange_order_id: str) -> None:
        self.cancelled.append(exchange_order_id)

    async def close(self) -> None:
        self.closed = True


def _intent() -> OrderIntent:
    return OrderIntent(symbol="AAPL", side="buy", type="market", quantity=Decimal("1"))


async def test_rejects_when_market_closed_without_touching_venue() -> None:
    inner = RecordingAdapter()
    guard = SessionGuardedAdapter(inner, FakeSession(open_=False), now_ms=lambda: 0)

    result = await guard.place_order(_intent())

    assert result.order.status == "rejected"
    assert result.immediate_fills == ()
    assert inner.placed == []  # never reached the venue


async def test_passes_through_when_market_open() -> None:
    inner = RecordingAdapter()
    guard = SessionGuardedAdapter(inner, FakeSession(open_=True), now_ms=lambda: 0)

    result = await guard.place_order(_intent())

    assert result.order.status == "submitted"
    assert len(inner.placed) == 1


async def test_cancel_and_close_pass_through() -> None:
    inner = RecordingAdapter()
    guard = SessionGuardedAdapter(inner, FakeSession(open_=False))
    assert guard.venue == "alpaca"
    await guard.cancel_order("ord-1")
    await guard.close()
    assert inner.cancelled == ["ord-1"]
    assert inner.closed is True
