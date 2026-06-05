from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.schemas import DcaParams, GridParams, MaCrossoverParams
from app.strategies.dca import DcaStrategy
from app.strategies.factory import build_strategy
from app.strategies.grid import GridStrategy
from app.strategies.ma_crossover import MaCrossoverStrategy


def test_builds_dca() -> None:
    cfg = DcaParams(
        strategy_type="dca", symbol="BTC/USDT", quote_amount=Decimal("50"), interval_minutes=1
    )
    s = build_strategy(cfg)
    assert isinstance(s, DcaStrategy)


def test_builds_grid() -> None:
    cfg = GridParams(
        strategy_type="grid",
        symbol="BTC/USDT",
        lower_price=Decimal("49000"),
        upper_price=Decimal("51000"),
        grid_levels=5,
        total_quote=Decimal("500"),
    )
    assert isinstance(build_strategy(cfg), GridStrategy)


def test_builds_ma_crossover() -> None:
    cfg = MaCrossoverParams(
        strategy_type="ma_crossover",
        symbol="BTC/USDT",
        fast_period=5,
        slow_period=20,
        position_quote=Decimal("100"),
    )
    assert isinstance(build_strategy(cfg), MaCrossoverStrategy)


def test_unknown_strategy_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown strategy_type"):
        build_strategy(SimpleNamespace(strategy_type="nope"))  # type: ignore[arg-type]
