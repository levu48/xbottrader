"""AiSignalCompanion: consults the model and writes decisions into state."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from xbt_core.strategies.ai_signal import SIGNAL_SLOT, WINDOW_SLOT
from xbt_core.strategies.base import StrategyState

from app.clients.ai_engine import SignalDecision
from app.runtime.ai_companion import AiSignalCompanion, build_ai_companion


class FakeClient:
    def __init__(self, decision: SignalDecision | Exception) -> None:
        self.calls: list[dict[str, object]] = []
        self._decision = decision

    async def signal(self, **kwargs: object) -> SignalDecision:
        self.calls.append(kwargs)
        if isinstance(self._decision, Exception):
            raise self._decision
        return self._decision


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


def _companion(client: FakeClient, publisher: FakePublisher | None = None) -> AiSignalCompanion:
    return AiSignalCompanion(
        client=client,
        user_id="u1",
        bot_id="u1:bot-1",
        symbol="BTC/USDT",
        decision_interval_minutes=1,
        guidance="hold the line",
        model=None,
        publisher=publisher,
    )


def _stub_sleep(monkeypatch: pytest.MonkeyPatch, ticks: int) -> None:
    """Replace asyncio.sleep so the loop runs exactly `ticks` times then stops."""
    state = {"n": 0}

    async def fake_sleep(_seconds: float) -> None:
        state["n"] += 1
        if state["n"] > ticks:
            raise asyncio.CancelledError

    monkeypatch.setattr("app.runtime.ai_companion.asyncio.sleep", fake_sleep)


@pytest.mark.asyncio
async def test_writes_decision_into_state(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(SignalDecision(action="buy", reason="momentum"))
    publisher = FakePublisher()
    state = StrategyState()
    state.custom[WINDOW_SLOT] = [Decimal("100"), Decimal("101"), Decimal("102")]
    state.custom["ai_held_qty"] = Decimal("0")

    _stub_sleep(monkeypatch, ticks=1)
    with pytest.raises(asyncio.CancelledError):
        await _companion(client, publisher).run(state)

    signal = state.custom[SIGNAL_SLOT]
    assert signal == {"seq": 1, "action": "buy", "reason": "momentum", "ts_ms": signal["ts_ms"]}  # type: ignore[index]
    # Consulted with the snapshot window and the held position.
    assert client.calls[0]["closes"] == [Decimal("100"), Decimal("101"), Decimal("102")]
    assert client.calls[0]["position"] == Decimal("0")
    assert client.calls[0]["symbol"] == "BTC/USDT"
    # An ai_decision event was emitted for observability.
    assert len(publisher.events) == 1


@pytest.mark.asyncio
async def test_skips_when_window_too_small(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(SignalDecision(action="buy", reason="x"))
    state = StrategyState()
    state.custom[WINDOW_SLOT] = [Decimal("100")]  # only one close

    _stub_sleep(monkeypatch, ticks=2)
    with pytest.raises(asyncio.CancelledError):
        await _companion(client).run(state)

    assert client.calls == []  # never consulted
    assert SIGNAL_SLOT not in state.custom


@pytest.mark.asyncio
async def test_failed_consult_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(RuntimeError("ai engine down"))
    state = StrategyState()
    state.custom[WINDOW_SLOT] = [Decimal("100"), Decimal("101")]

    _stub_sleep(monkeypatch, ticks=2)
    # The loop must survive a failed consult and keep ticking until cancelled.
    with pytest.raises(asyncio.CancelledError):
        await _companion(client).run(state)

    assert len(client.calls) == 2  # tried both ticks
    assert SIGNAL_SLOT not in state.custom  # no verdict written on failure


def test_build_ai_companion_returns_none_for_non_ai() -> None:
    params = SimpleNamespace(strategy_type="dca", symbol="BTC/USDT")
    assert build_ai_companion(ai_client=None, params=params, user_id="u1", bot_id="b1") is None


def test_build_ai_companion_requires_client() -> None:
    params = SimpleNamespace(
        strategy_type="ai_signal",
        symbol="BTC/USDT",
        decision_interval_minutes=15,
        guidance=None,
        model=None,
    )
    with pytest.raises(ValueError, match="AI Engine"):
        build_ai_companion(ai_client=None, params=params, user_id="u1", bot_id="b1")


def test_build_ai_companion_builds_for_ai_signal() -> None:
    client = FakeClient(SignalDecision(action="hold", reason=""))
    params = SimpleNamespace(
        strategy_type="ai_signal",
        symbol="ETH/USDT",
        decision_interval_minutes=5,
        guidance="g",
        model="claude-opus-4-8",
    )
    companion = build_ai_companion(ai_client=client, params=params, user_id="u1", bot_id="b1")
    assert isinstance(companion, AiSignalCompanion)
