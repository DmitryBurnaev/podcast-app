# copy source code
FROM alpine:3.22 AS code-layer
WORKDIR /usr/src

COPY src ./src
COPY alembic.ini .
COPY etc/docker-entrypoint .

# copy source code
FROM python:3.14-alpine AS requirements-layer
WORKDIR /usr/src
ARG DEV_DEPS="false"
ARG UV_VERSION=0.11.7

COPY pyproject.toml .
COPY uv.lock .

RUN pip install uv==${UV_VERSION} && \
	  if [ "${DEV_DEPS}" = "true" ]; then \
      uv export --quiet --format requirements-txt --frozen --no-editable --no-emit-project --output-file requirements.txt; \
    else \
      uv export --quiet --format requirements-txt --frozen --no-dev --no-editable --no-emit-project --output-file requirements.txt; \
    fi

FROM python:3.14-alpine AS base
ARG PIP_DEFAULT_TIMEOUT=300
ARG PIP_VERSION=26
WORKDIR /app

COPY --from=requirements-layer /usr/src/requirements.txt .

RUN apk add --no-cache ffmpeg tzdata ca-certificates && \
    pip install --upgrade pip==${PIP_VERSION} && \
    pip install --timeout "${PIP_DEFAULT_TIMEOUT}" --no-cache-dir --require-hashes -r requirements.txt

RUN addgroup -S podcast-app -g 1008 && \
    adduser -S -G podcast-app -u 1008 -H podcast-app && \
    mkdir -p /app/.local/media_cache/episodes_cover && \
    chown -R podcast-app:podcast-app /app/.local

USER podcast-app

COPY --from=code-layer --chown=podcast-app:podcast-app /usr/src .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV APP_PORT=8000
ENV MEDIA_CACHE_DIR=/app/.local/media_cache/episodes_cover

FROM base AS service

EXPOSE 8000

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint"]

FROM base AS tests

COPY pyproject.toml .

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint"]
