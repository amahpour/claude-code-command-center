.DEFAULT_GOAL := help
PYTHON := python
PORT := 3000

.PHONY: help up down test lint format typecheck check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## Start the server (port 3000)
	uvicorn server.main:app --port $(PORT) --reload

down: ## Stop the server
	@pkill -f "uvicorn server.main:app" 2>/dev/null && echo "Server stopped" || echo "Server not running"

test: ## Run all tests
	$(PYTHON) -m pytest tests/ -v

lint: ## Run ruff linter and format check
	ruff check .
	ruff format --check .

format: ## Auto-format code with ruff
	ruff check --fix .
	ruff format .

typecheck: ## Run mypy type checking
	mypy server/

check: lint typecheck test ## Run all checks (lint + typecheck + test)

clean: ## Remove build artifacts and caches
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache reports/
	find . -path ./.venv -prune -o -type d -name __pycache__ -print -exec rm -rf {} +
	find . -path ./.venv -prune -o -type f -name '*.pyc' -print -delete
