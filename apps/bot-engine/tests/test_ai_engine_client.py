"""AiEngineClient: signs the signal request and parses the decision."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.api.auth import InternalAuthenticator
from app.clients.ai_engine import AiEngineClient, AiEngineError

SECRET = "shared-internal-secret-xyz"


def _client(handler: httpx.MockTransport) -> AiEngineClient:
    auth = InternalAuthenticator.from_env(SECRET)
    http = httpx.AsyncClient(transport=handler, base_url="http://ai-engine:5002")
    return AiEngineClient("http://ai-engine:5002", auth, http=http)


@pytest.mark.asyncio
async def test_signs_request_and_parses_decision() -> None:
    seen: dict[str, object] = {}
    verifier = InternalAuthenticator.from_env(SECRET)

    def handle(request: httpx.Request) -> httpx.Response:
        body = request.content
        seen["path"] = request.url.path
        seen["body"] = json.loads(body)
        # Server-side verification of the signature the client produced.
        ident = verifier.verify(
            method=request.method,
            path=request.url.path,
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
        )
        seen["user"] = ident.user_id
        return httpx.Response(
            200,
            json={
                "action": "buy",
                "reason": "momentum up",
                "usage": {"input_tokens": 90, "output_tokens": 7},
            },
        )

    client = _client(httpx.MockTransport(handle))
    decision = await client.signal(
        user_id="u1",
        symbol="BTC/USDT",
        closes=[Decimal("100"), Decimal("101.5")],
        position=Decimal("0"),
        guidance="be bold",
        model=None,
    )

    assert decision.action == "buy"
    assert decision.reason == "momentum up"
    assert decision.usage == {"input_tokens": 90, "output_tokens": 7}
    assert seen["path"] == "/strategy/signal"
    assert seen["user"] == "u1"
    # Decimals serialized as strings; model omitted because it was None.
    assert seen["body"] == {
        "symbol": "BTC/USDT",
        "closes": ["100", "101.5"],
        "position": "0",
        "guidance": "be bold",
    }


@pytest.mark.asyncio
async def test_raises_on_error_status() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad signature")

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(AiEngineError, match="401"):
        await client.signal(
            user_id="u1",
            symbol="BTC/USDT",
            closes=[Decimal("1"), Decimal("2")],
            position=Decimal("0"),
        )


@pytest.mark.asyncio
async def test_raises_on_invalid_action() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"action": "moon", "reason": "?"})

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(AiEngineError, match="invalid action"):
        await client.signal(
            user_id="u1",
            symbol="BTC/USDT",
            closes=[Decimal("1"), Decimal("2")],
            position=Decimal("0"),
        )
