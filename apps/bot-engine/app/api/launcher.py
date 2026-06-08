"""Bridge between an HTTP start request and the Supervisor.

Production launcher reads the bot's row from Postgres, decrypts the user's
exchange API key, instantiates ccxt, and wires a live BarSource. Test
launcher returns a paper setup with an injected finite bar list. The
:class:`BotLauncher` Protocol is the only thing the API depends on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from xbt_core.market.session import MarketSession

from ..runtime.supervisor import BarSource, OrderRouter
from ..strategies.base import Strategy


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    strategy: Strategy
    bars: BarSource
    router: OrderRouter
    # Market-hours session for this bot's venue. None = always-open (crypto).
    session: MarketSession | None = None
    # Optional max age (ms) for a trusted mark; see Supervisor._tripped.
    max_mark_age_ms: int | None = None


class BotLauncher(Protocol):
    async def launch(
        self,
        *,
        bot_id: str,
        user_id: str,
        request: object,
    ) -> LaunchPlan:
        ...
