"""uvicorn entrypoint. Wires real Redis + the launcher selected by env vars.

    XBT_LAUNCHER=demo   → DemoPaperLauncher (staging only)
    XBT_LAUNCHER=prod   → production launcher (NotImplementedError until built)

Required env vars (any mode):
    REDIS_URL
    GATEWAY_INTERNAL_HMAC_SECRET
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from redis.asyncio import Redis as AsyncRedis

from .api.auth import InternalAuthenticator
from .events.publisher import EventPublisher
from .main import create_app


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} env var must be set")
    return v


def build_app() -> FastAPI:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    secret = _require_env("GATEWAY_INTERNAL_HMAC_SECRET")

    redis = AsyncRedis.from_url(redis_url)
    publisher = EventPublisher(redis)
    auth = InternalAuthenticator.from_env(secret)

    mode = os.environ.get("XBT_LAUNCHER", "prod")
    if mode == "demo":
        from .launchers.demo_paper import DemoPaperLauncher

        bar_interval_s = float(os.environ.get("XBT_DEMO_BAR_INTERVAL_S", "5"))
        launcher = DemoPaperLauncher(publisher, bar_interval_seconds=bar_interval_s)
    elif mode == "prod":
        raise NotImplementedError(
            "production launcher not implemented yet — set XBT_LAUNCHER=demo for staging"
        )
    else:
        raise RuntimeError(f"unknown XBT_LAUNCHER mode: {mode}")

    return create_app(launcher=launcher, publisher=publisher, internal_auth=auth)


app = build_app()
