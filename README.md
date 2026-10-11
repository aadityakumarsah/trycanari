# AgentBounty

A marketplace where sponsors pay to have their AI agents attacked, and pay only when an
attack provably works. A sponsor lists an agent and plants canaries; hunters submit
attacks; each attack is replayed against a sandboxed copy of the agent, and a verified
break pays the reward in shielded ZEC.

## Running locally

Needs Docker with Compose, [uv](https://docs.astral.sh/uv/) and `make`.

```sh
cp .env.example .env      # then set POSTGRES_PASSWORD and the same password in DATABASE_URL
make up                   # builds the image, starts db, api, worker and payment
curl http://127.0.0.1:8000/health    # {"status":"ok"}
make down
```

`db` is PostgreSQL 16 with a named volume. `api` starts only after `db` is healthy.
`worker` and `payment` idle for now; they are built in later steps.

### Tests and checks

```sh
make test         # pytest; needs the database in DATABASE_URL to be reachable
make lint         # ruff check and ruff format --check
make typecheck    # mypy --strict over src and tests
make fmt          # apply ruff fixes and formatting
```

`make test` reads `DATABASE_URL` from `.env`. With the Compose stack up, the value in
`.env.example` points at its database on `localhost:5433`.

### Without Docker

Point `DATABASE_URL` at any PostgreSQL 16 database, then:

```sh
uv sync
set -a; . ./.env; set +a
uv run uvicorn agentbounty.app:create_app --factory --app-dir src
```

`--app-dir src` imports the package from the source tree. It is needed on macOS when the
repo sits in a folder the system marks hidden files in (Desktop, Documents): Python 3.12
skips the hidden `.pth` file that an editable install relies on.

## Configuration

Configuration comes only from environment variables; nothing is read from a file by the
application itself. `.env` is a convenience for Compose and `make`. The service refuses
to start, naming the variable, if a required one is missing or invalid.

| Variable | Used by | Meaning |
|---|---|---|
| `APP_ENV` | app | `development`, `test` or `production` |
| `DATABASE_URL` | app | `postgresql+asyncpg://user:password@host:port/database` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compose | Credentials and database for the `db` service |
| `DB_PORT`, `API_PORT` | Compose | Host ports for `db` and `api`, bound to 127.0.0.1 |

## Layout

```
src/agentbounty/
  app.py         FastAPI app factory
  settings.py    Environment-driven settings
  db.py          Async engine, session factory, get_session dependency
  api/           HTTP routes
  services/      bounty, judge, harness, payment
  workers/       Background processes
  models/        Database models
  templates/     Jinja2 templates
tests/
```
