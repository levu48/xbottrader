# Bot Engine container.
#
# Build context MUST be the monorepo root. Build with:
#
#     docker build -f apps/bot-engine/Dockerfile -t xbt-bot-engine .

FROM python:3.12-slim
WORKDIR /app

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

COPY apps/bot-engine/pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY apps/bot-engine/app ./app

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:5001/healthz || exit 1

CMD ["uvicorn", "app.entrypoint:app", "--host", "0.0.0.0", "--port", "5001"]
