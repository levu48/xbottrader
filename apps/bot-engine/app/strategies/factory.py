"""Map a validated API strategy config to a runtime :class:`Strategy`.

The launcher (demo and, later, production) calls :func:`build_strategy` so the
config-DTO → strategy translation lives in one place instead of being inlined
per launcher. New strategies plug in here.
"""

from __future__ import annotations

from ..api.schemas import StrategyConfig
from .base import Strategy
from .dca import DcaParams, DcaStrategy
from .grid import GridParams, GridStrategy
from .ma_crossover import MaCrossoverParams, MaCrossoverStrategy


def build_strategy(config: StrategyConfig) -> Strategy:
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
    raise ValueError(f"unknown strategy_type: {config.strategy_type!r}")
