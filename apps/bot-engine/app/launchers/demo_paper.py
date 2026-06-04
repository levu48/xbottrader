"""Demo launcher used for staging deploys.

Lets a deployed Bot Engine run end-to-end without a real exchange or decrypted
keys — useful for validating the infrastructure topology (DO Droplet +
Reserved IP + Redis + HMAC contract) before the production launcher exists.
Production deploys must NOT use this.

It still uses real DB persistence: a ``BotConfigRow`` is created on the fly if
the supplied bot_id isn't already there, so the audit trail and fills behave
the same as production.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..api.launcher import LaunchPlan
from ..api.schemas import StartBotRequest
from ..db.repositories import BotConfigRepo
from ..events.publisher import EventPublisher
from ..exchanges.paper import PaperConfig, PaperExchangeAdapter
from ..runtime.router import ExchangeOrderRouter
from ..strategies.base import Bar
from ..strategies.dca import DcaParams, DcaStrategy


class SyntheticBarSource:
    """Emits a random-walk bar stream forever, ticking in real time."""

    def __init__(
        self,
        *,
        symbol: str,
        adapter: PaperExchangeAdapter,
        start_price: Decimal = Decimal("50000"),
        interval_seconds: float = 5.0,
        volatility_bps: int = 20,
    ) -> None:
        self._symbol = symbol
        self._adapter = adapter
        self._price = start_price
        self._interval_s = interval_seconds
        self._vol_bps = volatility_bps

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        ts_ms = 0
        rng = random.Random()
        bar_interval_ms = int(self._interval_s * 1000)
        while True:
            self._adapter.update_mark(self._symbol, self._price)
            yield Bar(
                ts_ms=ts_ms,
                symbol=self._symbol,
                open=self._price,
                high=self._price,
                low=self._price,
                close=self._price,
                volume=Decimal("0"),
            )
            change_bps = (rng.random() - 0.5) * 2 * self._vol_bps
            self._price *= Decimal(1) + Decimal(change_bps) / Decimal(10_000)
            if self._price <= 0:
                self._price = Decimal("1")
            ts_ms += bar_interval_ms
            await asyncio.sleep(self._interval_s)


class DemoPaperLauncher:
    def __init__(
        self,
        publisher: EventPublisher,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        bar_interval_seconds: float = 5.0,
    ) -> None:
        self._publisher = publisher
        self._sessions = session_factory
        self._bar_interval_s = bar_interval_seconds

    async def launch(
        self,
        *,
        bot_id: str,
        user_id: str,
        request: object,
    ) -> LaunchPlan:
        assert isinstance(request, StartBotRequest), "demo launcher needs a StartBotRequest"
        params = request.strategy
        if params.strategy_type != "dca":
            raise ValueError(
                f"demo launcher only supports DCA strategies (got {params.strategy_type})"
            )

        # Make sure the BotConfig row exists so order/fill FKs hold.
        async with self._sessions() as session:
            existing = await BotConfigRepo.get(session, bot_id, user_id=user_id)
            if existing is None:
                await BotConfigRepo.create(
                    session,
                    bot_id=bot_id,
                    user_id=user_id,
                    name=f"demo-{bot_id}",
                    exchange="paper",
                    mode="paper",
                    strategy=params.model_dump(mode="json"),
                )
            await session.commit()

        adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=5, fee_bps=10))
        bars = SyntheticBarSource(
            symbol=params.symbol,
            adapter=adapter,
            interval_seconds=self._bar_interval_s,
        )
        strategy = DcaStrategy(
            DcaParams(
                symbol=params.symbol,
                quote_amount=params.quote_amount,
                interval_minutes=params.interval_minutes,
            )
        )
        router = ExchangeOrderRouter(
            user_id=user_id,
            adapter=adapter,
            publisher=self._publisher,
            session_factory=self._sessions,
        )
        return LaunchPlan(strategy=strategy, bars=bars, router=router)
