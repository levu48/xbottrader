"""Market-hours guard around any exchange adapter.

Wraps an :class:`ExchangeAdapter` with a :class:`MarketSession` so that orders
placed while the venue's market is closed are rejected *locally* — without ever
hitting the venue — using the existing ``"rejected"`` status. The order router
persists and emits a rejected order exactly like a venue rejection, so a closed
market surfaces as a normal (non-fatal) ``order_submitted`` event with
``status="rejected"`` rather than a ``bot_error``.

Only wired for venues whose session can close (equities). Crypto bots keep the
bare adapter, so this code never runs on the crypto path.
"""

from __future__ import annotations

from datetime import timezone
from typing import Callable

from ..market.session import MarketSession
from ..strategies.base import OrderIntent
from .base import ExchangeAdapter, PlacementResult, SubmittedOrder, new_order_id, utcnow


def _now_ms() -> int:
    return int(utcnow().astimezone(timezone.utc).timestamp() * 1000)


class SessionGuardedAdapter:
    """Implements :class:`ExchangeAdapter`, rejecting orders when the market is closed."""

    def __init__(
        self,
        inner: ExchangeAdapter,
        session: MarketSession,
        *,
        now_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._inner = inner
        self._session = session
        self._now_ms = now_ms

    @property
    def venue(self) -> str:
        return self._inner.venue

    async def place_order(self, intent: OrderIntent) -> PlacementResult:
        if not self._session.is_open(self._now_ms()):
            # Reject locally — never touch the venue. Reuses the "rejected" status
            # so the router writes a paper trail and emits a non-fatal event.
            return PlacementResult(
                order=SubmittedOrder(
                    exchange_order_id=new_order_id(),
                    intent=intent,
                    submitted_at=utcnow(),
                    status="rejected",
                )
            )
        return await self._inner.place_order(intent)

    async def cancel_order(self, exchange_order_id: str) -> None:
        await self._inner.cancel_order(exchange_order_id)

    async def close(self) -> None:
        await self._inner.close()
