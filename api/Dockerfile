FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv==0.13.4

FROM base AS dependencies
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
    && mkdir -p /app/var \
    && chown -R workflow:workflow /app/var /home/workflow

COPY --from=dependencies --chown=workflow:workflow /app/api/.venv /app/api/.venv
COPY --chown=workflow:workflow api/src /app/api/src
COPY --chown=workflow:workflow api/alembic.ini /app/api/alembic.ini
COPY --chown=workflow:workflow api/migrations /app/api/migrations
COPY --chown=workflow:workflow contracts /app/contracts
COPY --chown=workflow:workflow --chmod=755 api/docker-entrypoint.sh /usr/local/bin/api-entrypoint

USER workflow
EXPOSE 5001
ENTRYPOINT ["api-entrypoint"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "5001"]
