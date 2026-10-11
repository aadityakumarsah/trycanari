# Settings come from .env when it exists (see .env.example).
-include .env
export

.PHONY: up down test lint typecheck fmt

up:
	docker compose up --build --detach --wait

down:
	docker compose down

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

fmt:
	uv run ruff check --fix .
	uv run ruff format .
