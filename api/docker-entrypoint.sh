#!/bin/sh
set -eu

python -m api.db.migrations upgrade --database-url "$DATABASE_URL"
if [ -n "${MIGRATE_FROM_SQLITE:-}" ]; then
    python -m api.db.sqlite_import --source "$MIGRATE_FROM_SQLITE" --database-url "$DATABASE_URL"
fi
exec "$@"
