from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import DcaParams, StartBotRequest
from app.events.publisher import EventPublisher
from app.launchers.demo_paper import DemoPaperLauncher
from app.runtime.supervisor import BotState, Supervisor


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


async def test_demo_launcher_runs_paper_dca_with_synthetic_bars(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = FakeRedis()
    publisher = EventPublisher(redis)
    launcher = DemoPaperLauncher(publisher, session_factory, bar_interval_seconds=0.01)

    plan = await launcher.launch(
        bot_id="b1",
        user_id="u1",
        request=StartBotRequest(
            strategy=DcaParams(
                strategy_type="dca",
                symbol="BTC/USDT",
                quote_amount=Decimal("50"),
                interval_minutes=1,
            )
        ),
    )

    supervisor = Supervisor(publisher)
    await supervisor.start(
        bot_id="b1",
        user_id="u1",
        strategy=plan.strategy,
        bars=plan.bars,
        router=plan.router,
    )

    # Let synthetic feed run for a moment, then stop
    await asyncio.sleep(0.08)
    await supervisor.stop("b1")

    types = [f["event_type"] for _, f in redis.entries]
    assert types[0] == "bot_started"
    assert "fill" in types
    fill_payload = next(
        json.loads(f["payload"]) for _, f in redis.entries if f["event_type"] == "fill"
    )
    assert fill_payload["symbol"] == "BTC/USDT"
    assert supervisor.get_state("b1") in (BotState.STOPPED, BotState.STOPPING)


async def test_demo_launcher_rejects_non_dca_for_now(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = FakeRedis()
    publisher = EventPublisher(redis)
    launcher = DemoPaperLauncher(publisher, session_factory)

    import pytest

    from app.api.schemas import GridParams

    with pytest.raises(ValueError, match="DCA"):
        await launcher.launch(
            bot_id="b1",
            user_id="u1",
            request=StartBotRequest(
                strategy=GridParams(
                    strategy_type="grid",
                    symbol="BTC/USDT",
                    lower_price=Decimal("49000"),
                    upper_price=Decimal("51000"),
                    grid_levels=5,
                    total_quote=Decimal("500"),
                ),
            ),
        )
