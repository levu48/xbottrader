"""Pydantic models for the Bot Engine control plane.

These mirror the Zod schemas in packages/shared (see notes in §4 of the plan
about keeping them in sync via a JSON Schema diff in CI).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class DcaParams(BaseModel):
    strategy_type: Literal["dca"]
    symbol: str
    quote_amount: Decimal = Field(gt=0)
    interval_minutes: int = Field(gt=0)


# Grid + MA params are stubbed; they'll be filled in alongside their strategy
# implementations. Including them now keeps the discriminator stable.
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


StrategyConfig = Annotated[
    Union[DcaParams, GridParams, MaCrossoverParams],
    Field(discriminator="strategy_type"),
]


class StartBotRequest(BaseModel):
    strategy: StrategyConfig
    mode: Literal["paper", "live"] = "paper"


class BotStateResponse(BaseModel):
    bot_id: str
    state: str
    last_error: str | None = None
