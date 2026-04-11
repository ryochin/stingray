FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG SUPERCRONIC_VERSION=v0.2.33
ARG TARGETARCH

# Install supercronic (cron designed for containers) + tzdata for JST
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
  && curl -fsSL "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
       -o /usr/local/bin/supercronic \
  && chmod +x /usr/local/bin/supercronic \
  && apt-get purge -y --auto-remove curl \
  && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Tokyo \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application sources
COPY src/ ./src/
COPY crontab ./crontab

CMD ["/usr/local/bin/supercronic", "-passthrough-logs", "/app/crontab"]
