FROM node:22-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG SUPERCRONIC_VERSION=v0.2.33
ARG TARGETARCH

# Install supercronic (cron designed for containers) + tzdata for JST.
# curl is kept at runtime — used by container healthchecks.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
  && curl -fsSL "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
       -o /usr/local/bin/supercronic \
  && chmod +x /usr/local/bin/supercronic \
  && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Tokyo \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install Python dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application sources
COPY backend/ ./backend/
COPY crontab ./crontab

# Copy built frontend assets
COPY --from=frontend-build /build/dist ./frontend/dist

CMD ["/usr/local/bin/supercronic", "-passthrough-logs", "/app/crontab"]
