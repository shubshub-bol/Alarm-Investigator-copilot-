# Short commands for common tasks. Every target runs plain commands that are also in the README,
# so make is optional: on Windows without make, run the commands directly.

PY_PACKAGES := alarm_simulator alarm_api_client alarm_mcp rag copilot
COV_FLAGS := $(foreach p,$(PY_PACKAGES),--cov=$(p))

.PHONY: help install ingest up down logs smoke run-api run-mcp run-backend run-gui \
        test test-python test-gui coverage lint format typecheck audit clean

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- Docker (the one-command way)
up: ## Build and start all four services; GUI at http://localhost:3000
	docker compose up --build

down: ## Stop the services (saved chats stay in the Docker volume)
	docker compose down

logs: ## Follow the logs of all services
	docker compose logs -f

smoke: ## Check a running stack end to end with the acceptance question
	uv run python scripts/smoke_stack.py

# ---------------------------------------------------------------- local development (no Docker)
install: ## Install Python and GUI dependencies
	uv sync --locked
	npm --prefix apps/frontend ci

ingest: ## Build the document index (downloads the embedding model the first time)
	uv run python -m rag.ingestion

run-api: ## Alarm API simulator on :8000
	uv run python -m alarm_simulator

run-mcp: ## MCP server on :9000
	uv run python -m alarm_mcp

run-backend: ## Copilot backend on :8080
	uv run python -m copilot

run-gui: ## GUI dev server on :5173
	npm --prefix apps/frontend run dev

# ---------------------------------------------------------------- quality
test: test-python test-gui ## All tests

test-python: ## Python tests: unit, MCP, RAG, orchestration, integration, end-to-end
	uv run pytest

test-gui: ## GUI tests
	npm --prefix apps/frontend run test

coverage: ## Python tests with a coverage report (htmlcov/index.html)
	uv run pytest $(COV_FLAGS) --cov-report=term --cov-report=html

lint: ## Formatting, lint and type checks
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	npm --prefix apps/frontend run typecheck

format: ## Fix formatting and safe lint findings
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Static types only
	uv run mypy
	npm --prefix apps/frontend run typecheck

audit: ## Check dependencies for known vulnerabilities
	uv export --locked --no-dev --no-emit-project --no-hashes --format requirements-txt > .audit-requirements.txt
	uvx pip-audit --disable-pip --no-deps -r .audit-requirements.txt
	npm --prefix apps/frontend audit --omit=dev --audit-level=high
	rm -f .audit-requirements.txt

clean: ## Remove reports and build output
	rm -rf htmlcov .coverage coverage.xml apps/frontend/dist
