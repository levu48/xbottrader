"""The async companion that drives an ai_signal bot.

The :class:`AiSignalStrategy` is a pure, synchronous ``on_bar`` that only *reads*
decisions from its :class:`StrategyState`; it never calls the model. This task is
the other half: on the bot's ``decision_interval_minutes`` cadence it snapshots
the rolling window the strategy maintains, asks the AI Engine for a buy/sell/hold
verdict, and writes that verdict into the slot ``on_bar`` consumes.

It runs as a sibling of the bot's bar loop under the Supervisor, sharing the same
:class:`StrategyState` instance, and is cancelled when the bot stops. Because the
event loop is single-threaded, snapshotting the window/position synchronously
(before any ``await``) is a consistent read against the bar loop's mutations.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from xbt_core.strategies.ai_signal import SIGNAL_SLOT, WINDOW_SLOT
from xbt_core.strategies.base import StrategyState

from ..clients.ai_engine import AiEngineClient
from ..events.publisher import Event

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _utc_date() -> date:
    return datetime.now(UTC).date()


class PublisherLike(Protocol):
    async def publish(self, event: Event) -> object:
        ...


class AiSignalCompanion:
    """Polls the AI Engine and publishes decisions into the strategy's state."""

    def __init__(
        self,
        *,
        client: AiEngineClient,
        user_id: str,
        bot_id: str,
        symbol: str,
        decision_interval_minutes: int,
        guidance: str | None,
        model: str | None,
        publisher: PublisherLike | None = None,
        max_daily_consults: int | None = None,
    ) -> None:
        self._client = client
        self._user_id = user_id
        self._bot_id = bot_id
        self._symbol = symbol
        self._interval_s = decision_interval_minutes * 60
        self._guidance = guidance
        self._model = model
        self._publisher = publisher
        # Cost cap: at most this many model consults per UTC day (None = unlimited).
        self._max_daily_consults = max_daily_consults

    async def run(self, state: StrategyState) -> None:
        seq = 0
        # Per-UTC-day spend cap state. `flagged` makes the budget-exhausted event
        # fire once per day, not every tick after the cap is hit.
        day: date | None = None
        consults = 0
        flagged = False
        while True:
            # Decide AFTER the first interval so the window has bars to reason over;
            # the strategy fills WINDOW_SLOT as bars arrive.
            await asyncio.sleep(self._interval_s)

            today = _utc_date()
            if today != day:
                day, consults, flagged = today, 0, False

            # Synchronous snapshot — consistent vs. the bar loop (no await between).
            raw = state.custom.get(WINDOW_SLOT, [])
            closes = [Decimal(str(c)) for c in raw] if isinstance(raw, list) else []
            position = state.custom.get("ai_held_qty", Decimal(0))
            if not isinstance(position, Decimal):
                position = Decimal(str(position))
            if len(closes) < 2:
                continue  # not enough history yet — skip this tick, don't burn a call

            if self._max_daily_consults is not None and consults >= self._max_daily_consults:
                # Cap hit: stop consulting (no more cost) until the day rolls over.
                # Flag it once so the user sees why decisions paused.
                if not flagged:
                    flagged = True
                    logger.warning(
                        "ai_signal bot %s hit daily consult cap (%d) — pausing consults until %s+1",
                        self._bot_id, self._max_daily_consults, today,
                    )
                    await self._emit(
                        "ai_budget_exhausted",
                        {
                            "date": today.isoformat(),
                            "consults": consults,
                            "max_daily_consults": self._max_daily_consults,
                        },
                    )
                continue

            try:
                decision = await self._client.signal(
                    user_id=self._user_id,
                    symbol=self._symbol,
                    closes=closes,
                    position=position,
                    guidance=self._guidance,
                    model=self._model,
                )
            except Exception:
                # A failed consult must never kill the bot — log and try next tick.
                logger.exception("ai_signal consult failed for bot %s", self._bot_id)
                continue

            consults += 1
            seq += 1
            state.custom[SIGNAL_SLOT] = {
                "seq": seq,
                "action": decision.action,
                "reason": decision.reason,
                "ts_ms": _now_ms(),
            }
            await self._emit(
                "ai_decision",
                {
                    "seq": seq,
                    "action": decision.action,
                    "reason": decision.reason,
                    "usage": decision.usage,
                    "consults_today": consults,
                },
            )

    async def _emit(self, event_type: str, payload: dict[str, object]) -> None:
        if self._publisher is not None:
            await self._publisher.publish(
                Event(event_type, self._user_id, self._bot_id, payload)
            )


def build_ai_companion(
    *,
    ai_client: AiEngineClient | None,
    params: object,
    user_id: str,
    bot_id: str,
    publisher: PublisherLike | None = None,
) -> AiSignalCompanion | None:
    """Build the companion for an ai_signal strategy, else return None.

    Raises if an ai_signal bot is launched without a configured AI Engine — better
    a clear 400 at start than a bot that silently never trades.
    """
    if getattr(params, "strategy_type", None) != "ai_signal":
        return None
    if ai_client is None:
        raise ValueError(
            "ai_signal strategy requires the AI Engine — set AI_ENGINE_URL on the Bot Engine"
        )
    return AiSignalCompanion(
        client=ai_client,
        user_id=user_id,
        bot_id=bot_id,
        symbol=params.symbol,  # type: ignore[attr-defined]
        decision_interval_minutes=params.decision_interval_minutes,  # type: ignore[attr-defined]
        guidance=getattr(params, "guidance", None),
        model=getattr(params, "model", None),
        publisher=publisher,
        max_daily_consults=ai_client.max_daily_consults,
    )
