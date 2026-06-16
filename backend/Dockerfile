# syntax=docker/dockerfile:1

# ──────────────────────────────────────────────
# Stage 1: System deps (cached — rarely changes)
# ──────────────────────────────────────────────
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────
# Stage 2: Python deps (cached unless pyproject.toml changes)
# Installs ONLY third-party dependencies — NOT the project itself.
# This avoids needing README.md / app/ code during deps install.
# ──────────────────────────────────────────────
FROM base AS deps

WORKDIR /build

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy only the dependency manifest
COPY pyproject.toml ./

# Generate requirements.txt from pyproject.toml (no project build needed)
RUN python3 -c "import tomllib; data=tomllib.load(open('pyproject.toml','rb')); open('requirements.txt','w').write('\n'.join(data['project']['dependencies'])+'\n')"

# Create venv and install deps
RUN uv venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements.txt

# ──────────────────────────────────────────────
# Stage 3: Runtime — only app code changes trigger rebuild
# ──────────────────────────────────────────────
FROM base

WORKDIR /app

# Copy pre-built virtualenv (compiled rasterio, numpy, etc. already inside)
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code — ONLY this layer changes on code edits
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Non-root user
RUN mkdir -p /tile_cache && \
    useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app /tile_cache
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
