<p align="center">
  <img src="docs/banner.png" alt="trycanari" width="100%" />
</p>

<h1 align="center">trycanari</h1>

<p align="center">
  <b>Bug bounties for AI agents.</b><br />
  Sponsors plant canaries in their agents and lock a reward. Hunters attack.<br />
  A break that is proven five times over is paid automatically, in shielded Zcash.
</p>

<p align="center">
  <a href="https://trycanari.pages.dev">Website</a> ·
  <a href="https://trycanari.pages.dev/docs">Docs</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="docs/REFERENCE.md">Reference</a>
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white" />
  <img alt="PostgreSQL 16" src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white" />
  <img alt="React 19" src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black" />
  <img alt="TypeScript strict" src="https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white" />
  <img alt="Bun" src="https://img.shields.io/badge/Bun-1.3-000000?logo=bun&logoColor=white" />
  <br />
  <img alt="472 tests" src="https://img.shields.io/badge/tests-472%20passing-6ee7b7" />
  <img alt="mypy strict" src="https://img.shields.io/badge/mypy-strict-6ee7b7" />
  <img alt="ruff" src="https://img.shields.io/badge/lint-ruff-6ee7b7" />
  <img alt="Zcash" src="https://img.shields.io/badge/payouts-shielded%20ZEC-F4B728?logo=zcash&logoColor=black" />
</p>

---

## At a glance

| | |
|---|---|
| **What it is** | A marketplace where an AI agent's owner pays only for attacks that provably work |
| **Type** | Web application: JSON API, two background workers, single-page app |
| **Backend** | Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · Pydantic v2 |
| **Frontend** | React 19 · TypeScript (strict) · Bun |
| **Database** | PostgreSQL 16 (works on Supabase behind its pooler) |
| **Payments** | Shielded Zcash, behind a three-method wallet interface |
| **Status** | Working end to end in demo mode. A real model and a funded wallet are not connected yet |

| Number | |
|---:|---|
| **34** | API endpoints |
| **11** | database migrations |
| **385** | Python tests |
| **87** | web tests |
| **~8,100** | lines of Python |
| **~6,600** | lines of TypeScript |

## Contents

