"""Pydantic models for the Bot Engine control plane.

The strategy-config models are shared with the AI Engine — they live in
``xbt_core.strategy_config`` and are re-exported here so the live and backtest
paths validate identical strategies. This module adds the Bot-Engine-only
models (risk limits, encrypted credentials, start request, responses).

These mirror the Zod schemas in packages/shared (see notes in §4 of the plan
about keeping them in sync via a JSON Schema diff in CI).
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

# Symbol shapes, validated per asset class (mirrors Symbol_ in packages/shared).
# Crypto: BASE/QUOTE (e.g. BTC/USDT). US equity: a bare ticker (e.g. AAPL, BRK.B).
_CRYPTO_SYMBOL_RE = re.compile(r"^[A-Z0-9]+/[A-Z0-9]+$")
_EQUITY_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def validate_symbol(exchange: str, symbol: str) -> None:
    """Raise ``ValueError`` unless ``symbol`` matches ``exchange``'s asset class.

    Shared by ``StartBotRequest`` and the market-data endpoint so the live and
    OHLCV paths accept identical symbols.
    """
    if asset_class_for(exchange) is AssetClass.US_EQUITY:
        if not _EQUITY_SYMBOL_RE.match(symbol):
            raise ValueError(
                f"{symbol!r} is not a valid equity ticker for {exchange!r} "
                "(expected e.g. AAPL)"
            )
    elif not _CRYPTO_SYMBOL_RE.match(symbol):
        raise ValueError(
            f"{symbol!r} is not a valid crypto pair for {exchange!r} "
            "(expected BASE/QUOTE, e.g. BTC/USDT)"
        )


class RiskLimits(BaseModel):
    """Per-bot risk controls. Enforced by the Supervisor's circuit breaker.

    ``max_loss_quote`` is an absolute loss cap in the strategy's quote currency:
    once the bot's mark-to-market PnL falls to ``-max_loss_quote`` it auto-pauses
    and cancels its open orders. ``None`` disables the breaker.
    """

    max_loss_quote: Decimal | None = Field(default=None, gt=0)


class EncryptedKeyEnvelope(BaseModel):
    """Envelope-encrypted exchange credentials, produced by the Gateway.

    Mirrors ``EncryptedEnvelope`` in app/security/keys.py. The Bot Engine is the
    only service that decrypts it; the plaintext is JSON
    ``{"apiKey","secret","password"?}``. Required for ``mode="live"``.
    """

    v: int
    dek_iv: str
    dek_ct: str
    data_iv: str
    data_ct: str


class StartBotRequest(BaseModel):
    strategy: StrategyConfig
    mode: Literal["paper", "live"] = "paper"
    # Venue id. Crypto: ccxt venue ("binance", "coinbase"). US equities: "alpaca"
    # (or "alpaca-paper"). Drives the adapter, market-data feed, and market-hours
    # session; ignored by the synthetic demo launcher.
    exchange: str = "binance"
    risk: RiskLimits = Field(default_factory=RiskLimits)
    credentials: EncryptedKeyEnvelope | None = None

    @model_validator(mode="after")
    def _symbol_matches_asset_class(self) -> "StartBotRequest":
        validate_symbol(self.exchange, self.strategy.symbol)
        return self


class BotStateResponse(BaseModel):
    bot_id: str
    state: str
    last_error: str | None = None


class KillAllResponse(BaseModel):
    killed: list[str]


class OhlcvResponse(BaseModel):
    """Public OHLCV candles for a symbol, newest-last.

    Each candle is a ccxt row ``[ts_ms, open, high, low, close, volume]``. Kept
    as a flat list of floats (not a model) so the payload stays compact and the
    front-end can hand it straight to lightweight-charts.
    """

    exchange: str
    symbol: str
    timeframe: str
    candles: list[list[float]]
