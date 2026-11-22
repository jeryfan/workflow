#!/bin/bash
set -euo pipefail

export LANG=${LANG:-C.UTF-8}
export LC_ALL=${LC_ALL:-C.UTF-8}
export PYTHONIOENCODING=utf-8

if [[ "${MODE:-server}" == "server" ]]; then
    alembic -c /app/api/alembic.ini upgrade head
fi

if [[ "${MODE:-server}" == "worker" ]]; then
    exec celery -A api.app.celery worker \
        --loglevel "${LOG_LEVEL:-INFO}" \
        --concurrency "${CELERY_CONCURRENCY:-2}" \
        --disable-prefetch
elif [[ "${MODE:-server}" == "beat" ]]; then
    exec celery -A api.app.celery beat \
        --loglevel "${LOG_LEVEL:-INFO}" \
        --schedule=/tmp/celerybeat-schedule
else
    TRUSTED_PROXIES="${TRUSTED_PROXIES:-127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
    if [[ "${DEBUG:-false}" == "true" ]]; then
        exec uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload \
            --proxy-headers --forwarded-allow-ips="$TRUSTED_PROXIES"
    else
        exec uvicorn api.app:app --host 0.0.0.0 --port 8000 \
            --workers 4 \
            --ws websockets \
            --proxy-headers \
            --forwarded-allow-ips="$TRUSTED_PROXIES"
    fi
fi
