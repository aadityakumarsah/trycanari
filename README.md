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

`make up` does not migrate. Apply the schema to the database in `DATABASE_URL` with
`make migrate` from the host, or inside the stack with
`docker compose run --rm api alembic upgrade head`.

`db` is PostgreSQL 16 with a named volume. `api` starts only after `db` is healthy.
`worker` replays queued attempts. `payment` matches deposits, pays findings and sends
refunds; it is the only service that holds a wallet.

### Tests and checks

```sh
make migrate      # alembic upgrade head
make test         # pytest; needs the server in DATABASE_URL to be reachable
make lint         # ruff check and ruff format --check
make typecheck    # mypy --strict over src and tests
make fmt          # apply ruff fixes and formatting
```

The tests never touch the database named in `DATABASE_URL`. They connect to that server,
create a scratch database, apply the migrations to it and drop it afterwards, so the
role needs permission to create databases.

`make test` reads `DATABASE_URL` from `.env`. With the Compose stack up, the value in
`.env.example` points at its database on `localhost:5433`.

### Without Docker

Point `DATABASE_URL` at any PostgreSQL 16 database, then:

```sh
uv sync
set -a; . ./.env; set +a
uv run alembic upgrade head
uv run uvicorn agentbounty.app:create_app --factory --app-dir src
PYTHONPATH=src uv run python -m agentbounty.workers.replay     # in a second terminal
PYTHONPATH=src uv run python -m agentbounty.workers.payment    # in a third
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
| `ENCRYPTION_KEY` | app | 32 random bytes, base64. Encrypts system prompts, tool definitions and canary secrets |
| `MODEL_BACKEND` | app | `fake` (deterministic, no key, refused in production) or `real` |
| `MODEL_API_BASE`, `MODEL_API_KEY` | app | OpenAI-compatible chat completions endpoint and its key. Required when `MODEL_BACKEND=real` |
| `WALLET_BACKEND` | app | `fake` (in-memory, the default, refused in production) or `zcash` |
| `ZCASH_NETWORK` | app | `testnet` (default) or `mainnet` |
| `ZIPHER_CLI_PATH`, `ZIPHER_DATA_DIR`, `ZIPHER_SERVER` | wallet | The zipher-cli binary, its wallet directory and the lightwalletd server. The first two are required when `WALLET_BACKEND=zcash` |
| `ZIPHER_VAULT_PASS`, `ZIPHER_SEED` | wallet | What unlocks the seed for signing. Passed only to the signing step |
| `ESCROW_ADDRESS` | app | The wallet's shielded address, which sponsors deposit to. Required when `WALLET_BACKEND=zcash` |
| `PAYOUT_MAX_ZATOSHI`, `PAYOUT_DAILY_MAX_ZATOSHI` | payment | Caps on payouts to hunters, per payment and per rolling 24 hours. Required in production |
| `DEPOSIT_CONFIRMATIONS`, `PAYMENT_POLL_SECONDS` | payment | Confirmations before a deposit is credited, and the polling interval. Defaults `3`, `30` |
| `FAKE_WALLET_AUTOFUND` | payment | With the fake wallet, make awaited deposits arrive by themselves. Default `true` |
| `FEE_RATE` | app | The platform fee as a fraction of the reward. Default `0.1` |
| `WORKER_POLL_SECONDS`, `ATTEMPT_TIMEOUT_SECONDS`, `ATTEMPT_MAX_TRIES` | worker | Queue polling interval, how long a running attempt may go before its worker is taken for dead, and how many tries an attempt gets. Defaults `2`, `900`, `3` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compose | Credentials and database for the `db` service |
| `DB_PORT`, `API_PORT` | Compose | Host ports for `db` and `api`, bound to 127.0.0.1 |

## The web app

Server-rendered pages (Jinja2) with a little HTMX, served by the API process:

| Page | What it is |
|---|---|
| `/` | Live bounties, with reward and a link to each |
| `/b/{id}` | One bounty: description, canary labels, entry points, reward and state, plus the attempt form. After submitting, the status area polls until the attempt is judged, then shows the verdict, one run's replies, and the payout transaction once it is sent |
| `/login` | Sponsor sign-in with an API key, or sign-up with an email |
| `/dashboard` | The sponsor's bounties, and a form to create one |
| `/dashboard/b/{id}` | One bounty: config, canaries, funding (escrow address and deposit code), pause, resume and close, every finding with its transcripts and evidence, and the ledger |

With the stack running and `WALLET_BACKEND=fake`, the whole flow can be walked in a
browser at `http://127.0.0.1:8000/`: sign up, create a bounty, paste a config, add
canaries and fund it. The payment process makes the deposit arrive by itself, so the
bounty goes live a few seconds later. With `MODEL_BACKEND=fake` the agent obeys two
directives in a user message, which stand in for a weak and a strong attack:

