"""Dollar-cost averaging strategy.

Every ``interval_minutes`` of elapsed bar time, buy ``quote_amount`` worth of
``symbol`` at the market. The simplest possible strategy — exists to exercise
the full pipeline (signals → orders → fills → PnL → reporting).

The interval is measured against bar timestamps (wall-clock). For an equity bot,
a market-closed gap (overnight/weekend) simply elapses, so the interval will have
passed by the first bar of the next session and the bot buys then — i.e. "every N
minutes of market time, and at least once per session." Trading-time-aware
intervals are a deferred refinement.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .base import Bar, OrderIntent, Strategy, StrategyState


@dataclass(frozen=True, slots=True)
class DcaParams:
    symbol: str
    quote_amount: Decimal
    interval_minutes: int

    def __post_init__(self) -> None:
        if self.quote_amount <= 0:
            raise ValueError("quote_amount must be positive")
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")


class DcaStrategy(Strategy):
    def __init__(self, params: DcaParams) -> None:
        self._p = params

    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        if bar.symbol != self._p.symbol:
            return []

        interval_ms = self._p.interval_minutes * 60_000
        if state.last_action_ts_ms is not None and bar.ts_ms - state.last_action_ts_ms < interval_ms:
            return []

        if bar.close <= 0:
            return []
        qty = self._p.quote_amount / bar.close
        state.last_action_ts_ms = bar.ts_ms

        return [
            OrderIntent(
                symbol=self._p.symbol,
                side="buy",
                type="market",
                quantity=qty,
                client_tag=f"dca:{bar.ts_ms}",
            )
        ]
