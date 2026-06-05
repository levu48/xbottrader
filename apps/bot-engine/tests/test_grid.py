from __future__ import annotations

from decimal import Decimal

import pytest

from app.strategies.base import Bar, StrategyState
from app.strategies.grid import GridParams, GridStrategy


def _bar(ts_ms: int, close: str, symbol: str = "BTC/USDT") -> Bar:
    p = Decimal(close)
    return Bar(ts_ms=ts_ms, symbol=symbol, open=p, high=p, low=p, close=p, volume=Decimal("0"))


def _params() -> GridParams:
    # Lines at 49000, 49500, 50000, 50500, 51000; quote_per_level = 400/4 = 100.
    return GridParams(
        symbol="BTC/USDT",
        lower_price=Decimal("49000"),
        upper_price=Decimal("51000"),
        grid_levels=5,
        total_quote=Decimal("400"),
    )


def test_lines_and_quote_per_level() -> None:
    p = _params()
    assert p.lines == [Decimal(x) for x in ("49000", "49500", "50000", "50500", "51000")]
    assert p.quote_per_level == Decimal("100")


def test_first_bar_only_sets_reference() -> None:
    s = GridStrategy(_params())
    state = StrategyState()
    assert s.on_bar(_bar(0, "50000"), state) == []
    assert state.custom["prev_close"] == Decimal("50000")


def test_falling_price_buys_each_crossed_line() -> None:
    s = GridStrategy(_params())
    state = StrategyState()
    s.on_bar(_bar(0, "50000"), state)  # reference
    intents = s.on_bar(_bar(1, "49000"), state)  # crosses 49500 and 49000

    assert [i.side for i in intents] == ["buy", "buy"]
    assert all(i.type == "market" for i in intents)
    # Processed high → low.
    assert intents[0].quantity == Decimal("100") / Decimal("49500")
    assert intents[1].quantity == Decimal("100") / Decimal("49000")
    assert len(state.custom["lots"]) == 2


def test_rising_price_sells_held_lots_only() -> None:
    s = GridStrategy(_params())
    state = StrategyState()
    s.on_bar(_bar(0, "50000"), state)
    s.on_bar(_bar(1, "49000"), state)  # buy 2 lots (49500, 49000)
    intents = s.on_bar(_bar(2, "50500"), state)  # crosses 49500, 50000, 50500 up

    # Only two lots are held → only two sells despite three crossed lines.
    assert [i.side for i in intents] == ["sell", "sell"]
    assert not state.custom["lots"]


def test_rising_with_no_inventory_does_nothing() -> None:
    s = GridStrategy(_params())
    state = StrategyState()
    s.on_bar(_bar(0, "49000"), state)  # reference
    assert s.on_bar(_bar(1, "50000"), state) == []  # rising, nothing held


def test_ignores_other_symbols() -> None:
    s = GridStrategy(_params())
    state = StrategyState()
    assert s.on_bar(_bar(0, "3000", symbol="ETH/USDT"), state) == []
    assert "prev_close" not in state.custom


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lower_price": Decimal("0"), "upper_price": Decimal("1"), "grid_levels": 5, "total_quote": Decimal("100")},
        {"lower_price": Decimal("100"), "upper_price": Decimal("100"), "grid_levels": 5, "total_quote": Decimal("100")},
        {"lower_price": Decimal("49000"), "upper_price": Decimal("51000"), "grid_levels": 1, "total_quote": Decimal("100")},
        {"lower_price": Decimal("49000"), "upper_price": Decimal("51000"), "grid_levels": 5, "total_quote": Decimal("0")},
    ],
)
def test_rejects_invalid_params(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        GridParams(symbol="BTC/USDT", **kwargs)
