# Settings come from .env when it exists (see .env.example). uv reads it as a dotenv
# file, so values may be quoted, which a URL containing & or ? needs.
ENV := $(if $(wildcard .env),--env-file .env,)
API_PORT ?= 8000

.PHONY: up down migrate seed e2e test lint typecheck fmt dupes web-install web-build web-dev web-test web-typecheck web-deploy check

up:
	docker compose up --build --detach --wait

down:
	docker compose down

# Apply migrations to the database in DATABASE_URL.
migrate:
	uv run $(ENV) alembic upgrade head

# Create the demo treasury bounty on the running stack and print its URL. With the fake
# wallet it is funded by a simulated deposit and is live within seconds.
seed:
	PYTHONPATH=src uv run $(ENV) python -m agentbounty.demo --api http://127.0.0.1:$(API_PORT)

# The definition of done, in one test.
e2e:
	uv run $(ENV) pytest tests/test_e2e.py -v

test:
	uv run $(ENV) pytest

# Editor and cloud-sync tools drop byte-identical copies beside a file: "pilot 2.py"
# next to "pilot.py". They are untracked, so CI never sees them, but two copies of one
# migration give Alembic two heads and take the whole suite down locally. Fail loudly
# instead of letting that happen quietly.
dupes:
	@found=$$(find src tests migrations docs web/src -name '* [0-9].*' 2>/dev/null); \
	if [ -n "$$found" ]; then \
		echo "Sync duplicates found. Delete these and run again:"; \
		echo "$$found" | sed 's/^/  /'; \
		exit 1; \
	fi

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

# Rebuilds the app on every save. The API serves the build, so reload its page.
web-dev: web-install
	cd web && bun run dev

# Publish the app to Cloudflare Pages. The API runs elsewhere (see the README).
web-deploy: web-install
	cd web && bun run deploy

web-test: web-install
	cd web && bun test

web-typecheck: web-install
	cd web && bun run typecheck

# Everything CI runs.
check: dupes lint typecheck test web-typecheck web-test web-build
