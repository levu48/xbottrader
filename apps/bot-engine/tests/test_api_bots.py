"""End-to-end test for the control plane: HMAC + supervisor + paper run."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.auth import InternalAuthenticator
from app.api.launcher import BotLauncher, LaunchPlan
from app.events.publisher import EventPublisher
from app.exchanges.paper import PaperConfig, PaperExchangeAdapter
from app.main import create_app
from app.runtime.router import ExchangeOrderRouter
from app.strategies.base import Bar
from app.strategies.dca import DcaParams, DcaStrategy

SECRET = "shared-internal-secret-test"


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


class BarFeedWithMark:
    def __init__(self, bars: list[Bar], adapter: PaperExchangeAdapter) -> None:
        self._bars = bars
        self._adapter = adapter

    def __aiter__(self) -> AsyncIterator[Bar]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Bar]:
        for bar in self._bars:
            self._adapter.update_mark(bar.symbol, bar.close)
            yield bar
            await asyncio.sleep(0)


def _bar(ts_ms: int, close: str, symbol: str = "BTC/USDT") -> Bar:
    p = Decimal(close)
    return Bar(ts_ms=ts_ms, symbol=symbol, open=p, high=p, low=p, close=p, volume=Decimal("0"))


class PaperLauncher:
    """Test launcher: build a finite paper run from the start request."""

    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher
        self.last_plan: LaunchPlan | None = None

    async def launch(
        self, *, bot_id: str, user_id: str, request: object
    ) -> LaunchPlan:
        from app.api.schemas import StartBotRequest

        assert isinstance(request, StartBotRequest)
        params = request.strategy
        # MVP API only wires DCA; reject other shapes loudly
        assert params.strategy_type == "dca"

        adapter = PaperExchangeAdapter(config=PaperConfig(slippage_bps=0, fee_bps=0))
        bars = BarFeedWithMark(
            [_bar(0, "50000", params.symbol), _bar(60_000, "50100", params.symbol)],
            adapter,
        )
        strategy = DcaStrategy(
            DcaParams(
                symbol=params.symbol,
                quote_amount=params.quote_amount,
                interval_minutes=params.interval_minutes,
            )
        )
        router = ExchangeOrderRouter(user_id=user_id, adapter=adapter, publisher=self._publisher)
        plan = LaunchPlan(strategy=strategy, bars=bars, router=router)
        self.last_plan = plan
        return plan


def _signed(method: str, path: str, user_id: str, body: bytes) -> dict[str, str]:
    auth = InternalAuthenticator(SECRET.encode())
    ts = int(time.time())
    sig = auth.sign(method=method, path=path, ts=ts, user_id=user_id, body=body)
    return {"x-xbt-user": user_id, "x-xbt-ts": str(ts), "x-xbt-sig": sig}


def _client() -> tuple[TestClient, FakeRedis, PaperLauncher]:
    redis = FakeRedis()
    publisher = EventPublisher(redis)
    launcher = PaperLauncher(publisher)
    auth = InternalAuthenticator(SECRET.encode())
    app = create_app(launcher=launcher, publisher=publisher, internal_auth=auth)
    return TestClient(app), redis, launcher


def test_start_endpoint_runs_a_bot_end_to_end() -> None:
    client, redis, _ = _client()

    body = json.dumps(
        {
            "strategy": {
                "strategy_type": "dca",
                "symbol": "BTC/USDT",
                "quote_amount": "100",
                "interval_minutes": 1,
            }
        }
    ).encode()

    resp = client.post(
        "/bots/b1/start",
        content=body,
        headers={
            **_signed("POST", "/bots/b1/start", "u1", body),
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["bot_id"] == "b1"

    # Wait for finite bar source to finish
    deadline = time.time() + 2.0
    while time.time() < deadline:
        # poll status endpoint
        status_body = b""
        status_resp = client.get(
            "/bots/b1",
            headers=_signed("GET", "/bots/b1", "u1", status_body),
        )
        if status_resp.json()["state"] == "stopped":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("bot did not reach stopped state in time")

    types = [f["event_type"] for _, f in redis.entries]
    assert types[0] == "bot_started"
    assert types[-1] == "bot_stopped"
    assert types.count("fill") == 2
    assert types.count("order_submitted") == 2


def test_unauthenticated_request_rejected() -> None:
    client, _, _ = _client()
    resp = client.post(
        "/bots/b1/start",
        content=b"{}",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 401


def test_signature_mismatch_rejected() -> None:
    client, _, _ = _client()
    body = b'{"strategy":{"strategy_type":"dca","symbol":"BTC/USDT","quote_amount":"100","interval_minutes":1}}'
    headers = _signed("POST", "/bots/b1/start", "u1", body)
    # tamper with body
    tampered = body.replace(b'"100"', b'"99999"')
    resp = client.post(
        "/bots/b1/start",
        content=tampered,
        headers={**headers, "content-type": "application/json"},
    )
    assert resp.status_code == 401


def test_stop_endpoint_returns_404_for_unknown_bot() -> None:
    client, _, _ = _client()
    resp = client.post(
        "/bots/never-started/stop",
        content=b"",
        headers=_signed("POST", "/bots/never-started/stop", "u1", b""),
    )
    assert resp.status_code == 404


def test_get_endpoint_isolates_users() -> None:
    client, _, _ = _client()
    body = json.dumps(
        {
            "strategy": {
                "strategy_type": "dca",
                "symbol": "BTC/USDT",
                "quote_amount": "100",
                "interval_minutes": 1,
            }
        }
    ).encode()
    # start as u1
    client.post(
        "/bots/b9/start",
        content=body,
        headers={**_signed("POST", "/bots/b9/start", "u1", body), "content-type": "application/json"},
    )
    # u2 should not see u1's bot
    resp = client.get("/bots/b9", headers=_signed("GET", "/bots/b9", "u2", b""))
    assert resp.status_code == 404
