from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from xbt_core.internal_auth import InternalAuthenticator

from app.backtest.engine import Backtester
from app.llm.copilot import Copilot
from app.main import create_app

SECRET = b"ai-engine-test-secret"


class FakeData:
    def __init__(self, candles: list[list]) -> None:
        self._candles = candles
        self.closed = False

    async def fetch_ohlcv(self, symbol, timeframe="1h", *, since=None, limit=500):
        return self._candles

    async def close(self) -> None:
        self.closed = True


def _signed(method: str, path: str, user: str, body: bytes) -> dict[str, str]:
    auth = InternalAuthenticator(SECRET)
    ts = int(time.time())
    sig = auth.sign(method=method, path=path, ts=ts, user_id=user, body=body)
    return {"x-xbt-user": user, "x-xbt-ts": str(ts), "x-xbt-sig": sig}


def _client() -> TestClient:
    copilot = Copilot(lambda sb, msgs, model, mt: ("copilot reply", {"input_tokens": 1, "output_tokens": 1}))
    candles = [[i * 3_600_000, 100, 100, 100, 100, 1] for i in range(5)]
    backtester = Backtester(lambda exchange: FakeData(candles))
    auth = InternalAuthenticator(SECRET)
    return TestClient(create_app(internal_auth=auth, copilot=copilot, backtester=backtester))


def test_copilot_requires_auth() -> None:
    resp = _client().post("/copilot/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 401


def test_copilot_chat_signed() -> None:
    client = _client()
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    resp = client.post(
        "/copilot/chat",
        content=body,
        headers={**_signed("POST", "/copilot/chat", "u1", body), "content-type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "copilot reply"


def test_backtest_run_signed() -> None:
    client = _client()
    payload = {
        "strategy": {
            "strategy_type": "dca",
            "symbol": "BTC/USDT",
            "quote_amount": "100",
            "interval_minutes": 60,
        },
        "timeframe": "1h",
        "limit": 5,
        "starting_cash": "1000",
    }
    body = json.dumps(payload).encode()
    resp = client.post(
        "/backtest/run",
        content=body,
        headers={**_signed("POST", "/backtest/run", "u1", body), "content-type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["symbol"] == "BTC/USDT"
    assert data["bars"] == 5
    assert "total_return_pct" in data["stats"]
    assert len(data["equity_curve"]) == 5
