"""uvicorn entrypoint. Wires the real Anthropic client + ccxt data fetcher.

Required env vars:
    GATEWAY_INTERNAL_HMAC_SECRET   shared HMAC secret with the Gateway
    ANTHROPIC_API_KEY              for the copilot
Optional:
    XBT_COPILOT_MODEL              default claude-sonnet-4-6
    XBT_AUTHOR_MODEL               strategy author; default claude-sonnet-4-6
    XBT_SIGNAL_MODEL               ai_signal advisor; default claude-sonnet-4-6
    XBT_BACKTEST_EXCHANGE          default kraken
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from xbt_core.internal_auth import InternalAuthenticator

from .backtest.data import build_ccxt_data_client
from .backtest.engine import Backtester
from .llm.author import StrategyAuthor, build_anthropic_tool_complete
from .llm.author import DEFAULT_MODEL as AUTHOR_DEFAULT_MODEL
from .llm.copilot import DEFAULT_MODEL, Copilot, build_anthropic_complete
from .llm.signal import DEFAULT_MODEL as SIGNAL_DEFAULT_MODEL
from .llm.signal import SignalAdvisor
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
    author_model = os.environ.get("XBT_AUTHOR_MODEL", AUTHOR_DEFAULT_MODEL)
    signal_model = os.environ.get("XBT_SIGNAL_MODEL", SIGNAL_DEFAULT_MODEL)
    exchange = os.environ.get("XBT_BACKTEST_EXCHANGE", "kraken")

    auth = InternalAuthenticator.from_env(secret)
    copilot = Copilot(build_anthropic_complete(api_key), model=model)
    backtester = Backtester(build_ccxt_data_client, default_exchange=exchange)
    # Both the author and the signal advisor force a structured tool call; they
    # share one ToolCompleteFn wrapping the Anthropic SDK.
    tool_complete = build_anthropic_tool_complete(api_key)
    author = StrategyAuthor(tool_complete, model=author_model)
    signal_advisor = SignalAdvisor(tool_complete, model=signal_model)

    return create_app(
        internal_auth=auth,
        copilot=copilot,
        backtester=backtester,
        author=author,
        signal_advisor=signal_advisor,
    )


app = build_app()
