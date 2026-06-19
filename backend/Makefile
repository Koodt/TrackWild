.PHONY: help install lint format test check-all \
       queue-status queue-redis queue-stats \
       tile-generate tile-get db-stats db-tables

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies (main + dev + lint)
	@uv pip install -e ".[dev,lint]"

# ── Lint ────────────────────────────────────────────────

ruff-check: ## Run ruff linter
	@echo "▶ Ruff check..."
	@ruff check app/ scripts/

ruff-format-check: ## Check formatting without modifying
	@echo "▶ Ruff format check..."
	@ruff format --check app/ scripts/

ruff-fix: ## Auto-fix ruff issues
	@echo "▶ Ruff fix..."
	@ruff check --fix app/ scripts/
	@ruff format app/ scripts/

mypy: ## Run mypy type checker
	@echo "▶ MyPy..."
	@mypy app/ scripts/

lint: ruff-check ruff-format-check mypy ## Run all linters (no auto-fix)

format: ruff-fix ## Auto-format and fix all issues

# ── Tests ───────────────────────────────────────────────

test: ## Run tests
	@echo "▶ Tests..."
	@pytest -v

# ── Aggregated ──────────────────────────────────────────

check-all: ruff-check ruff-format-check mypy test ## Full CI pipeline: lint + type + tests

# ── Queue & Tile monitoring ──────────────────────────────

COMPOSE := docker compose -f ../deployments/docker-compose.dev.yml
BACKEND := $(COMPOSE) exec -T backend
REDIS   := $(COMPOSE) exec -T redis
DB      := $(COMPOSE) exec -T db

queue-status: ## Tile queue status (ondemand/pregen/pending)
	@curl -s http://localhost/v1/tiles/queue-status | python3 -m json.tool 2>/dev/null || echo "Backend not reachable on localhost"

queue-redis: ## Raw Redis stream info (pending messages, consumers, length)
	@echo "=== On-demand stream ===" && \
	$(REDIS) redis-cli XINFO STREAM tw:tile:stream:ondemand 2>/dev/null || echo "Stream empty or not created yet" && \
	echo "" && echo "=== Pregen stream ===" && \
	$(REDIS) redis-cli XINFO STREAM tw:tile:stream:pregen 2>/dev/null || echo "Stream empty or not created yet" && \
	echo "" && echo "=== Pending dedup set ===" && \
	$(REDIS) redis-cli SCARD tw:tile:pending 2>/dev/null || echo "0" && \
	echo "" && echo "=== Pregen layer counters ===" && \
	$(REDIS) redis-cli KEYS "tw:tile:pregen:layer:*" 2>/dev/null | while read k; do echo "$$k = $$(redis-cli GET $$k)"; done || echo "No layer counters"

queue-stats: ## Detailed consumer group stats (inflight, idle, etc.)
	@echo "=== On-demand consumers ===" && \
	$(REDIS) redis-cli XINFO CONSUMERS tw:tile:stream:ondemand tile-workers 2>/dev/null || echo "No consumers" && \
	echo "" && echo "=== Pregen consumers ===" && \
	$(REDIS) redis-cli XINFO CONSUMERS tw:tile:stream:pregen tile-workers 2>/dev/null || echo "No consumers" && \
	echo "" && echo "=== On-demand pending ===" && \
	$(REDIS) redis-cli XPENDING tw:tile:stream:ondemand tile-workers 2>/dev/null || echo "No pending" && \
	echo "" && echo "=== Pregen pending ===" && \
	$(REDIS) redis-cli XPENDING tw:tile:stream:pregen tile-workers 2>/dev/null || echo "No pending"

tile-generate: ## Enqueue a tile for generation. Usage: make tile-generate SLOT=day Z=7 X=65 Y=33
	@if [ -z "$(SLOT)" ] || [ -z "$(Z)" ] || [ -z "$(X)" ] || [ -z "$(Y)" ]; then \
		echo "Usage: make tile-generate SLOT=day Z=7 X=65 Y=33"; exit 1; \
	fi
	@curl -s -X POST "http://localhost/v1/tiles/generate/$(SLOT)/$(Z)/$(X)/$(Y)" | python3 -m json.tool

tile-get: ## Fetch a tile and save to /tmp. Usage: make tile-get SLOT=day Z=7 X=65 Y=33
	@if [ -z "$(SLOT)" ] || [ -z "$(Z)" ] || [ -z "$(X)" ] || [ -z "$(Y)" ]; then \
		echo "Usage: make tile-get SLOT=day Z=7 X=65 Y=33"; exit 1; \
	fi
	@curl -s -o /tmp/tile_$(SLOT)_$(Z)_$(X)_$(Y).png -w "HTTP %{http_code}, size %{size_download} bytes\n" "http://localhost/v1/tiles/$(SLOT)/$(Z)/$(X)/$(Y).png"
	@file /tmp/tile_$(SLOT)_$(Z)_$(X)_$(Y).png 2>/dev/null || true

db-stats: ## Database row counts (estimated from catalog)
	@$(DB) psql -U trackwild -d trackwild -c "SELECT relname AS table, COALESCE(reltuples::bigint,0) AS estimated_rows FROM pg_class c JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='public' AND relkind='r' ORDER BY relname"

db-tables: ## List all tables in public schema
	@$(DB) psql -U trackwild -d trackwild -c "\dt public.*" 2>/dev/null || echo "DB not reachable"
