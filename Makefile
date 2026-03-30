.DEFAULT_GOAL := help
PORT := 4700
# Prefer project .venv via uv so `make up` works without `source .venv/bin/activate`
UV_RUN := uv run

.PHONY: help setup install up down test lint format typecheck check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup install: ## Full local setup: uv venv + dev deps, Playwright Chromium, Claude hooks
	uv sync --extra dev
	uv run python -m playwright install chromium
	bash scripts/setup.sh $(PORT)
	@echo ""
	@echo "Setup complete. Start the app:  make up"
	@echo "If Playwright/e2e fails (missing OS libs), run:  uv run python -m playwright install --with-deps chromium"

up: ## Start the server (default 4700; override with PORT=XXXX)
	@bash scripts/setup.sh $(PORT)
	$(UV_RUN) uvicorn server.main:app --port $(PORT) --reload

down: ## Stop the server
	@pkill -f "uvicorn server.main:app" 2>/dev/null && echo "Server stopped" || echo "Server not running"

test: ## Run all tests
	$(UV_RUN) python -m pytest tests/ -v

lint: ## Run ruff linter and format check
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .

format: ## Auto-format code with ruff
	$(UV_RUN) ruff check --fix .
	$(UV_RUN) ruff format .

typecheck: ## Run mypy type checking
	$(UV_RUN) mypy server/

check: lint typecheck test ## Run all checks (lint + typecheck + test)

clean: ## Remove build artifacts and caches
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache reports/
	find . -path ./.venv -prune -o -type d -name __pycache__ -print -exec rm -rf {} +
	find . -path ./.venv -prune -o -type f -name '*.pyc' -print -delete
