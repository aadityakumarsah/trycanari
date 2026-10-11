# Settings come from .env when it exists (see .env.example).
-include .env
export

.PHONY: up down migrate seed e2e test lint typecheck fmt web-install web-build web-dev web-test web-typecheck check

up:
	docker compose up --build --detach --wait

down:
	docker compose down

# Apply migrations to the database in DATABASE_URL.
migrate:
	uv run alembic upgrade head

# Create the demo treasury bounty on the running stack and print its URL. With the fake
# wallet it is funded by a simulated deposit and is live within seconds.
seed:
	PYTHONPATH=src uv run python -m agentbounty.demo --api http://127.0.0.1:$${API_PORT:-8000}

# The definition of done, in one test.
e2e:
	uv run pytest tests/test_e2e.py -v

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

# --- the web app (web/): React and TypeScript, built and tested with Bun ---

web-install:
	cd web && bun install --frozen-lockfile

# Builds into src/agentbounty/static/app, where the API serves it.
web-build: web-install
	cd web && bun run build

# Hot-reloading pages on :5173, with /v1 proxied to the API (API_URL, default :8000).
web-dev: web-install
	cd web && bun run dev

web-test: web-install
	cd web && bun test

web-typecheck: web-install
	cd web && bun run typecheck

# Everything CI runs.
check: lint typecheck test web-typecheck web-test web-build
