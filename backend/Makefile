.PHONY: help install lint format test check-all

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
