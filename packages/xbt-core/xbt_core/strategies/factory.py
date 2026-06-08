"""Map a validated strategy config to a runtime :class:`Strategy`.

The config is duck-typed: any object exposing ``strategy_type`` plus the fields
for that type works — the Bot Engine passes its pydantic ``StrategyConfig``, a
backtest request can pass a dataclass or namespace. Keeping this here (not tied
to any one app's schema) lets both the live launcher and the backtester build
strategies the same way. New strategies plug in here.
"""

from __future__ import annotations

from typing import Any, Protocol

from .ai_signal import AiSignalParams, AiSignalStrategy
from .base import Strategy
from .dca import DcaParams, DcaStrategy
from .grid import GridParams, GridStrategy
from .ma_crossover import MaCrossoverParams, MaCrossoverStrategy
from .rule_engine import RuleEngineParams, RuleEngineStrategy


class StrategyConfigLike(Protocol):
    strategy_type: str


def build_strategy(config: StrategyConfigLike | Any) -> Strategy:
    if config.strategy_type == "dca":
        return DcaStrategy(
            DcaParams(
                symbol=config.symbol,
                quote_amount=config.quote_amount,
                interval_minutes=config.interval_minutes,
            )
        )
    if config.strategy_type == "grid":
        return GridStrategy(
            GridParams(
                symbol=config.symbol,
                lower_price=config.lower_price,
                upper_price=config.upper_price,
                grid_levels=config.grid_levels,
                total_quote=config.total_quote,
            )
        )
    if config.strategy_type == "ma_crossover":
        return MaCrossoverStrategy(
            MaCrossoverParams(
                symbol=config.symbol,
                fast_period=config.fast_period,
                slow_period=config.slow_period,
                position_quote=config.position_quote,
            )
        )
    if config.strategy_type == "custom_rules":
        # Nested config (indicators/rules) is normalized from the duck-typed
        # object inside from_config — works for pydantic, dataclass, or dict.
        return RuleEngineStrategy(RuleEngineParams.from_config(config))
    if config.strategy_type == "ai_signal":
        # The pure strategy only reads decisions from state; the LLM client that
        # produces them is wired by the launcher's companion task, not here.
        return AiSignalStrategy(
            AiSignalParams(
                symbol=config.symbol,
                quote_amount=config.quote_amount,
                decision_interval_minutes=config.decision_interval_minutes,
                lookback_bars=getattr(config, "lookback_bars", 50),
                guidance=getattr(config, "guidance", None),
                model=getattr(config, "model", None),
            )
        )
    raise ValueError(f"unknown strategy_type: {config.strategy_type!r}")
