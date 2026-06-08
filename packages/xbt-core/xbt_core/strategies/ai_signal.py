"""AI-signal strategy — an LLM decides buy/sell/hold, the strategy executes it.

Unlike every other strategy here, this one is NOT a pure function of the bar
stream: the decision comes from a language model. To keep ``on_bar`` pure and
synchronous (the parity/runtime contract every strategy shares), the LLM call is
made *outside* this class — by an async companion task in the Bot Engine — which
writes its verdict into a slot in :class:`StrategyState`. ``on_bar`` only:

1. appends the close to a bounded rolling window (the window the companion sends
   to the model — kept here so there is one source of truth for it), and
2. acts on the latest *unconsumed* decision in that slot.

Because the model never sizes trades, sizing lives in the config: a ``buy`` spends
``quote_amount`` of notional; a ``sell`` unwinds the position this strategy has
itself accumulated (tracked in state, so it can never oversell). A ``hold`` — or
no fresh decision — emits nothing.

The decision *cadence* is owned by the companion (it calls the model every
``decision_interval_minutes``); ``on_bar`` simply consumes whatever fresh verdict
is present, which naturally rate-limits actions to that cadence.

This strategy is intentionally not backtestable — see the AI Engine backtester,
which rejects it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .base import Bar, OrderIntent, Strategy, StrategyState

# Slot in ``StrategyState.custom`` the companion task writes and ``on_bar`` reads.
# Shape: {"seq": int, "action": "buy"|"sell"|"hold", "reason": str, "ts_ms": int}.
# Exported so the Bot Engine poller writes the exact key this strategy reads.
SIGNAL_SLOT = "ai_signal"
# Bounded rolling window of closes the companion reads to build its prompt.
WINDOW_SLOT = "ai_closes"


@dataclass(frozen=True, slots=True)
class AiSignalParams:
    symbol: str
    quote_amount: Decimal
    decision_interval_minutes: int
    lookback_bars: int = 50
    guidance: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.quote_amount <= 0:
            raise ValueError("quote_amount must be positive")
        if self.decision_interval_minutes < 1:
            raise ValueError("decision_interval_minutes must be >= 1")
        if self.lookback_bars < 2:
            raise ValueError("lookback_bars must be >= 2")


class AiSignalStrategy(Strategy):
    def __init__(self, params: AiSignalParams) -> None:
        self._p = params

    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        if bar.symbol != self._p.symbol or bar.close <= 0:
            return []

        # Maintain the bounded window the companion sends to the model. Kept here
        # so the window definition (size = lookback_bars) has a single owner.
        closes: list[Decimal] = state.custom.setdefault(WINDOW_SLOT, [])  # type: ignore[assignment]
        closes.append(bar.close)
        if len(closes) > self._p.lookback_bars:
            del closes[0 : len(closes) - self._p.lookback_bars]

        signal = state.custom.get(SIGNAL_SLOT)
        if not isinstance(signal, dict):
            return []  # no decision yet
        seq = signal.get("seq")
        if not isinstance(seq, int) or seq <= int(state.custom.get("ai_consumed_seq", 0)):  # type: ignore[arg-type]
            return []  # already acted on this verdict (or malformed)

        # Mark consumed regardless of action so a 'hold' is not re-evaluated.
        state.custom["ai_consumed_seq"] = seq
        action = signal.get("action")
        held: Decimal = state.custom.get("ai_held_qty", Decimal(0))  # type: ignore[assignment]

        if action == "buy":
            qty = self._p.quote_amount / bar.close
            state.custom["ai_held_qty"] = held + qty
            state.last_action_ts_ms = bar.ts_ms
            return [
                OrderIntent(
                    symbol=self._p.symbol,
                    side="buy",
                    type="market",
                    quantity=qty,
                    client_tag=f"ai:{seq}",
                )
            ]
        if action == "sell" and held > 0:
            state.custom["ai_held_qty"] = Decimal(0)
            state.last_action_ts_ms = bar.ts_ms
            return [
                OrderIntent(
                    symbol=self._p.symbol,
                    side="sell",
                    type="market",
                    quantity=held,
                    client_tag=f"ai:{seq}",
                )
            ]
        return []  # hold, or sell with nothing held
