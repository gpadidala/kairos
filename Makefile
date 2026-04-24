.PHONY: help install lock sync lint format type test test-unit test-integration test-e2e coverage verify run api scheduler once docker-build docker-run clean

PY := uv run python
PKG := pcap
IMAGE ?= pcap:dev

help: ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## install all dependencies (incl. dev)
	uv sync --all-extras --group dev

lock: ## regenerate uv.lock
	uv lock

sync: ## sync deps (no updates)
	uv sync --frozen --all-extras --group dev

lint: ## ruff check
	uv run ruff check src tests
	uv run ruff format --check src tests

format: ## ruff format + fix
	uv run ruff format src tests
	uv run ruff check --fix src tests

type: ## mypy --strict
	uv run mypy src/$(PKG)

test: ## full test suite with coverage
	uv run pytest

test-unit: ## unit tests only
	uv run pytest tests/unit -q

test-integration: ## integration tests only
	uv run pytest tests/integration -q -m integration

test-e2e: ## e2e tests only
	uv run pytest tests/e2e -q -m e2e

coverage: ## html coverage report
	uv run pytest --cov-report=html
	@echo "open htmlcov/index.html"

verify: lint type test ## one-shot: lint + type + test (phase gate)

run api: ## run FastAPI on :8080
	uv run python -m $(PKG) api

scheduler: ## run scheduler loop
	uv run python -m $(PKG) scheduler

once: ## single pipeline run (dry-run honored via env)
	uv run python -m $(PKG) once

docker-build: ## build container image
	docker build -t $(IMAGE) .

docker-run: ## run container locally
	docker run --rm -p 8080:8080 -e PCAP_FEATURES__DRY_RUN=true $(IMAGE)

clean: ## remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
