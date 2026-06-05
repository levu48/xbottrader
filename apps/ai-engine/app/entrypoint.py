"""uvicorn entrypoint. Wires the real Anthropic client + ccxt data fetcher.

Required env vars:
    GATEWAY_INTERNAL_HMAC_SECRET   shared HMAC secret with the Gateway
    ANTHROPIC_API_KEY              for the copilot
Optional:
    XBT_COPILOT_MODEL              default claude-sonnet-4-6
    XBT_BACKTEST_EXCHANGE          default kraken
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from xbt_core.internal_auth import InternalAuthenticator

from .backtest.data import build_ccxt_data_client
from .backtest.engine import Backtester
from .llm.copilot import DEFAULT_MODEL, Copilot, build_anthropic_complete
from .main import create_app


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} env var must be set")
    return v


def build_app() -> FastAPI:
    secret = _require_env("GATEWAY_INTERNAL_HMAC_SECRET")
    api_key = _require_env("ANTHROPIC_API_KEY")
    model = os.environ.get("XBT_COPILOT_MODEL", DEFAULT_MODEL)
    exchange = os.environ.get("XBT_BACKTEST_EXCHANGE", "kraken")

    auth = InternalAuthenticator.from_env(secret)
    copilot = Copilot(build_anthropic_complete(api_key), model=model)
    backtester = Backtester(build_ccxt_data_client, default_exchange=exchange)

    return create_app(internal_auth=auth, copilot=copilot, backtester=backtester)


app = build_app()
