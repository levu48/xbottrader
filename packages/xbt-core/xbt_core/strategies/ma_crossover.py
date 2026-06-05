"""Moving-average crossover strategy.

Trend following on two simple moving averages of the close. A golden cross
(fast rises above slow) opens a long of ``position_quote`` worth; a death cross
(fast falls back below slow) closes it. One position at a time — no pyramiding,
no shorting.

State is a rolling window of the last ``slow_period`` closes plus the prior
fast-vs-slow relationship, so the same bars always produce the same signals
(backtest/live parity).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .base import Bar, OrderIntent, Strategy, StrategyState


@dataclass(frozen=True, slots=True)
class MaCrossoverParams:
    symbol: str
    fast_period: int
    slow_period: int
    position_quote: Decimal

    def __post_init__(self) -> None:
        if self.fast_period < 2:
            raise ValueError("fast_period must be >= 2")
        if self.slow_period <= self.fast_period:
            raise ValueError("slow_period must exceed fast_period")
        if self.position_quote <= 0:
            raise ValueError("position_quote must be positive")


class MaCrossoverStrategy(Strategy):
    def __init__(self, params: MaCrossoverParams) -> None:
        self._p = params

    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        if bar.symbol != self._p.symbol or bar.close <= 0:
            return []

        closes: list[Decimal] = state.custom.setdefault("closes", [])  # type: ignore[assignment]
        closes.append(bar.close)
        if len(closes) > self._p.slow_period:
            del closes[0]
        if len(closes) < self._p.slow_period:
            return []  # not enough history to compute the slow MA yet

        fast = sum(closes[-self._p.fast_period :]) / self._p.fast_period
        slow = sum(closes) / self._p.slow_period  # window length == slow_period
        is_above = fast > slow
        was_above = state.custom.get("was_above")
        in_position: bool = state.custom.get("in_position", False)  # type: ignore[assignment]

        intents: list[OrderIntent] = []
        if was_above is not None and is_above != was_above:
            if is_above and not in_position:
                qty = self._p.position_quote / bar.close
                state.custom["in_position"] = True
                state.custom["held_qty"] = qty
                intents.append(
                    OrderIntent(
                        symbol=self._p.symbol,
                        side="buy",
                        type="market",
                        quantity=qty,
                        client_tag=f"ma-cross-up:{bar.ts_ms}",
                    )
                )
            elif not is_above and in_position:
                qty: Decimal = state.custom["held_qty"]  # type: ignore[assignment]
                state.custom["in_position"] = False
                state.custom["held_qty"] = None
                intents.append(
                    OrderIntent(
                        symbol=self._p.symbol,
                        side="sell",
                        type="market",
                        quantity=qty,
                        client_tag=f"ma-cross-down:{bar.ts_ms}",
                    )
                )

        state.custom["was_above"] = is_above
        return intents
