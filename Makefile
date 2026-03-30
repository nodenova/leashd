.PHONY: check check-all

check:
	uv run ruff check --fix . && uv run ruff format .
	uv run mypy leashd/ --explicit-package-bases || true
	uv run pytest

check-all: check
	uv run pytest -m e2e
