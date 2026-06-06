"""Shared pydantic strategy-config schema for the control plane.

The Bot Engine (live) and AI Engine (backtest) both validate the *same*
``StrategyConfig`` before handing it to :func:`xbt_core.strategies.factory.
build_strategy`. Keeping the models here — instead of duplicating them in each
app — guarantees the two services accept exactly the same strategies, which is
the whole point of backtest/live parity.

This module requires pydantic (declared as the ``schemas`` optional dependency
of xbt-core). The pure strategy domain — ``xbt_core.strategies.*`` — must NOT
import it, so that the runtime classes stay dependency-free; ``build_strategy``
duck-types these models via ``model_dump`` rather than importing them.

Mirrored by the Zod ``StrategyConfig`` in ``packages/shared/src/bot.ts``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------- #
# Built-in strategies
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Custom rule-engine strategy (user-authored logic as data).
# The recursive Condition / Rule shape mirrors the DSL parsed by
# xbt_core.strategies.rule_engine; this layer validates structure only.
# --------------------------------------------------------------------------- #
_COMPARATORS = {"<", "<=", ">", ">=", "==", "crossover", "crossunder"}
_BOOL_OPS = {"and", "or", "not"}
_RESERVED_OPERANDS = {"price"}  # current bar close — always available


class IndicatorSpec(BaseModel):
    name: str = Field(min_length=1)
    fn: Literal["price", "value", "sma", "rsi"]
    period: int = Field(default=0, ge=0)
    value: Decimal = Decimal(0)

    @model_validator(mode="after")
    def _period_for_windowed(self) -> "IndicatorSpec":
        if self.fn in ("sma", "rsi") and self.period < 2:
            raise ValueError(f"{self.fn} requires period >= 2")
        return self


class Condition(BaseModel):
    op: str
    # Comparison form:
    left: str | None = None
    right: str | None = None
    # Boolean form:
    terms: list["Condition"] | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Condition":
        if self.op in _BOOL_OPS:
            if not self.terms:
                raise ValueError(f"'{self.op}' requires terms")
            if self.op == "not" and len(self.terms) != 1:
                raise ValueError("'not' takes exactly one term")
        elif self.op in _COMPARATORS:
            if self.left is None or self.right is None:
                raise ValueError(f"'{self.op}' requires left and right")
        else:
            raise ValueError(f"unknown condition op: {self.op!r}")
        return self


class ActionSpec(BaseModel):
    side: Literal["buy", "sell"]
    type: Literal["market", "limit"] = "market"
    quote: Decimal = Field(gt=0)
    limit_offset_pct: Decimal | None = None

    @model_validator(mode="after")
    def _limit_needs_offset(self) -> "ActionSpec":
        if self.type == "limit" and self.limit_offset_pct is None:
            raise ValueError("limit action requires limit_offset_pct")
        return self


class RuleSpec(BaseModel):
    when: Condition
    do: ActionSpec
    cooldown_minutes: int = Field(default=0, ge=0)


class CustomRulesParams(BaseModel):
    strategy_type: Literal["custom_rules"]
    symbol: str = Field(min_length=1)
    indicators: list[IndicatorSpec] = Field(default_factory=list)
    rules: list[RuleSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _names_resolve(self) -> "CustomRulesParams":
        names = {i.name for i in self.indicators}
        if len(names) != len(self.indicators):
            raise ValueError("indicator names must be unique")
        if names & _RESERVED_OPERANDS:
            raise ValueError(f"indicator name is reserved: {names & _RESERVED_OPERANDS}")
        known = names | _RESERVED_OPERANDS
        for rule in self.rules:
            for tok in _referenced_tokens(rule.when):
                if tok not in known and not _is_number(tok):
                    raise ValueError(f"rule references unknown indicator: {tok!r}")
        return self


def _is_number(tok: str) -> bool:
    try:
        Decimal(tok)
        return True
    except Exception:
        return False


def _referenced_tokens(cond: Condition) -> set[str]:
    if cond.terms:
        out: set[str] = set()
        for t in cond.terms:
            out |= _referenced_tokens(t)
        return out
    return {t for t in (cond.left, cond.right) if t is not None}


StrategyConfig = Annotated[
    Union[DcaParams, GridParams, MaCrossoverParams, CustomRulesParams],
    Field(discriminator="strategy_type"),
]
