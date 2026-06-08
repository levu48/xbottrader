from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from xbt_core.internal_auth import InternalAuthenticator

from app.backtest.engine import Backtester
from app.llm.author import StrategyAuthor
from app.llm.copilot import Copilot
from app.llm.signal import SignalAdvisor
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


def _fake_author_complete(sb, msgs, model, mt, tools, tool_choice):
    # Return a valid custom_rules tool input (the route validates it).
    tool_input = {
        "indicators": [{"name": "fast", "fn": "sma", "period": 10}],
        "rules": [{"when": {"op": ">", "left": "price", "right": "fast"}, "do": {"side": "buy", "quote": "100"}}],
        "explanation": "Buy when price is above its 10-bar SMA.",
    }
    return tool_input, {"input_tokens": 1, "output_tokens": 1}


def _fake_signal_complete(sb, msgs, model, mt, tools, tool_choice):
    return {"action": "buy", "reason": "uptrend"}, {"input_tokens": 1, "output_tokens": 1}


def _client() -> TestClient:
    copilot = Copilot(lambda sb, msgs, model, mt: ("copilot reply", {"input_tokens": 1, "output_tokens": 1}))
    candles = [[i * 3_600_000, 100, 100, 100, 100, 1] for i in range(5)]
    backtester = Backtester(lambda exchange: FakeData(candles))
    author = StrategyAuthor(_fake_author_complete)
    signal_advisor = SignalAdvisor(_fake_signal_complete)
    auth = InternalAuthenticator(SECRET)
    return TestClient(
        create_app(
            internal_auth=auth,
            copilot=copilot,
            backtester=backtester,
            author=author,
            signal_advisor=signal_advisor,
        )
    )


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


def test_strategy_author_signed() -> None:
    client = _client()
    payload = {"description": "buy when price tops its SMA", "symbol": "BTC/USDT", "exchange": "binance"}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/strategy/author",
        content=body,
        headers={**_signed("POST", "/strategy/author", "u1", body), "content-type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["strategy"]["strategy_type"] == "custom_rules"
    assert data["strategy"]["symbol"] == "BTC/USDT"
    assert data["explanation"]


def test_strategy_signal_signed() -> None:
    client = _client()
    payload = {"symbol": "BTC/USDT", "closes": ["100", "101", "102"], "position": "0"}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/strategy/signal",
        content=body,
        headers={**_signed("POST", "/strategy/signal", "u1", body), "content-type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["action"] == "buy"
