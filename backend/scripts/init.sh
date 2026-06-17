#!/bin/bash
set -e

echo "=== TrackWild Backend Init ==="

export PYTHONPATH=/app

# 1. Run migrations to head
echo "[1/1] Running alembic migrations..."
alembic upgrade head
echo "  ✓ Migrations applied"

echo "=== Init complete, starting server ==="

# Execute CMD (the actual server)
exec "$@"
