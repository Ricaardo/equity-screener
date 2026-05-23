UV ?= uv
TEST_PATH ?= tests

.PHONY: install install-dev install-all format format-check lint lint-fix test typecheck validate hooks pre-commit

install:
	$(UV) sync

install-dev:
	$(UV) sync --extra dev

install-all:
	$(UV) sync --extra dev --extra pdf --extra ui

format:
	$(UV) run --extra dev ruff format src tests

format-check:
	$(UV) run --extra dev ruff format --check src tests

lint:
	$(UV) run --extra dev ruff check src tests

lint-fix:
	$(UV) run --extra dev ruff check --fix src tests

test:
	$(UV) run python -m unittest discover -s $(TEST_PATH) -v

typecheck:
	$(UV) run python -m compileall -q src tests

validate: lint typecheck test

hooks:
	$(UV) run --extra dev pre-commit install

pre-commit:
	$(UV) run --extra dev pre-commit run --all-files
