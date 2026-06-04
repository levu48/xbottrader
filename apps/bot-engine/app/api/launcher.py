"""Bridge between an HTTP start request and the Supervisor.

Production launcher reads the bot's row from Postgres, decrypts the user's
exchange API key, instantiates ccxt, and wires a live BarSource. Test
launcher returns a paper setup with an injected finite bar list. The
:class:`BotLauncher` Protocol is the only thing the API depends on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..runtime.supervisor import BarSource, OrderRouter
from ..strategies.base import Strategy


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    strategy: Strategy
    bars: BarSource
    router: OrderRouter


class BotLauncher(Protocol):
    async def launch(
        self,
        *,
        bot_id: str,
        user_id: str,
        request: object,
    ) -> LaunchPlan:
        ...
