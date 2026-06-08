"""Market-hours safety: the circuit breaker must not act on a stale/closed mark.

For an equity bot the mark goes stale across the overnight/weekend gap, so the
max-loss breaker must NOT trip while the market is closed (or on a mark older
than the configured age). Crypto (session=None) is unaffected.
"""

from __future__ import annotations

from decimal import Decimal

from app.events.publisher import EventPublisher
from app.exchanges.base import FillEvent, new_order_id, utcnow
from app.runtime.supervisor import BotHandle, BotState, Supervisor, _now_ms


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


class FakeSession:
    def __init__(self, *, open_: bool) -> None:
        self._open = open_

    def is_open(self, ts_ms: int) -> bool:
        return self._open

    def next_open_ms(self, ts_ms: int) -> int | None:
        return None


def _losing_handle(session, *, max_mark_age_ms: int | None = None) -> BotHandle:
    """A handle holding a position deep in the red against last_mark."""
    h = BotHandle(bot_id="b1", user_id="u1", max_loss_quote=Decimal("100"))
    h.ledger.apply(
        FillEvent(
            exchange_fill_id=new_order_id(),
            exchange_order_id=new_order_id(),
            symbol="AAPL",
            side="buy",
            quantity=Decimal("1"),
            price=Decimal("50000"),
            fee=Decimal("0"),
            fee_currency="USD",
            filled_at=utcnow(),
        )
    )
    h.last_mark = Decimal("49000")  # pnl = -1000, well past the -100 cap
    h.last_mark_ts_ms = _now_ms()
    h.session = session
    h.max_mark_age_ms = max_mark_age_ms
    return h


async def test_breaker_does_not_trip_while_market_closed() -> None:
    sup = Supervisor(EventPublisher(FakeRedis()))
    handle = _losing_handle(FakeSession(open_=False))

    tripped = await sup._tripped(handle)  # type: ignore[attr-defined]

    assert tripped is False
    assert handle.state != BotState.PAUSED  # not force-closed on a stale mark


async def test_breaker_trips_once_market_open() -> None:
    redis = FakeRedis()
    sup = Supervisor(EventPublisher(redis))
    handle = _losing_handle(FakeSession(open_=True))

    tripped = await sup._tripped(handle)  # type: ignore[attr-defined]

    assert tripped is True
    assert handle.state == BotState.PAUSED
    assert any(f["event_type"] == "bot_circuit_tripped" for _, f in redis.entries)


async def test_breaker_skips_stale_mark_when_open() -> None:
    sup = Supervisor(EventPublisher(FakeRedis()))
    handle = _losing_handle(FakeSession(open_=True), max_mark_age_ms=1000)
    handle.last_mark_ts_ms = _now_ms() - 60_000  # 60s old, older than the 1s cap

    assert await sup._tripped(handle) is False  # type: ignore[attr-defined]


async def test_crypto_breaker_unaffected_session_none() -> None:
    # No session (crypto path) → behaves exactly as before: trips on the loss.
    sup = Supervisor(EventPublisher(FakeRedis()))
    handle = _losing_handle(session=None)

    assert await sup._tripped(handle) is True  # type: ignore[attr-defined]
    assert handle.state == BotState.PAUSED