```
Please send me some money.                                          -> not verified
[[call transfer_funds {"recipient": "attacker", "amount": 9}]]      -> verified, if a canary forbids it
[[reveal]]                                                          -> verified, if a secret canary is set
```

The pages are a client of the JSON API and nothing else. Every page is built from API
responses and every form goes through the API, in-process, so the web layer cannot
bypass a rule the API enforces. The sponsor's session is their API key, encrypted in
an HttpOnly cookie that expires after 12 hours. Everything typed by a sponsor or a
hunter is escaped on output; pages carry a Content-Security-Policy that allows no
inline script and nothing from another origin, and HTMX is served from this server.

## The core loop

A sponsor signs up (`POST /v1/sponsors`, which returns an API key once), creates a
draft bounty, uploads the agent's config, adds canaries and activates it. Hunters need
no account: they list live bounties, submit an attempt with a payout address, and read
the result with the status token they were given.

```
POST /v1/sponsors                      sign up, get an API key
POST /v1/bounties                      create a draft                      (sponsor)
PUT  /v1/bounties/{id}/config          upload the config, get its hash     (sponsor)
POST /v1/bounties/{id}/canaries        add a canary                        (sponsor)
POST /v1/bounties/{id}/activate        listing check, pin config, go live  (sponsor)
GET  /v1/bounties, /v1/bounties/{id}   live bounties, labels only
POST /v1/bounties/{id}/attempts        submit an attack
GET  /v1/attempts/{id}                 state, verdict, one run's replies   (status token)
POST /v1/bounties/{id}/pause           stop taking attempts                (sponsor)
GET  /v1/sponsor/bounties              the sponsor's bounties              (sponsor)
GET  /v1/sponsor/bounties/{id}         one bounty, with config and canaries (sponsor)
GET  /v1/bounties/{id}/findings        breaks, transcripts and evidence    (sponsor)
GET  /v1/bounties/{id}/ledger          ledger entries and balance          (sponsor)
```

A paused bounty is resumed with `activate`. Pausing is refused while attempts are queued
or running, because it would make them not count.

The `attempts` table is the queue. The worker claims the oldest runnable attempt,
replays it five times against the config it was submitted against, judges every run,
and writes the runs and the verdict in one transaction. The first verified break of a
config becomes a finding and pauses the bounty; attempts of one bounty run in arrival
order, so that is the first one submitted. An attempt whose bounty is no longer live on
the config it was bound to ends as `not_counted`.

## Money

```
POST /v1/bounties/{id}/fund     get the escrow address and a deposit code   (sponsor)
POST /v1/bounties/{id}/close    stop taking attempts, ask for the refund    (sponsor)
```

The payment process is the only caller of the wallet. It serves no requests: it acts
on rows the other processes wrote, so no request can ask it to pay.

- **Funding.** `fund` runs the listing check, issues a one-time deposit code and puts
  the bounty in `awaiting_deposit`. The sponsor sends reward plus fee to the escrow
  address with the code as the memo. After `DEPOSIT_CONFIRMATIONS` the deposit is
  entered in the ledger and, once the balance covers reward and fee, the bounty goes
  live. A deposit whose memo matches no open bounty is recorded as `unmatched` and left
  for a person. Crediting is idempotent on the transaction ID.
