from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.schemas import CustomRulesParams
from app.strategies.base import Bar, OrderIntent, StrategyState
from app.strategies.factory import build_strategy
from app.strategies.rule_engine import RuleEngineParams, RuleEngineStrategy


def _bar(ts_ms: int, close: str, symbol: str = "BTC/USDT") -> Bar:
    p = Decimal(close)
    return Bar(ts_ms=ts_ms, symbol=symbol, open=p, high=p, low=p, close=p, volume=Decimal("0"))


def _feed(
    s: RuleEngineStrategy, st: StrategyState, bars: list[tuple[int, str]]
) -> list[list[OrderIntent]]:
    return [s.on_bar(_bar(ts, c), st) for ts, c in bars]


def _crossover_dict() -> dict:
    return {
        "symbol": "BTC/USDT",
        "indicators": [
            {"name": "fast", "fn": "sma", "period": 2},
            {"name": "slow", "fn": "sma", "period": 3},
        ],
        "rules": [
            {"when": {"op": "crossover", "left": "fast", "right": "slow"},
             "do": {"side": "buy", "type": "market", "quote": "100"}},
        ],
    }


def test_crossover_rule_fires_buy_on_golden_cross() -> None:
    s = RuleEngineStrategy(RuleEngineParams.from_dict(_crossover_dict()))
    st = StrategyState()
    # bars 0..2 hold fast==slow; bar 3 lifts the fast SMA above the slow.
    out = _feed(s, st, [(0, "10"), (1, "10"), (2, "10"), (3, "13")])
    assert out[0] == [] and out[1] == [] and out[2] == []
    assert out[3] and out[3][0].side == "buy"
    assert out[3][0].quantity == Decimal("100") / Decimal("13")


def test_level_condition_is_edge_triggered() -> None:
    cfg = {
        "symbol": "BTC/USDT",
        "indicators": [{"name": "th", "fn": "value", "value": "12"}],
        "rules": [
            {"when": {"op": ">", "left": "price", "right": "th"},
             "do": {"side": "buy", "quote": "50"}},
        ],
    }
    s = RuleEngineStrategy(RuleEngineParams.from_dict(cfg))
    st = StrategyState()
    out = _feed(s, st, [(0, "10"), (1, "13"), (2, "14")])
    assert out[0] == []          # below threshold
    assert out[1] and out[1][0].side == "buy"  # crossing up → fire once
    assert out[2] == []          # stays above → no repeat


def test_cooldown_suppresses_refire() -> None:
    cfg = {
        "symbol": "BTC/USDT",
        "indicators": [{"name": "th", "fn": "value", "value": "12"}],
        "rules": [
            {"when": {"op": ">", "left": "price", "right": "th"},
             "do": {"side": "buy", "quote": "50"}, "cooldown_minutes": 60},
        ],
    }
    s = RuleEngineStrategy(RuleEngineParams.from_dict(cfg))
    st = StrategyState()
    # fire at t=0; drop below; rise again at t=20min (< 60min cooldown) → suppressed.
    out = _feed(s, st, [(0, "13"), (600_000, "10"), (1_200_000, "13")])
    fires = [o for o in out if o]
    assert len(fires) == 1


def test_factory_builds_from_pydantic_config() -> None:
    cfg = CustomRulesParams.model_validate({"strategy_type": "custom_rules", **_crossover_dict()})
    s = build_strategy(cfg)
    assert isinstance(s, RuleEngineStrategy)
    # And it actually runs end-to-end off the pydantic-sourced config.
    st = StrategyState()
    out = [s.on_bar(_bar(i, c), st) for i, c in enumerate(["10", "10", "10", "13"])]
    assert out[3] and out[3][0].side == "buy"


def test_pydantic_rejects_unknown_indicator_reference() -> None:
    with pytest.raises(ValidationError, match="unknown indicator"):
        CustomRulesParams.model_validate({
            "strategy_type": "custom_rules",
            "symbol": "BTC/USDT",
            "indicators": [{"name": "fast", "fn": "sma", "period": 2}],
            "rules": [{"when": {"op": ">", "left": "fast", "right": "slow"},
                       "do": {"side": "buy", "quote": "1"}}],
        })
