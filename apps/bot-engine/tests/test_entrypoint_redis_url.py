from __future__ import annotations

import os

# app.entrypoint builds the app at import time (uvicorn entry: `app = build_app()`),
# so provide a minimal valid demo env before importing the validator under test.
os.environ.setdefault("GATEWAY_INTERNAL_HMAC_SECRET", "test")
os.environ.setdefault("XBT_LAUNCHER", "demo")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import pytest  # noqa: E402

from app.entrypoint import _validate_redis_url  # noqa: E402


@pytest.mark.parametrize(
    "url",
    ["redis://localhost:6379", "rediss://default:pw@host:25061", "unix:///tmp/redis.sock"],
)
def test_accepts_valid_schemes(url: str) -> None:
    assert _validate_redis_url(url) == url


@pytest.mark.parametrize("url", ["", "  ", "localhost:6379", "http://x", "default:pw@host:25061"])
def test_rejects_blank_or_schemeless(url: str) -> None:
    with pytest.raises(RuntimeError, match="REDIS_URL is missing or malformed"):
        _validate_redis_url(url)
