# syntax=docker/dockerfile:1
# Dataset Genie as one container: the built UI is served by the FastAPI backend on :8765.
#   docker compose up -d --build         (or scripts/docker-start.sh / scripts\docker-start.ps1)
# Data (SQLite DB, secrets file) lives in GENIE_HOME=/data; exports in /data/exports.

# ---- stage 1: build the React UI ------------------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- stage 2: Python runtime ----------------------------------------------------------------
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# dependencies first (cached until the lockfile changes), then the project itself
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY backend/ backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
# genie.main serves <repo>/frontend/dist, i.e. /app/frontend/dist here
COPY --from=ui /ui/dist frontend/dist

RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin genie \
 && mkdir -p /data/exports && chown -R genie:genie /data
ENV GENIE_HOME=/data \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER genie
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2).status == 200 else 1)"
CMD ["genie", "serve", "--host", "0.0.0.0", "--port", "8765"]
