# AI Engine container.
#
# Build context MUST be the monorepo root. Build with:
#
#     docker build -f apps/ai-engine/Dockerfile -t xbt-ai-engine .
#
# The repo layout is mirrored under /repo so the local `xbt-core` path
# dependency (../../packages/xbt-core) resolves the same as in dev.

FROM python:3.12-slim
WORKDIR /repo

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY packages/xbt-core ./packages/xbt-core
RUN uv pip install --system --no-cache ./packages/xbt-core

COPY apps/ai-engine/pyproject.toml ./apps/ai-engine/pyproject.toml
RUN uv pip install --system --no-cache -r apps/ai-engine/pyproject.toml

COPY apps/ai-engine/app ./apps/ai-engine/app

WORKDIR /repo/apps/ai-engine

EXPOSE 5002

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:5002/healthz || exit 1

CMD ["uvicorn", "app.entrypoint:app", "--host", "0.0.0.0", "--port", "5002"]
