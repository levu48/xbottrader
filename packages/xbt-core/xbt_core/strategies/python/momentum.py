"""Reference operator strategy: rate-of-change momentum.

Copy this file as the template for a new Python strategy. It tracks the percent
change of the close over a trailing ``lookback`` window: a rise of at least
``threshold_pct`` opens a long of ``quote_amount`` worth; a fall of at least
``threshold_pct`` from the window's start closes it. One position at a time.

Pure by construction — state is a bounded rolling window in ``state.custom`` plus
a position flag, so the same bars always yield the same intents (backtest/live
parity). No I/O, no wall-clock, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from ..base import Bar, OrderIntent, Strategy, StrategyState
from ..python_registry import register


@dataclass(frozen=True, slots=True)
class MomentumParams:
    symbol: str
    lookback: int
    threshold_pct: Decimal  # e.g. Decimal("5") == 5%
    quote_amount: Decimal

    def __post_init__(self) -> None:
        if self.lookback < 1:
            raise ValueError("lookback must be >= 1")
        if self.threshold_pct <= 0:
            raise ValueError("threshold_pct must be positive")
        if self.quote_amount <= 0:
            raise ValueError("quote_amount must be positive")

    @classmethod
    def from_params(cls, params: Mapping[str, object]) -> "MomentumParams":
        """Validate/coerce the free-form ``params`` dict. The typed boundary for
        otherwise-opaque transport params — bad values raise ``ValueError``."""
        return cls(
            symbol=str(params["symbol"]),
            lookback=int(params["lookback"]),
            threshold_pct=Decimal(str(params["threshold_pct"])),
            quote_amount=Decimal(str(params["quote_amount"])),
        )


class MomentumStrategy(Strategy):
    def __init__(self, params: MomentumParams) -> None:
        self._p = params

    @classmethod
    def from_params(cls, params: Mapping[str, object]) -> "MomentumStrategy":
        return cls(MomentumParams.from_params(params))

    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        if bar.symbol != self._p.symbol or bar.close <= 0:
            return []

        closes: list[Decimal] = state.custom.setdefault("closes", [])  # type: ignore[assignment]
        closes.append(bar.close)
        if len(closes) > self._p.lookback + 1:
            del closes[0]
        if len(closes) < self._p.lookback + 1:
            return []  # not enough history to measure the change yet

        ref = closes[0]
        if ref <= 0:
            return []
        change_pct = (bar.close - ref) / ref * Decimal(100)
        in_position: bool = state.custom.get("in_position", False)  # type: ignore[assignment]

        if not in_position and change_pct >= self._p.threshold_pct:
            qty = self._p.quote_amount / bar.close
            state.custom["in_position"] = True
            state.custom["held_qty"] = qty
            return [
                OrderIntent(
                    symbol=self._p.symbol,
                    side="buy",
                    type="market",
                    quantity=qty,
                    client_tag=f"momentum-up:{bar.ts_ms}",
                )
            ]
        if in_position and change_pct <= -self._p.threshold_pct:
            qty: Decimal = state.custom["held_qty"]  # type: ignore[assignment]
            state.custom["in_position"] = False
            state.custom["held_qty"] = None
            return [
                OrderIntent(
                    symbol=self._p.symbol,
                    side="sell",
                    type="market",
                    quantity=qty,
                    client_tag=f"momentum-down:{bar.ts_ms}",
                )
            ]
        return []


register("momentum")(MomentumStrategy)
