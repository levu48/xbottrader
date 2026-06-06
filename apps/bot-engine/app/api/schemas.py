"""Pydantic models for the Bot Engine control plane.

The strategy-config models are shared with the AI Engine — they live in
``xbt_core.strategy_config`` and are re-exported here so the live and backtest
paths validate identical strategies. This module adds the Bot-Engine-only
models (risk limits, encrypted credentials, start request, responses).

These mirror the Zod schemas in packages/shared (see notes in §4 of the plan
about keeping them in sync via a JSON Schema diff in CI).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

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
    # ccxt venue id (e.g. "binance", "coinbase"). Drives the live client and the
    # public market-data feed; ignored by the synthetic demo launcher.
    exchange: str = "binance"
    risk: RiskLimits = Field(default_factory=RiskLimits)
    credentials: EncryptedKeyEnvelope | None = None


class BotStateResponse(BaseModel):
    bot_id: str
    state: str
    last_error: str | None = None


class KillAllResponse(BaseModel):
    killed: list[str]
