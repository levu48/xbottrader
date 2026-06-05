"""AI Engine FastAPI app factory.

Holds the singletons (Copilot, Backtester, InternalAuthenticator) and exposes
the copilot + backtest routes. Dependencies are injected so tests run the same
app with a fake Anthropic client and a fake data fetcher.
"""

from __future__ import annotations

from fastapi import FastAPI
from xbt_core.internal_auth import InternalAuthenticator

from .api.routes import router as ai_router
from .backtest.engine import Backtester
from .llm.copilot import Copilot


def create_app(
    *,
    internal_auth: InternalAuthenticator,
    copilot: Copilot,
    backtester: Backtester,
) -> FastAPI:
    app = FastAPI(title="xbt-ai-engine")
    app.state.internal_auth = internal_auth
    app.state.copilot = copilot
    app.state.backtester = backtester
    app.include_router(ai_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app
