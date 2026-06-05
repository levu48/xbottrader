"""Pydantic models for the Bot Engine control plane.

These mirror the Zod schemas in packages/shared (see notes in §4 of the plan
about keeping them in sync via a JSON Schema diff in CI).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator


class RiskLimits(BaseModel):
    """Per-bot risk controls. Enforced by the Supervisor's circuit breaker.

    ``max_loss_quote`` is an absolute loss cap in the strategy's quote currency:
    once the bot's mark-to-market PnL falls to ``-max_loss_quote`` it auto-pauses
    and cancels its open orders. ``None`` disables the breaker.
    """

    max_loss_quote: Decimal | None = Field(default=None, gt=0)


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

    @model_validator(mode="after")
    def _slow_exceeds_fast(self) -> "MaCrossoverParams":
        if self.slow_period <= self.fast_period:
            raise ValueError("slow_period must exceed fast_period")
        return self


StrategyConfig = Annotated[
    Union[DcaParams, GridParams, MaCrossoverParams],
    Field(discriminator="strategy_type"),
]


class StartBotRequest(BaseModel):
    strategy: StrategyConfig
    mode: Literal["paper", "live"] = "paper"
    risk: RiskLimits = Field(default_factory=RiskLimits)


class BotStateResponse(BaseModel):
    bot_id: str
    state: str
    last_error: str | None = None


class KillAllResponse(BaseModel):
    killed: list[str]
