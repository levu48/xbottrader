"""Grid strategy.

Lays an evenly-spaced ladder of price lines between ``lower_price`` and
``upper_price``. As the mark falls through a line, buy one lot; as it rises
through a line, sell a lot back. Mean-reversion in a range: accumulate cheaper,
distribute dearer.

Within the bar-only :class:`Strategy` interface (no fill callbacks), we detect
the lines the close crossed since the previous bar and emit **market** orders
at the close. Two deliberate MVP simplifications:

- Fills are modelled at the bar close, not as resting limits exactly at the
  line — close enough for ranging bars; a limit-grid variant can come later.
- A simple LIFO lot stack prevents selling inventory we never bought (no
  shorting); precise per-line pairing and partial fills are deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .base import Bar, OrderIntent, Strategy, StrategyState


@dataclass(frozen=True, slots=True)
class GridParams:
    symbol: str
    lower_price: Decimal
    upper_price: Decimal
    grid_levels: int
    total_quote: Decimal

    def __post_init__(self) -> None:
        if self.lower_price <= 0:
            raise ValueError("lower_price must be positive")
        if self.upper_price <= self.lower_price:
            raise ValueError("upper_price must exceed lower_price")
        if self.grid_levels < 2:
            raise ValueError("grid_levels must be >= 2")
        if self.total_quote <= 0:
            raise ValueError("total_quote must be positive")

    @property
    def lines(self) -> list[Decimal]:
        """The grid price lines, low → high."""
        step = (self.upper_price - self.lower_price) / (self.grid_levels - 1)
        return [self.lower_price + step * i for i in range(self.grid_levels)]

    @property
    def quote_per_level(self) -> Decimal:
        """Notional allocated to each of the ``grid_levels - 1`` intervals."""
        return self.total_quote / (self.grid_levels - 1)


class GridStrategy(Strategy):
    def __init__(self, params: GridParams) -> None:
        self._p = params
        self._lines = params.lines

    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        if bar.symbol != self._p.symbol or bar.close <= 0:
            return []

        prev = state.custom.get("prev_close")
        lots: list[Decimal] = state.custom.setdefault("lots", [])  # type: ignore[assignment]
        curr = bar.close
        state.custom["prev_close"] = curr

        if prev is None:  # first bar — establish the reference, trade nothing
            return []

        intents: list[OrderIntent] = []
        if curr < prev:
            # Falling: buy a lot at each line crossed downward (high → low).
            for line in sorted(self._lines, reverse=True):
                if prev > line >= curr:
                    qty = self._p.quote_per_level / line
                    lots.append(qty)
                    intents.append(
                        OrderIntent(
                            symbol=self._p.symbol,
                            side="buy",
                            type="market",
                            quantity=qty,
                            client_tag=f"grid-buy:{line}",
                        )
                    )
        elif curr > prev:
            # Rising: sell a held lot at each line crossed upward (low → high).
            for line in sorted(self._lines):
                if prev < line <= curr and lots:
                    qty = lots.pop()
                    intents.append(
                        OrderIntent(
                            symbol=self._p.symbol,
                            side="sell",
                            type="market",
                            quantity=qty,
                            client_tag=f"grid-sell:{line}",
                        )
                    )
        return intents
