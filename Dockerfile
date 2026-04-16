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
ARG UV_VERSION=0.11.6

COPY pyproject.toml .
COPY uv.lock .

RUN pip install uv==${UV_VERSION} && \
	  if [ "${DEV_DEPS}" = "true" ]; then \
      uv export --format requirements-txt --frozen --output-file requirements.txt; \
    else \
      uv export --format requirements-txt --frozen --no-dev --output-file requirements.txt; \
    fi

RUN ls -lah /usr/src/requirements.txt
RUN cat /usr/src/requirements.txt


FROM python:3.14-alpine AS base
ARG PIP_DEFAULT_TIMEOUT=300
ARG PIP_VERSION=26
WORKDIR /app

COPY --from=requirements-layer /usr/src/requirements.txt .

RUN cat /app/requirements.txt
RUN pip install --upgrade pip==${PIP_VERSION} && \
    pip install --timeout "${PIP_DEFAULT_TIMEOUT}" --no-cache-dir --require-hashes -r requirements.txt

RUN addgroup -S podcast-app -g 1007 && \
    adduser -S -G podcast-app -u 1007 -H podcast-app

USER podcast-app

COPY --from=code-layer --chown=podcast-app:podcast-app /usr/src .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV APP_PORT=8000

FROM base AS service

EXPOSE 8000

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint"]

FROM base AS tests

COPY pyproject.toml .

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint"]
