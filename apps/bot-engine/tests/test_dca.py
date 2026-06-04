from __future__ import annotations

from decimal import Decimal

import pytest

from app.strategies.base import Bar, StrategyState
from app.strategies.dca import DcaParams, DcaStrategy

MIN = 60_000


def _bar(ts_ms: int, close: Decimal, symbol: str = "BTC/USDT") -> Bar:
    return Bar(
        ts_ms=ts_ms,
        symbol=symbol,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("0"),
    )


def test_first_bar_buys_quote_amount_worth() -> None:
    s = DcaStrategy(DcaParams(symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=60))
    state = StrategyState()
    intents = s.on_bar(_bar(0, Decimal("50000")), state)
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "buy"
    assert intent.type == "market"
    assert intent.quantity == Decimal("100") / Decimal("50000")
    assert state.last_action_ts_ms == 0


def test_does_not_buy_again_before_interval() -> None:
    s = DcaStrategy(DcaParams(symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=60))
    state = StrategyState()
    s.on_bar(_bar(0, Decimal("50000")), state)
    intents = s.on_bar(_bar(30 * MIN, Decimal("50100")), state)
    assert intents == []


def test_buys_again_after_interval() -> None:
    s = DcaStrategy(DcaParams(symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=60))
    state = StrategyState()
    s.on_bar(_bar(0, Decimal("50000")), state)
    intents = s.on_bar(_bar(60 * MIN, Decimal("50100")), state)
    assert len(intents) == 1
    assert state.last_action_ts_ms == 60 * MIN


def test_ignores_other_symbols() -> None:
    s = DcaStrategy(DcaParams(symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=60))
    state = StrategyState()
    intents = s.on_bar(_bar(0, Decimal("3000"), symbol="ETH/USDT"), state)
    assert intents == []
    assert state.last_action_ts_ms is None


def test_skips_zero_or_negative_price() -> None:
    s = DcaStrategy(DcaParams(symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=60))
    state = StrategyState()
    assert s.on_bar(_bar(0, Decimal("0")), state) == []


def test_rejects_invalid_params() -> None:
    with pytest.raises(ValueError):
        DcaParams(symbol="BTC/USDT", quote_amount=Decimal("0"), interval_minutes=60)
    with pytest.raises(ValueError):
        DcaParams(symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=0)
