FROM node:22.22-bookworm-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

#FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
FROM ghcr.io/astral-sh/uv:0.11.7-python3.12-trixie-slim@sha256:760df02ce4a80b395949f5ac7bf9741c5123fb829d9b62092363bfdca0088059

ARG SUPERCRONIC_VERSION=v0.2.33
# SHA-1 sums from the upstream release notes; update them with the version.
ARG SUPERCRONIC_SHA1_AMD64=71b0d58cc53f6bd72cf2f293e09e294b79c666d8
ARG SUPERCRONIC_SHA1_ARM64=e0f0c06ebc5627e43b25475711e694450489ab00
ARG TARGETARCH

# Install supercronic (cron designed for containers) + tzdata for zone-aware
# timestamps.
# curl is kept at runtime — used by container healthchecks.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
  && case "${TARGETARCH}" in \
       amd64) supercronic_sha1="${SUPERCRONIC_SHA1_AMD64}" ;; \
       arm64) supercronic_sha1="${SUPERCRONIC_SHA1_ARM64}" ;; \
     esac \
  && curl -fsSL "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
       -o /usr/local/bin/supercronic \
  && echo "${supercronic_sha1}  /usr/local/bin/supercronic" | sha1sum -c - \
  && chmod +x /usr/local/bin/supercronic \
  && rm -rf /var/lib/apt/lists/*

ENV TZ=UTC \
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
