from __future__ import annotations

import base64
import json
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import DcaParams, EncryptedKeyEnvelope, StartBotRequest
from app.db.models import BotConfigRow
from app.events.publisher import EventPublisher
from app.exchanges.ccxt_adapter import CcxtExchangeAdapter
from app.exchanges.ccxt_live import CcxtBarSource, ExchangeCredentials
from app.exchanges.paper import PaperExchangeAdapter
from app.launchers.production import ProductionLauncher
from app.security.keys import EnvelopeCipher

TEST_KEK = base64.b64encode(b"\x11" * 32).decode()


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list = []

    async def xadd(self, stream: str, fields: dict, *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


class DummyPublicClient:
    async def fetch_ohlcv(self, symbol, timeframe="1m", *, limit=2):
        return []

    async def close(self) -> None:
        pass


def _dca_request(mode: str = "paper", credentials: EncryptedKeyEnvelope | None = None) -> StartBotRequest:
    return StartBotRequest(
        strategy=DcaParams(
            strategy_type="dca", symbol="BTC/USDT", quote_amount=Decimal("50"), interval_minutes=1
        ),
        mode=mode,  # type: ignore[arg-type]
        exchange="binance",
        credentials=credentials,
    )


def _make_launcher(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    allow_live: bool = False,
    ccxt_factory=None,
) -> tuple[ProductionLauncher, dict]:
    captured: dict = {}

    def default_ccxt_factory(exchange: str, creds: ExchangeCredentials):
        captured["exchange"] = exchange
        captured["creds"] = creds
        return object()  # CcxtExchangeAdapter only stores it; no calls at build time

    launcher = ProductionLauncher(
        EventPublisher(FakeRedis()),
        session_factory,
        EnvelopeCipher(TEST_KEK),
        allow_live=allow_live,
        ccxt_client_factory=ccxt_factory or default_ccxt_factory,
        public_client_factory=lambda exchange: DummyPublicClient(),
    )
    return launcher, captured


def _encrypt_creds(api_key: str, secret: str) -> EncryptedKeyEnvelope:
    env = EnvelopeCipher(TEST_KEK).encrypt(json.dumps({"apiKey": api_key, "secret": secret}))
    return EncryptedKeyEnvelope(
        v=env.v, dek_iv=env.dek_iv, dek_ct=env.dek_ct, data_iv=env.data_iv, data_ct=env.data_ct
    )


async def test_paper_mode_builds_paper_plan_and_persists_config(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    launcher, _ = _make_launcher(session_factory)
    plan = await launcher.launch(bot_id="b1", user_id="u1", request=_dca_request("paper"))

    assert isinstance(plan.bars, CcxtBarSource)
    assert isinstance(plan.router._adapter, PaperExchangeAdapter)  # type: ignore[attr-defined]

    async with session_factory() as s:
        rows = list((await s.execute(select(BotConfigRow))).scalars().all())
    assert len(rows) == 1
    assert rows[0].exchange == "binance" and rows[0].mode == "paper"


async def test_live_mode_rejected_when_not_allowed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    launcher, _ = _make_launcher(session_factory, allow_live=False)
    with pytest.raises(ValueError, match="live trading disabled"):
        await launcher.launch(
            bot_id="b1", user_id="u1", request=_dca_request("live", _encrypt_creds("k", "s"))
        )


async def test_live_mode_requires_credentials(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    launcher, _ = _make_launcher(session_factory, allow_live=True)
    with pytest.raises(ValueError, match="requires encrypted credentials"):
        await launcher.launch(bot_id="b1", user_id="u1", request=_dca_request("live", None))


async def test_live_mode_decrypts_into_ccxt_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    launcher, captured = _make_launcher(session_factory, allow_live=True)
    creds = _encrypt_creds("test-key", "test-secret")

    plan = await launcher.launch(bot_id="b1", user_id="u1", request=_dca_request("live", creds))

    assert isinstance(plan.router._adapter, CcxtExchangeAdapter)  # type: ignore[attr-defined]
    # The factory received the DECRYPTED key material.
    assert captured["exchange"] == "binance"
    assert captured["creds"].api_key == "test-key"
    assert captured["creds"].secret == "test-secret"
    # Plaintext must not linger on the launcher itself.
    assert "test-secret" not in repr(launcher.__dict__)
