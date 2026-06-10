from __future__ import annotations

from decimal import Decimal

import pytest
from xbt_core.strategies.python.momentum import MomentumParams, MomentumStrategy
from xbt_core.strategies.python_registry import build_python_strategy, register

from app.api.schemas import PythonParams
from app.strategies.base import Bar, OrderIntent, StrategyState
from app.strategies.factory import build_strategy


def _bar(ts_ms: int, close: str, symbol: str = "BTC/USDT") -> Bar:
    p = Decimal(close)
    return Bar(ts_ms=ts_ms, symbol=symbol, open=p, high=p, low=p, close=p, volume=Decimal("0"))


def _feed(s: MomentumStrategy, state: StrategyState, closes: list[str]) -> list[list[OrderIntent]]:
    return [s.on_bar(_bar(i, c), state) for i, c in enumerate(closes)]


# --- factory / registry wiring -------------------------------------------- #
def test_factory_builds_registered_python_strategy() -> None:
    cfg = PythonParams(
        strategy_type="python",
        symbol="BTC/USDT",
        strategy_key="momentum",
        params={"symbol": "BTC/USDT", "lookback": 2, "threshold_pct": "5", "quote_amount": "100"},
    )
    assert isinstance(build_strategy(cfg), MomentumStrategy)


def test_unknown_strategy_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown python strategy_key"):
        build_python_strategy("does-not-exist", {})


def test_duplicate_registration_raises() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register("momentum")(MomentumStrategy)


def test_from_params_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        MomentumParams.from_params(
            {"symbol": "BTC/USDT", "lookback": 2, "threshold_pct": "0", "quote_amount": "100"}
        )


# --- pure on_bar behaviour ------------------------------------------------ #
def _params() -> MomentumParams:
    return MomentumParams(
        symbol="BTC/USDT", lookback=2, threshold_pct=Decimal("5"), quote_amount=Decimal("100")
    )


def test_no_signal_until_window_filled() -> None:
    s = MomentumStrategy(_params())
    state = StrategyState()
    # lookback 2 needs 3 closes before the first measurement.
    assert _feed(s, state, ["100", "100"]) == [[], []]


def test_momentum_up_opens_long_then_down_closes() -> None:
    s = MomentumStrategy(_params())
    state = StrategyState()
    # closes[0]=100; at bar2 close 110 -> +10% >= 5% -> buy.
    out = _feed(s, state, ["100", "105", "110"])
    assert out[2] and out[2][0].side == "buy"
    assert out[2][0].quantity == Decimal("100") / Decimal("110")
    assert state.custom["in_position"] is True
    # window now [105,110,...]; feed a drop of >=5% from window start to sell.
    sell = s.on_bar(_bar(3, "99"), state)  # ref=105 -> (99-105)/105 ~ -5.7%
    assert sell and sell[0].side == "sell"
    assert sell[0].quantity == out[2][0].quantity  # closes the exact position
    assert state.custom["in_position"] is False


def _sig(runs: list[list[OrderIntent]]) -> list[list[tuple[str, Decimal]]]:
    return [[(i.side, i.quantity) for i in r] for r in runs]


def test_determinism_same_bars_same_intents() -> None:
    closes = ["100", "104", "112", "108", "95", "120"]
    a = MomentumStrategy(_params())
    b = MomentumStrategy(_params())
    assert _sig(_feed(a, StrategyState(), closes)) == _sig(_feed(b, StrategyState(), closes))


def test_ignores_other_symbols() -> None:
    s = MomentumStrategy(_params())
    state = StrategyState()
    assert s.on_bar(_bar(0, "3000", symbol="ETH/USDT"), state) == []
    assert "closes" not in state.custom
