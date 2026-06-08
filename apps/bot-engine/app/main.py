"""Bot Engine FastAPI app factory.

Owns the singletons (Supervisor, EventPublisher, InternalAuthenticator,
BotLauncher) and exposes the control-plane routes. The launcher is injected
so tests run the same app with a paper setup.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from redis.asyncio import Redis as AsyncRedis

from typing import Any, Callable

from .api.auth import InternalAuthenticator
from .api.bots import router as bots_router
from .api.launcher import BotLauncher
from .api.market import router as market_router
from .events.publisher import EventPublisher
from .exchanges.ccxt_live import build_public_ccxt_client
from .runtime.supervisor import Supervisor


def create_app(
    *,
    launcher: BotLauncher,
    publisher: EventPublisher,
    internal_auth: InternalAuthenticator,
    public_client_factory: Callable[[str], Any] = build_public_ccxt_client,
) -> FastAPI:
    supervisor = Supervisor(publisher)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        for bot_id in list(supervisor.running_bots()):
            await supervisor.stop(bot_id)

    app = FastAPI(title="xbt-bot-engine", lifespan=lifespan)
    app.state.supervisor = supervisor
    app.state.launcher = launcher
    app.state.internal_auth = internal_auth
    app.state.public_client_factory = public_client_factory
    app.include_router(bots_router)
    app.include_router(market_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


def main() -> FastAPI:
    """Production entrypoint. Wires real Redis + (TODO) real launcher."""
    redis_url = os.environ["REDIS_URL"]
    secret = os.environ["GATEWAY_INTERNAL_HMAC_SECRET"]
    redis = AsyncRedis.from_url(redis_url)
    publisher = EventPublisher(redis)
    auth = InternalAuthenticator.from_env(secret)

    # Production: read bot row from Postgres, decrypt API key, build ccxt client,
    # construct a live BarSource. Stub for now to keep the scaffold honest.
    raise NotImplementedError("production launcher not implemented yet")


# uvicorn entry-point: `uvicorn app.main:app` would expect a module-level `app`.
# Production wiring will create one here once the launcher exists.
