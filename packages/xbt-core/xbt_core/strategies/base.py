"""Strategy base class. The same subclass runs in backtest and live execution.

A strategy receives market bars and emits :class:`OrderIntent` objects. It never
talks to an exchange directly — the runner (backtest harness or live supervisor)
translates intents into real orders. This is what makes backtest/live parity
testable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]


@dataclass(frozen=True, slots=True)
class Bar:
    ts_ms: int
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class OrderIntent:
    symbol: str
    side: Side
    type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    client_tag: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.type == "limit" and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.type == "market" and self.limit_price is not None:
            raise ValueError("market order must not set limit_price")


@dataclass(slots=True)
class StrategyState:
    """Mutable per-bot state that survives across bars within a single run."""

    last_action_ts_ms: int | None = None
    custom: dict[str, object] = field(default_factory=dict)


class Strategy(ABC):
    """Stateless strategy logic. State lives in :class:`StrategyState`.

    Implementations must be pure with respect to their inputs — same inputs,
    same outputs — so that backtest results match live behavior.
    """

    @abstractmethod
    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        """Return order intents triggered by this bar. May return []."""
