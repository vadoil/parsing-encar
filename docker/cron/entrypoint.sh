#!/bin/sh
set -e

echo "[entrypoint] Running migrations..."
uv run --no-sync alembic upgrade head

echo "[entrypoint] Syncing models..."
uv run --no-sync python -m encar_parser sync

echo "[entrypoint] Starting cron..."
exec cron -f
