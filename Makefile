.PHONY: help install lint format typecheck test invariants security data-check check api clean

UV ?= uv

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install everything
	$(UV) sync --extra dev

lint:  ## ruff check + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:  ## Apply ruff formatting and autofixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck:  ## mypy --strict
	$(UV) run mypy

test:  ## Full test suite with coverage
	$(UV) run pytest --cov --cov-report=term-missing

invariants:  ## Only the ADR-backed boundary tests
	$(UV) run pytest -m invariant -v

security:  ## Dependency audit + static analysis
	$(UV) export --format requirements-txt --no-emit-project --all-extras \
		--no-hashes -o requirements-audit.txt
	$(UV) run pip-audit --requirement requirements-audit.txt --strict
	$(UV) run bandit -c pyproject.toml -r src scripts

data-check:  ## Verify data/raw is intact (ADR-003)
	$(UV) run python scripts/check_raw_immutability.py

check: lint typecheck test security data-check  ## Everything CI runs

api:  ## Serve the operations API locally
	$(UV) run uvicorn observability.api:app --reload --port 8000

clean:  ## Remove caches and derived files
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis htmlcov \
		coverage.xml .coverage requirements-audit.txt
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
