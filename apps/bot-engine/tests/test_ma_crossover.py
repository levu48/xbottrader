from __future__ import annotations

from decimal import Decimal

import pytest

from app.strategies.base import Bar, OrderIntent, StrategyState
from app.strategies.ma_crossover import MaCrossoverParams, MaCrossoverStrategy


def _bar(ts_ms: int, close: str, symbol: str = "BTC/USDT") -> Bar:
    p = Decimal(close)
    return Bar(ts_ms=ts_ms, symbol=symbol, open=p, high=p, low=p, close=p, volume=Decimal("0"))


def _params() -> MaCrossoverParams:
    return MaCrossoverParams(
        symbol="BTC/USDT", fast_period=2, slow_period=3, position_quote=Decimal("100")
    )


def _feed(s: MaCrossoverStrategy, state: StrategyState, closes: list[str]) -> list[list[OrderIntent]]:
    return [s.on_bar(_bar(i, c), state) for i, c in enumerate(closes)]


def test_no_signal_until_slow_window_filled() -> None:
    s = MaCrossoverStrategy(_params())
    state = StrategyState()
    out = _feed(s, state, ["10", "10"])  # only 2 closes, slow_period is 3
    assert out == [[], []]


def test_golden_cross_opens_long() -> None:
    s = MaCrossoverStrategy(_params())
    state = StrategyState()
    # bar2 establishes fast==slow (not above); bar3 (close 13) lifts fast over slow.
    out = _feed(s, state, ["10", "10", "10", "13"])
    assert out[3] and out[3][0].side == "buy"
    assert out[3][0].quantity == Decimal("100") / Decimal("13")
    assert state.custom["in_position"] is True


def test_death_cross_closes_long() -> None:
    s = MaCrossoverStrategy(_params())
    state = StrategyState()
    out = _feed(s, state, ["10", "10", "10", "13", "5"])
    buy = out[3][0]
    sell = out[4][0]
    assert buy.side == "buy" and sell.side == "sell"
    assert sell.quantity == buy.quantity  # closes the exact position
    assert state.custom["in_position"] is False


def test_no_second_entry_while_in_position() -> None:
    s = MaCrossoverStrategy(_params())
    state = StrategyState()
    out = _feed(s, state, ["10", "10", "10", "13", "20"])  # stays above after entry
    assert out[3] and out[3][0].side == "buy"
    assert out[4] == []  # already long → no pyramiding


def test_ignores_other_symbols() -> None:
    s = MaCrossoverStrategy(_params())
    state = StrategyState()
    assert s.on_bar(_bar(0, "3000", symbol="ETH/USDT"), state) == []
    assert "closes" not in state.custom


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fast_period": 1, "slow_period": 3, "position_quote": Decimal("100")},
        {"fast_period": 5, "slow_period": 5, "position_quote": Decimal("100")},
        {"fast_period": 5, "slow_period": 3, "position_quote": Decimal("100")},
        {"fast_period": 2, "slow_period": 3, "position_quote": Decimal("0")},
    ],
)
def test_rejects_invalid_params(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        MaCrossoverParams(symbol="BTC/USDT", **kwargs)
