# Bot Engine container.
#
# Build context MUST be the monorepo root. Build with:
#
#     docker build -f apps/bot-engine/Dockerfile -t xbt-bot-engine .
#
# The repo layout is mirrored under /repo so the local `xbt-core` path
# dependency (../../packages/xbt-core) resolves the same as in dev.

FROM python:3.12-slim
WORKDIR /repo

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for any wheels without prebuilt binaries on slim.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

# Shared package first (its own layer for caching), then the app deps. The
# explicit install guarantees xbt-core is present even if the resolver skips it.
COPY packages/xbt-core ./packages/xbt-core
RUN uv pip install --system --no-cache ./packages/xbt-core

COPY apps/bot-engine/pyproject.toml ./apps/bot-engine/pyproject.toml
RUN uv pip install --system --no-cache -r apps/bot-engine/pyproject.toml

COPY apps/bot-engine/app ./apps/bot-engine/app
COPY apps/bot-engine/alembic ./apps/bot-engine/alembic
COPY apps/bot-engine/alembic.ini ./apps/bot-engine/alembic.ini

WORKDIR /repo/apps/bot-engine

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:5001/healthz || exit 1

CMD ["uvicorn", "app.entrypoint:app", "--host", "0.0.0.0", "--port", "5001"]
