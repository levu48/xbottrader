"""Pydantic models for the AI Engine API.

The strategy-config models are shared with the Bot Engine — they live in
``xbt_core.strategy_config`` and are re-exported here. They duck-type into
``xbt_core.strategies.factory.build_strategy`` so backtests run the exact live
strategy classes, against the exact same validated config the live path uses.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from xbt_core.market.session import AssetClass, asset_class_for

# Re-exported so existing `from app.api.schemas import DcaParams, ...` keep working.
from xbt_core.strategy_config import (  # noqa: F401
    ActionSpec,
    Condition,
    CustomRulesParams,
    DcaParams,
    GridParams,
    IndicatorSpec,
    MaCrossoverParams,
    RuleSpec,
    StrategyConfig,
)


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


# Symbol shapes per asset class (mirrors Symbol_ in packages/shared and the
# bot-engine StartBotRequest validator).
_CRYPTO_SYMBOL_RE = re.compile(r"^[A-Z0-9]+/[A-Z0-9]+$")
_EQUITY_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


class BacktestRequest(BaseModel):
    strategy: StrategyConfig
    timeframe: str = "1h"
    limit: int = Field(default=500, ge=2, le=1000)
    since_ms: int | None = None
    starting_cash: Decimal = Field(default=Decimal("10000"), gt=0)
    exchange: str = "kraken"

    @model_validator(mode="after")
    def _symbol_matches_asset_class(self) -> "BacktestRequest":
        symbol = self.strategy.symbol
        if asset_class_for(self.exchange) is AssetClass.US_EQUITY:
            if not _EQUITY_SYMBOL_RE.match(symbol):
                raise ValueError(f"{symbol!r} is not a valid equity ticker (e.g. AAPL)")
        elif not _CRYPTO_SYMBOL_RE.match(symbol):
            raise ValueError(f"{symbol!r} is not a valid crypto pair (e.g. BTC/USDT)")
        return self


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
