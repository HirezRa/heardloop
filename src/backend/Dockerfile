# One image for both services: api (default CMD) and worker (compose overrides the command).
# Build context is the repository root (see docker-compose.yml) so VERSION can be copied in.
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv

ARG VERSION=0.0.0
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.version=${VERSION} \
      org.opencontainers.image.revision=${GIT_SHA}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HEARDLOOP_GIT_SHA=${GIT_SHA}

WORKDIR /app
COPY src/backend/pyproject.toml src/backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY VERSION ./VERSION
COPY src/backend/alembic.ini ./
COPY src/backend/migrations/ migrations/
COPY src/backend/heardloop/ heardloop/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/media && chown app /data/media
USER app

EXPOSE 8000
# X-Forwarded-* headers are trusted only from the Docker network range (the web/nginx container, which
# overwrites X-Forwarded-For with the real client address) and from localhost (health check).
# Compose default networks use 172.16.0.0/12; widen it only if the stack runs on a custom network.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn heardloop.api.main:app --host 0.0.0.0 --port 8000 --no-server-header --proxy-headers --forwarded-allow-ips=172.16.0.0/12,127.0.0.1"]
