#!/bin/bash
set -e

echo "=== TrackWild Backend Init ==="

export PYTHONPATH=/app

# 1. Run migrations to head
echo "[1/2] Running alembic migrations..."
alembic upgrade head
echo "  ✓ Migrations applied"

# 2. Seed risk profiles
echo "[2/2] Seeding risk profiles..."
python scripts/seed.py
echo "  ✓ Profiles seeded"

echo "=== Init complete, starting server ==="

# Execute CMD (the actual server)
exec "$@"
