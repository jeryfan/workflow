FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv==0.13.4

FROM base AS dependencies
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY}
WORKDIR /app/api
COPY api/pyproject.toml api/uv.lock ./
COPY api/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM base AS production
WORKDIR /app/api
ENV VIRTUAL_ENV=/app/api/.venv \
    PATH="/app/api/.venv/bin:${PATH}" \
    WORKFLOW_PROJECT_ROOT=/app \
    TZ=UTC

RUN groupadd --system --gid 1001 workflow \
    && useradd --system --uid 1001 --gid workflow --home-dir /home/workflow --create-home workflow \
    && chown -R workflow:workflow /home/workflow

COPY --from=dependencies --chown=workflow:workflow /app/api/.venv /app/api/.venv
COPY --chown=workflow:workflow api/src /app/api/src
COPY --chown=workflow:workflow api/alembic.ini /app/api/alembic.ini
COPY --chown=workflow:workflow api/alembic /app/api/alembic
COPY --chown=workflow:workflow contracts /app/contracts
COPY --chown=workflow:workflow --chmod=755 api/docker-entrypoint.sh /usr/local/bin/api-entrypoint

USER workflow
EXPOSE 8000
ENTRYPOINT ["api-entrypoint"]
