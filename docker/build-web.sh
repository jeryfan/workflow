#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if [ -n "${PNPM:-}" ]; then
    PNPM_CMD=$PNPM
elif command -v pnpm >/dev/null 2>&1; then
    PNPM_CMD=pnpm
elif command -v corepack >/dev/null 2>&1; then
    PNPM_CMD="corepack pnpm"
elif [ -x "$HOME/Library/pnpm/pnpm" ]; then
    PNPM_CMD="$HOME/Library/pnpm/pnpm"
else
    echo "pnpm is required to build the frontend" >&2
    exit 1
fi

$PNPM_CMD -C web build
rm -rf docker/www.workflow.next docker/www.workflow.previous
mkdir -p docker/www docker/www.workflow.next
cp -R web/dist/. docker/www.workflow.next/
if [ -d docker/www/workflow ]; then
    mv docker/www/workflow docker/www.workflow.previous
fi
mv docker/www.workflow.next docker/www/workflow
rm -rf docker/www.workflow.previous