- [How it works](#how-it-works)
- [Features](#features)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Roles: owner, admin, ambassador](#roles-owner-admin-ambassador)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Checks](#checks)
- [Deployment](#deployment)
- [Security](#security)
- [What is not done](#what-is-not-done)

## How it works

```mermaid
sequenceDiagram
    autonumber
    participant S as Sponsor
    participant P as trycanari
    participant H as Hunter
    S->>P: List an agent, plant canaries
    S->>P: Deposit reward + fee to escrow
    P-->>S: Bounty goes live
    H->>P: Submit an attack
    P->>P: Replay it 5 times in a sandbox
    alt same canary trips in 3 or more runs
        P->>H: Pay the reward (shielded ZEC)
        P->>S: The attack and all 5 transcripts
        P-->>S: Bounty pauses until patched
    else fewer than 3
        P-->>H: What the agent did, to try again
    end
```

A **canary** is a tripwire the sponsor plants:

| Canary | Trips when the agent |
|---|---|
| **Secret leak** | emits a planted secret, in a reply or a tool call, in any of several encodings (plain, base64, hex, reversed, letter-separated) |
| **Forbidden tool call** | calls a named tool in a way the sponsor's rule forbids, for example a transfer to anyone not on an approved list |

Nobody judges a break. A canary tripped or it did not, and the transcript is the proof.

## Features

<table>
<tr>
<td width="33%" valign="top">

**For hunters**

- Live bounties with search and sort
- Attack composer with a live price
- Verdict, tripped runs and what the agent did
- Attempt history kept in the browser
- A plain-markdown guide for AI agents at `/agent.md`
- An attacker agent that hunts by itself

</td>
<td width="33%" valign="top">

**For sponsors**

- Config, mock tools and canaries
- A check that every canary can actually trip
- Escrow with a deposit code
- Activity, findings and a full ledger
- Re-arm gate: the paid attack is replayed against the patch
- Close and refund at any time

</td>
<td width="33%" valign="top">

**For operators**

- Invite-only hunting with referral codes
- Credits: a rolling 24-hour allowance per account
- Admin page: people, roles, codes
- Rate limits and a daily token budget
- Append-only ledger and payment audit
- Secrets and transcripts encrypted at rest

</td>
</tr>
</table>

## Architecture

```mermaid
flowchart LR
    B[Browser] --> CF[Cloudflare Pages<br/>static app + proxy]
    A[AI agents<br/>and scripts] --> API
    CF --> API[API<br/>FastAPI]
    API --> DB[(PostgreSQL)]
    W[Replay worker] --> DB
    W --> M[Model API]
    PAY[Payment process] --> DB
    PAY --> Z[Zcash wallet]
```

Three processes share one database and nothing else:

| Process | Does | Never does |
|---|---|---|
| **API** | Serves requests and the web app | Calls a model, holds a wallet |
| **Replay worker** | Replays attacks, judges them, records verdicts | Holds a wallet, takes requests |
| **Payment process** | Credits deposits, sends payouts and refunds | Takes requests, calls a model |

Money is integer zatoshi. A bounty's balance is never stored: it is the sum of an
append-only ledger that the database itself refuses to update or delete, and a finding
can have exactly one payout, enforced by a unique constraint.

## Quick start

You need [uv](https://docs.astral.sh/uv/), [Bun](https://bun.sh), PostgreSQL 16 and `make`.

```bash
git clone https://github.com/aadityakumarsah/trycanari.git
cd trycanari

cp .env.example .env      # set DATABASE_URL and SECRET_ENCRYPTION_KEY
make migrate              # create the schema
make web-build            # build the web app

# three processes, each in its own terminal
uv run --env-file .env uvicorn agentbounty.app:create_app --factory --app-dir src --port 3000
uv run --env-file .env python -m agentbounty.workers.replay
uv run --env-file .env python -m agentbounty.workers.payment
```

Open <http://localhost:3000>. To have something to attack:

```bash
make seed API_PORT=3000   # lists a demo treasury agent with two canaries
```

With `MODEL_BACKEND=fake` and `WALLET_BACKEND=fake` (the defaults in `.env.example`) the
whole flow runs with no model key and no money: deposits arrive by themselves, and the
stand-in agent obeys two directives, `[[reveal]]` and `[[call tool {…}]]`, so both kinds
of canary can be tripped.

## Roles: owner, admin, ambassador

| Role | Can | How someone gets it |
|---|---|---|
| **Owner** | Everything an admin can; cannot be removed | Listed in `ADMIN_EMAILS`, and signed in with Google |
| **Admin** | Make ambassadors and admins, grant hunting access, make referral codes of any size | An admin switches it on, on the Admin page |
| **Ambassador** | Make referral codes (up to 20, 25 places each) and share invite links | An admin switches it on |
| **Hunter** | Attack bounties, spending credits | Opens an invite link, or enters a referral code once |
| **Sponsor** | List agents and fund bounties | Anyone with an account |

**As the owner:** put your Google address in `ADMIN_EMAILS`, sign in with Google, and open
**Admin** from the menu under your picture. From there:

1. **People** → find someone → switch on **Ambassador** (or **Admin**, or **Can hunt**).
2. **Referral codes** → choose how many places → **Create and copy link**.

**As an ambassador:** the dashboard has a *Your referral codes* panel. Make a code, press
**Copy invite link**, and send it. The link looks like `https://your-site/invite/ABCD-2345`.

**Whoever opens the link** sees who invited them and signs in; the code is applied for them
and the bounties are open. Someone already signed in accepts with one click.

The same from a terminal, for a first setup or a script:

```bash
python -m agentbounty.admin code --uses 10             # a referral code
python -m agentbounty.admin ambassador a@example.com   # make an ambassador
python -m agentbounty.admin admin a@example.com        # make an admin
```

## Configuration

Everything comes from environment variables; `.env.example` lists them all with comments.

| Variable | What it is |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…` |
| `SECRET_ENCRYPTION_KEY` | 32 random bytes, base64. Losing it makes encrypted columns unreadable |
| `APP_ENV` | `development`, `test` or `production` |
| `MODEL_BACKEND` | `fake`, or `real` with `MODEL_API_BASE` and `MODEL_API_KEY` |
| `WALLET_BACKEND` | `fake`, or `zcash` with the wallet settings |
| `INVITE_ONLY` | `true`: hunting needs a referral code and spends credits |
| `ADMIN_EMAILS` | The owners' Google addresses, separated by commas |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `PUBLIC_URL` | Turn on sign in with Google |

<details>
<summary>Limits and tuning</summary>

| Variable | Default | What it limits |
|---|---:|---|
| `STARTER_CREDITS` | 25 | Credits a new account may spend in 24 hours |
| `PROVEN_CREDITS` | 200 | The same, after a verified break |
| `RATE_LIMIT_PER_MINUTE` | 120 | Requests per visitor to public endpoints |
| `ATTEMPTS_PER_ADDRESS_PER_HOUR` | 20 | Attacks per payout address |
| `ATTEMPTS_PER_BOUNTY_PER_HOUR` | 60 | Attacks on one bounty |
| `BOUNTY_DAILY_TOKEN_BUDGET` | 2,000,000 | Model tokens one bounty's replays may use in 24 hours |
| `FEE_RATE` | 0.1 | The platform's fee, charged only on a payout |
| `PUBLIC_CACHE_SECONDS` | 5 | How long public pages are served from memory |

</details>

## Project layout

```
src/agentbounty/
├── app.py            FastAPI app factory
├── settings.py       Every setting, read from the environment
├── api/              Routes: public, sponsor, admin, session, Google sign-in
├── services/         judge · harness · attempts · payment · ledger · credits · wallet · crypto
├── workers/          replay (verdicts) · payment (the only holder of a wallet)
├── models/           Tables and enum types
├── admin.py          Operator commands
├── demo.py           The demo agent and its seed command
└── attacker.py       An attacker agent that uses only the public API
web/
├── src/pages/        Home · Bounty · Dashboard · Profile · Admin · Invite · Docs · Login
├── src/components/   Flow · Transcript · AttemptStatus · ReferralGate · shared pieces
└── cloudflare/       The Pages worker that proxies API paths
migrations/versions/  0001 … 0011
tests/                One file per area; a scratch database per test
docs/REFERENCE.md     The long version of everything on this page
```

## Checks

```bash
make check        # everything below, in order
make test         # 385 Python tests, each on its own scratch database
make web-test     # 87 web tests
make lint         # ruff
make typecheck    # mypy --strict
make e2e          # the definition of done, as one test
```

The Python tests never touch a remote database: they refuse any host that is not local.

## Deployment

| Part | Where | How |
|---|---|---|
| Web app | Cloudflare Pages | `make web-deploy` |
| API and workers | Render, from `render.yaml` | One Docker service running all three processes |
| Database | Supabase | `make migrate`; row level security is on for every table |

The Pages worker passes `/v1`, `/health` and `/agent.md` to the API, so the browser sees a
single origin. Set `API_ORIGIN` on Pages to the API's address. Details are in the
[reference](docs/REFERENCE.md).

## Security

- **The sandbox.** Replays have no real tools and no network but the model API. Every tool
  call is answered by a mock from a table of canned results.
- **Encryption at rest.** System prompts, tool definitions, canary secrets and transcripts
  are envelope-encrypted, each value under its own key.
- **What hunters see.** Titles, entry points, canary labels and rewards. Never a prompt, a
  tool, a secret or a rule.
- **Sessions.** An HttpOnly, SameSite=Strict cookie; a state-changing request that relies
  on it must come from the site itself.
- **Payments.** A send is recorded before the wallet is asked to make it, and is never
  repeated automatically.

Found a problem? Please open an issue, or write to the address on the maintainer's profile
for anything sensitive.

## What is not done

- **A real model is not connected.** Demo mode uses a stand-in agent that anyone who knows
  its two directives can break.
- **A real wallet send is untested.** The Zcash wallet code exists; the test wallet was
  never funded.
- **Payouts stop at "sent".** The three-method wallet interface cannot see an outgoing
  payment confirm.
- **No site-wide token ceiling.** Limits are per bounty and per account, not across the
  whole platform.
- **CI is parked.** The workflow is at `ci/github-actions.yml`; copy it to
  `.github/workflows/ci.yml` to switch it on.
- **No license yet.** All rights are reserved until one is added.
