"""Production launcher — runs a user's bot against a real venue.

Builds the runtime from the start request: ensures the bot's row in Postgres,
constructs the strategy, and wires either a paper adapter (real prices, simulated
fills) or a live ccxt adapter (real orders) depending on ``mode``. Market data
comes from a polling :class:`CcxtBarSource` over public OHLCV.

Safety: real-money orders are gated behind ``allow_live`` (XBT_ALLOW_LIVE). The
user's decrypted API key is read once, handed to the ccxt client, and never
logged, persisted, or stored on a public attribute.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..api.launcher import LaunchPlan
from ..api.schemas import StartBotRequest
from ..db.repositories import BotConfigRepo
from ..events.publisher import EventPublisher
from ..exchanges.ccxt_adapter import CcxtExchangeAdapter
from ..exchanges.ccxt_live import (
    CcxtBarSource,
    ExchangeCredentials,
    build_ccxt_client,
    build_public_ccxt_client,
)
from ..exchanges.paper import PaperConfig, PaperExchangeAdapter
from ..runtime.router import ExchangeOrderRouter
from ..security.keys import EncryptedEnvelope, EnvelopeCipher
from ..strategies.factory import build_strategy

CcxtClientFactory = Callable[[str, ExchangeCredentials], Any]
PublicClientFactory = Callable[[str], Any]


class ProductionLauncher:
    def __init__(
        self,
        publisher: EventPublisher,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: EnvelopeCipher,
        *,
        allow_live: bool = False,
        ccxt_client_factory: CcxtClientFactory = build_ccxt_client,
        public_client_factory: PublicClientFactory = build_public_ccxt_client,
        timeframe: str = "1m",
        poll_interval_s: float = 2.0,
    ) -> None:
        self._publisher = publisher
        self._sessions = session_factory
        self._cipher = cipher
        self._allow_live = allow_live
        self._ccxt_client_factory = ccxt_client_factory
        self._public_client_factory = public_client_factory
        self._timeframe = timeframe
        self._poll_interval_s = poll_interval_s

    async def launch(
        self,
        *,
        bot_id: str,
        user_id: str,
        request: object,
    ) -> LaunchPlan:
        assert isinstance(request, StartBotRequest), "production launcher needs a StartBotRequest"
        params = request.strategy
        exchange = request.exchange

        # Ensure the BotConfig row exists so order/fill FKs hold. (Reading the
        # strategy from this row as source-of-truth is a follow-up once a
        # create-bot flow exists; for now the request is authoritative.)
        async with self._sessions() as session:
            existing = await BotConfigRepo.get(session, bot_id, user_id=user_id)
            if existing is None:
                await BotConfigRepo.create(
                    session,
                    bot_id=bot_id,
                    user_id=user_id,
                    name=f"bot-{bot_id}",
                    exchange=exchange,
                    mode=request.mode,
                    strategy=params.model_dump(mode="json"),
                )
            await session.commit()

        strategy = build_strategy(params)

        if request.mode == "live":
            adapter = self._build_live_adapter(exchange, request)
        else:
            adapter = PaperExchangeAdapter(venue=exchange, config=PaperConfig())

        # Public client drives market data for both modes (no keys needed).
        mark_sink = (
            adapter.update_mark if isinstance(adapter, PaperExchangeAdapter) else None
        )
        bars = CcxtBarSource(
            self._public_client_factory(exchange),
            symbol=params.symbol,
            timeframe=self._timeframe,
            poll_interval_s=self._poll_interval_s,
            mark_sink=mark_sink,
        )

        router = ExchangeOrderRouter(
            user_id=user_id,
            adapter=adapter,
            publisher=self._publisher,
            session_factory=self._sessions,
        )
        return LaunchPlan(strategy=strategy, bars=bars, router=router)

    def _build_live_adapter(self, exchange: str, request: StartBotRequest) -> CcxtExchangeAdapter:
        if not self._allow_live:
            raise ValueError("live trading disabled: set XBT_ALLOW_LIVE=1 to enable")
        if request.credentials is None:
            raise ValueError("live mode requires encrypted credentials")
        # Decrypt in-process; the plaintext lives only until the ccxt client holds it.
        plaintext = self._cipher.decrypt(
            EncryptedEnvelope.from_dict(request.credentials.model_dump())
        )
        creds = ExchangeCredentials.from_plaintext(plaintext)
        client = self._ccxt_client_factory(exchange, creds)
        return CcxtExchangeAdapter(client, venue=exchange)
