"""uvicorn entrypoint. Wires real Redis + the launcher selected by env vars.

    XBT_LAUNCHER=demo   → DemoPaperLauncher (staging only)
    XBT_LAUNCHER=prod   → production launcher (NotImplementedError until built)

Required env vars (any mode):
    REDIS_URL
    GATEWAY_INTERNAL_HMAC_SECRET
Optional:
    AI_ENGINE_URL   base URL of the AI Engine; required to run ai_signal bots
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import create_engine as create_sync_engine

from .api.auth import InternalAuthenticator
from .clients.ai_engine import AiEngineClient
from .db.models import Base
from .db.session import create_engine_from_url, make_session_factory
from .events.publisher import EventPublisher
from .main import create_app

# Default DB for staging: a SQLite file on a persisted volume. Production swaps
# this for DATABASE_URL=postgresql+asyncpg://… and applies schema via Alembic.
_DEFAULT_DATABASE_URL = "sqlite+aiosqlite:////var/lib/xbt/bot-engine.db"


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} env var must be set")
    return v


_REDIS_SCHEMES = ("redis://", "rediss://", "unix://")


def _validate_redis_url(redis_url: str) -> str:
    """Fail fast with a readable message on a blank/garbage REDIS_URL.

    Without this, a misconfigured value (e.g. an env line that resolved to empty)
    surfaces only as a deep ``redis.from_url`` stack trace at boot.
    """
    if not redis_url or not redis_url.startswith(_REDIS_SCHEMES):
        raise RuntimeError(
            "REDIS_URL is missing or malformed: expected one of "
            f"{', '.join(_REDIS_SCHEMES)} (got {redis_url!r}). "
            "Check /etc/xbt/bot-engine.env on the Droplet."
        )
    return redis_url


def _bootstrap_sqlite_schema(database_url: str) -> None:
    """Create the schema for a SQLite DB on boot.

    The image ships no Alembic step, so the demo/staging SQLite DB needs its
    tables created the first time the container starts. For a real Postgres
    URL this is a no-op — migrations own the schema there.
    """
    if not database_url.startswith("sqlite"):
        return
    # sqlite+aiosqlite:///path → sqlite:///path (sync driver for one-shot DDL)
    sync_url = database_url.replace("+aiosqlite", "")
    db_path = sync_url.split(":///", 1)[-1]
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    sync_engine = create_sync_engine(sync_url)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()


def build_app() -> FastAPI:
    redis_url = _validate_redis_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
    secret = _require_env("GATEWAY_INTERNAL_HMAC_SECRET")

    redis = AsyncRedis.from_url(redis_url)
    publisher = EventPublisher(redis)
    auth = InternalAuthenticator.from_env(secret)

    # The ai_signal companion calls the AI Engine, signing with the same shared
    # HMAC secret. Optional: absent → ai_signal bots are rejected at launch.
    ai_engine_url = os.environ.get("AI_ENGINE_URL")
    ai_client = AiEngineClient(ai_engine_url, auth) if ai_engine_url else None

    mode = os.environ.get("XBT_LAUNCHER", "prod")
    if mode == "demo":
        from .launchers.demo_paper import DemoPaperLauncher

        database_url = os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)
        _bootstrap_sqlite_schema(database_url)
        session_factory = make_session_factory(create_engine_from_url(database_url))

        bar_interval_s = float(os.environ.get("XBT_DEMO_BAR_INTERVAL_S", "5"))
        launcher = DemoPaperLauncher(
            publisher,
            session_factory,
            bar_interval_seconds=bar_interval_s,
            ai_client=ai_client,
        )
    elif mode == "prod":
        from .launchers.production import ProductionLauncher
        from .security.keys import EnvelopeCipher

        database_url = _require_env("DATABASE_URL")  # Postgres in prod; Alembic owns schema
        _bootstrap_sqlite_schema(database_url)  # no-op for Postgres; helps a local sqlite smoke
        session_factory = make_session_factory(create_engine_from_url(database_url))
        cipher = EnvelopeCipher.from_env("XBT_KEK")
        allow_live = os.environ.get("XBT_ALLOW_LIVE", "0") == "1"

        launcher = ProductionLauncher(
            publisher, session_factory, cipher, allow_live=allow_live, ai_client=ai_client
        )
    else:
        raise RuntimeError(f"unknown XBT_LAUNCHER mode: {mode}")

    return create_app(launcher=launcher, publisher=publisher, internal_auth=auth)


app = build_app()
