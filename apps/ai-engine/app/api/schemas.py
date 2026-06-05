"""Pydantic models for the AI Engine API.

The strategy-config union mirrors the Bot Engine's (and the shared Zod schema);
it duck-types into ``xbt_core.strategies.factory.build_strategy`` so backtests
run the exact live strategy classes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator


# ----- strategy config (mirrors bot-engine / shared) -----


class DcaParams(BaseModel):
    strategy_type: Literal["dca"]
    symbol: str
    quote_amount: Decimal = Field(gt=0)
    interval_minutes: int = Field(gt=0)


class GridParams(BaseModel):
    strategy_type: Literal["grid"]
    symbol: str
    lower_price: Decimal = Field(gt=0)
    upper_price: Decimal = Field(gt=0)
    grid_levels: int = Field(ge=2, le=200)
    total_quote: Decimal = Field(gt=0)


class MaCrossoverParams(BaseModel):
    strategy_type: Literal["ma_crossover"]
    symbol: str
    fast_period: int = Field(ge=2)
    slow_period: int = Field(ge=3)
    position_quote: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _slow_exceeds_fast(self) -> "MaCrossoverParams":
        if self.slow_period <= self.fast_period:
            raise ValueError("slow_period must exceed fast_period")
        return self


StrategyConfig = Annotated[
    Union[DcaParams, GridParams, MaCrossoverParams],
    Field(discriminator="strategy_type"),
]


# ----- copilot -----


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CopilotChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    # Optional grounding context (e.g. a bot/PnL summary) prepended to the chat.
    context: str | None = None


class CopilotChatResponse(BaseModel):
    reply: str
    usage: dict[str, int] = Field(default_factory=dict)


# ----- backtest -----


class BacktestRequest(BaseModel):
    strategy: StrategyConfig
    timeframe: str = "1h"
    limit: int = Field(default=500, ge=2, le=1000)
    since_ms: int | None = None
    starting_cash: Decimal = Field(default=Decimal("10000"), gt=0)
    exchange: str = "kraken"


class EquityPoint(BaseModel):
    ts_ms: int
    equity: Decimal


class BacktestStats(BaseModel):
    starting_cash: Decimal
    final_equity: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    num_trades: int
    win_rate: Decimal


class BacktestResponse(BaseModel):
    symbol: str
    timeframe: str
    bars: int
    stats: BacktestStats
    equity_curve: list[EquityPoint]
