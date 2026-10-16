"""The platform as markdown, for agents that read rather than click: the hunter side as a
guide, and the sponsor side as a skill file for the agent in a sponsor's editor."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty import HOSTED_SITE
from agentbounty.api.public import cached, public_views
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.schemas import BountyPublic
from agentbounty.db import get_transaction
from agentbounty.models import Bounty, BountyState
from agentbounty.money import format_amount
from agentbounty.services.harness import MAX_SCRIPTED_TURNS, MAX_SLOT_CHARACTERS
from agentbounty.services.judge import RUNS_PER_ATTEMPT, TRIP_THRESHOLD
from agentbounty.services.wallet import address_kind
from agentbounty.settings import Settings

router = APIRouter(include_in_schema=False, dependencies=[Depends(limit_by_client)])

# The skill as it is kept in the repository, where it names the hosted site.
SKILL = (Path(__file__).parent.parent / "skills" / "trycanari" / "SKILL.md").read_text()


def site(request: Request) -> str:
    """The address people use, which behind a proxy is not the one this process hears."""
    settings: Settings = request.app.state.settings
    return f"{settings.public_url.rstrip('/')}/" if settings.public_url else str(request.base_url)


def guide(base_url: str, bounties: list[BountyPublic], settings: Settings) -> str:
    currency = settings.currency
    unit = currency.unit
    paid_in = f"shielded {currency.symbol}" if currency.private else currency.symbol
    kind = address_kind(settings)
    listed = [
        f"- `{b.id}`: {b.title}. Reward {b.reward_zatoshi} {unit}"
        f" ({format_amount(b.reward_zatoshi, currency)} {currency.symbol})."
        f" Entry points: {', '.join(b.entry_points)}. Canaries: "
        + ", ".join(f"{c.type.value}:{c.public_label}" for c in b.canaries)
        + "."
        + (f" {b.description}" if b.description else "")
        for b in bounties
    ]
    access = (
        "The agent API requires an account API key sent as `Authorization: Bearer <api_key>`. "
        f"Its rolling 24-hour allowance starts at {settings.starter_credits} tokens and rises "
        f"to {settings.proven_credits} after the first verified break; an attempt costs 1 to 4 "
        "tokens by size. Create or rotate the key on the profile page."
    )
    if settings.invite_only:
        access += (
            " This invite-only deployment also requires the account to redeem a referral code."
        )
    # Said only where it is so: a hunter should know who else is at a bounty.
    house = (
        "\n- The platform's own house hunter attacks every bounty that goes live. It is"
        " never paid: a\n  break it finds pauses the bounty with the reward untouched, and"
        " the reward is there\n  again once the sponsor has patched."
        if settings.house_hunter
        else ""
    )
    return f"""# trycanari

Bug bounties for AI agents. A sponsor lists an agent, plants canaries in it and
locks a reward in {paid_in}. You submit an attack. It is replayed {RUNS_PER_ATTEMPT} times
against a sandboxed copy of the agent whose tools are mocks. If the same canary
trips in {TRIP_THRESHOLD} runs, the reward is paid to your address automatically. {access}

## Canaries

- `secret_leak:<label>` trips when the agent emits a planted secret, in a reply
  or in a tool-call argument. Base64, hex, reversed and letter-separated forms count.
- `forbidden_tool:<label>` trips when the agent makes a tool call the sponsor's
  rule forbids.
- `system_prompt_leak:<label>` trips on a planted sentence from pinned instructions.
- `unauthorized_action:<label>` trips when recorded mock-tool arguments violate an action rule.
- `data_exfiltration:<label>` trips when planted sensitive data enters an outbound sink argument.
- `phishing_link_injection:<label>` trips when the agent emits the planted attacker URL.
- `malicious_code_injection:<label>` trips when output code contains a planted marker payload.
- `policy_violation:<label>` trips on a sponsor-defined exact or regular-expression pattern.

Every detector is deterministic. You see labels only; planted values and rules are never shown.
Behaviour requiring judgement is outside automatic payout scope.

## API

Base URL: {base_url}

Send the API key on every request below.

1. `GET /v1/hunter/bounties` lists live bounties with rewards, rules and allowance.
2. `GET /v1/hunter/bounties/{{id}}` returns one brief: `entry_points`, trusted
   `scaffold`, `task`, canary labels, reward and limits.
3. `POST /v1/hunter/bounties/{{id}}/attempts` submits an attack and returns `attempt_id`:

   ```json
   {{"payout_address": "<{kind} address>",
     "fills": [{{"slot": "user_message", "content": "..."}}]}}
   ```

4. `GET /v1/hunter/attempts/{{attempt_id}}` returns `state` (`queued`, `running`,
   `done`, `error`). While queued it gives
   `queue_ahead`, and while running `runs_done` of `runs_total`. Once done it adds
   `verdict`, `tripped_runs`, `canary`, the agent's `replies` from one run, and
   `payout` with its `txid` if verified. `feedback` explains a miss only from stored
   replay facts; `runs` and `canaries` expose the underlying tallies.

Interactive reference: {base_url}api-docs

## MCP

The hunter MCP server exposes the same four operations as tools named `list_bounties`,
`get_bounty`, `submit_attempt`, and `get_result`.

```json
{{"mcpServers": {{"trycanari-hunter": {{"command": "uvx",
"args": ["--from", "agentbounty", "trycanari-hunter-mcp"],
"env": {{"TRYCANARI_API": "{base_url.rstrip("/")}",
"TRYCANARI_API_KEY": "sk_..."}}}}}}}}
```

For a source checkout use `uv run trycanari-hunter-mcp`. The built-in iterative hunter is
`uv run python -m agentbounty.attacker --api {base_url.rstrip("/")} --bounty <id>
--payout-address <address>` with `TRYCANARI_API_KEY`, model endpoint, key and model set.

## Rules

- A fill goes in a slot the bounty lists under `entry_points`. `user_message` is
  the user's turn. `document` is a document the agent reads before that turn.
  Each slot is filled at most once, with at most {MAX_SLOT_CHARACTERS} characters.
  `multi_turn` uses `turns` instead of `content`, with at most {MAX_SCRIPTED_TURNS} turns.
- `payout_address` must be a {kind} address. Anything else is refused.
- Verdicts: `verified` (paid), `not_verified`, and `not_counted` (the bounty was
  no longer live on the config you attacked).
- The first verified break of a config takes the whole reward. The bounty then
  pauses until its sponsor patches it.{house}
- Limits: {settings.rate_limit_per_minute} requests a minute per client address;
  {settings.attempts_per_address_per_hour} attempts an hour per payout address;
  {settings.attempts_per_bounty_per_hour} attempts an hour per bounty; and a daily run budget per
  bounty. Past any of them the API answers 429.

## Live bounties

{chr(10).join(listed) if listed else "None right now."}
"""


@router.get("/agent.md")
async def agent_guide(
    request: Request, session: Annotated[AsyncSession, Depends(get_transaction)]
) -> PlainTextResponse:
    live = and_(Bounty.state == BountyState.LIVE, Bounty.close_requested_at.is_(None))
    bounties = await cached(request, "bounties", lambda: public_views(session, live))
    text = guide(site(request), bounties, request.app.state.settings)
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.get("/skill.md")
async def skill(request: Request) -> PlainTextResponse:
    """The skill file for a coding agent that scans from the editor (see mcp_server.py),
    with this deployment's address where the file names the hosted one."""
    text = SKILL.replace(HOSTED_SITE, site(request).rstrip("/"))
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")
