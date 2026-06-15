.PHONY: install test test-live lint format migrate run sync

install:
	uv sync --extra dev
	uv run playwright install chromium

test:
	uv run pytest tests/unit tests/integration

test-live:
	uv run pytest tests/e2e -m live

lint:
	uv run ruff check .
	uv run mypy encar_parser

format:
	uv run ruff format .

migrate:
	uv run alembic upgrade head

run:
	uv run python -m encar_parser run

sync:
	uv run python -m encar_parser sync

record-fixtures:
	uv run python -m encar_parser record-fixtures