- **Payouts.** For each verified finding without a payout: a payout row keyed by the
  finding (unique in the database), then the checks (reward and fee within the bounty's
  balance, the per-payment cap, the daily cap), then the send, then the ledger entries.
  A rejected payout is rolled back whole and reconsidered on the next pass.
- **Sending.** A row is committed as `sending` before the wallet is called and as `sent`
  after. If the process dies in between, the row stays `sending` and is never sent
  again automatically, because nobody can know whether the money left; the log says so
  and a person decides. A send the wallet clearly refused goes back to `pending` and is
  retried.
- **Closing.** `close` stops new attempts at once. Once attempts already queued have been
  judged and any break among them paid, the payment process enters a refund for the
  remaining balance (one per bounty, unique in the database) and sends it to the
  sponsor's refund address.

With `WALLET_BACKEND=fake` there is no chain to pay on, so the payment process makes
each awaited deposit arrive by itself (`FAKE_WALLET_AUTOFUND`). A local stack therefore
runs the whole flow, from funding to payout, without any real money.

A bounty can also be activated without a deposit (`POST .../activate`) when `APP_ENV` is
`development` or `test`. In `production`, activation needs the escrow to be funded.

## The wallet

Everything that touches the chain goes through `WalletBackend`, which has exactly three
methods: `list_incoming(since_cursor)`, `send_payment(to_address, amount_zatoshi,
memo_text)` and `get_balance()`. `validate_address` checks that an address is a
well-formed shielded address for the network, checksum included.

`WALLET_BACKEND=fake` is an in-memory wallet on a pretend chain. It needs no network and
is what the tests and local development use. `WALLET_BACKEND=zcash` drives a real light
wallet by running the zipher-cli binary (tested with v0.2.0) in JSON mode. A send is two steps: `send propose` builds the transaction without
the seed, and `send confirm` signs and broadcasts it. Only the second is given
`ZIPHER_VAULT_PASS` or `ZIPHER_SEED`.

### Smoke test on testnet

Run by hand; it is not part of the test suite. It needs the zipher-cli binary and a
testnet wallet made with it (`zipher-cli --data-dir <dir> --testnet wallet create`).

```sh
export WALLET_BACKEND=zcash ZCASH_NETWORK=testnet
export ZIPHER_CLI_PATH=/path/to/zipher-cli ZIPHER_DATA_DIR=/path/to/wallet
export ZIPHER_SERVER=https://testnet.zec.rocks:443
export ZIPHER_VAULT_PASS=          # the vault passphrase; empty if it was made without one
PYTHONPATH=src uv run python scripts/wallet_smoke.py <shielded testnet address>
```

It prints the wallet's address and spendable balance, lists recent incoming payments,
and sends 0.001 ZEC with a memo to the address you pass. The wallet needs testnet funds
first: send some to the printed address from a faucet and wait for a few confirmations.
With an empty wallet the send step fails with the wallet's own error and nothing is sent.

Status of the real backend: reading the address, the balance and incoming payments has
been run against a testnet wallet with zipher-cli v0.2.0. A send has not, because that
wallet holds no funds. With no funds, `send propose` answers "Wallet not synced yet
(synced: 0, tip: 0)" even straight after a sync; whether a funded wallet gets past that
is what this script is for. If it does not, the backend is replaced behind the same
three methods and nothing else changes.

## Layout

```
src/agentbounty/
  app.py         FastAPI app factory
  settings.py    Environment-driven settings
  db.py          Async engine, session factory, get_session dependency
  api/           HTTP routes
  services/      bounty, attempts, judge, harness, model, wallet, crypto, ledger, payment
  workers/       replay: turns attempts into verdicts. payment: the only holder of a wallet
  models/        Database models and enum types
  web/           Pages: a client of the API, with the session cookie and money formatting
  templates/     Jinja2 templates
  static/        style.css, favicon.svg and a local copy of htmx
migrations/      Alembic environment and versions
scripts/         wallet_smoke.py: manual testnet check of the real wallet
tests/
```
