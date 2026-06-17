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
# ──────────────────────────────────────────────
FROM base AS deps

WORKDIR /build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./

RUN python3 -c "import tomllib; data=tomllib.load(open('pyproject.toml','rb')); open('requirements.txt','w').write('\n'.join(data['project']['dependencies'])+'\n')"

RUN uv venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements.txt

# ──────────────────────────────────────────────
# Stage 3: Runtime
# ──────────────────────────────────────────────
FROM base

WORKDIR /app

COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY config/ ./config/
COPY alembic.ini ./

RUN mkdir -p /tile_cache && \
    useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app /tile_cache && \
    chmod +x /app/scripts/init.sh
USER appuser

ENTRYPOINT ["/app/scripts/init.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
