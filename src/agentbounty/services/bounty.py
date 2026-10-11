"""Bounty service: sponsors, listings, agent configs, canaries and activation.

Plaintext prompts, tools and secrets exist only in memory here. They are
encrypted before they reach a model object and decrypted only to build a
harness config or to run the listing check.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import (
    Attempt,
    AttemptState,
    Bounty,
    BountyState,
    Canary,
    CanaryType,
    Finding,
    Sponsor,
)
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import judge
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import EntryPoint, MockTool, TargetConfig
from agentbounty.services.ledger import bounty_balance
from agentbounty.services.payment import go_live_if_funded, required_balance
from agentbounty.services.wallet import Network, validate_address

# What each encrypted column is bound to (see Cipher.encrypt).
SYSTEM_PROMPT = "target_configs.system_prompt"
TOOLS = "target_configs.tools"
CANARY_SECRET = "canaries.secret"  # noqa: S105 - a column name, not a credential

# A config can be edited while the bounty is in one of these states. A live config is
# never edited: queued attempts are bound to its hash.
EDITABLE = (BountyState.DRAFT, BountyState.PAUSED)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- sponsors ---------------------------------------------------------------------------


async def create_sponsor(session: AsyncSession, email: str) -> tuple[Sponsor, str]:
    """Create a sponsor and return it with its API key, which is not stored."""
    if await session.scalar(select(Sponsor.id).where(Sponsor.email == email)):
        raise ServiceError(409, "a sponsor with this email already exists")
    api_key = "sk_" + secrets.token_urlsafe(32)
    sponsor = Sponsor(email=email, api_key_hash=hash_token(api_key))
    session.add(sponsor)
    await session.flush()
    return sponsor, api_key


async def sponsor_for_key(session: AsyncSession, api_key: str) -> Sponsor | None:
    return await session.scalar(select(Sponsor).where(Sponsor.api_key_hash == hash_token(api_key)))


# --- canaries ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryDefinition:
    """A canary in the clear: exactly one of `secret` and `rule` is set."""

    type: CanaryType
    public_label: str
    secret: str | None = None
    rule: judge.ToolRule | None = None

    def __post_init__(self) -> None:
        if not self.public_label:
            raise ValueError("a canary needs a public label")
        if self.type is CanaryType.SECRET_LEAK:
            if self.secret is None or self.rule is not None:
                raise ValueError("a secret_leak canary needs a secret and no rule")
            judge.validate_secret(self.secret)
        elif self.rule is None or self.secret is not None:
            raise ValueError("a forbidden_tool canary needs a rule and no secret")


@dataclass(frozen=True)
class LoadedCanary:
    id: uuid.UUID
    definition: CanaryDefinition


def rule_to_json(rule: judge.ToolRule) -> dict[str, Any]:
    predicate = rule.predicate
    return {
        "tool": rule.tool,
        "predicate": None
        if predicate is None
        else {
            "argument": predicate.argument,
            "operator": predicate.operator.value,
            "value": predicate.value,
        },
    }


def rule_from_json(data: Mapping[str, Any]) -> judge.ToolRule:
    predicate = data.get("predicate")
    return judge.ToolRule(
        tool=data["tool"],
        predicate=None
        if predicate is None
        else judge.ArgumentPredicate(
            predicate["argument"], judge.Operator(predicate["operator"]), predicate["value"]
        ),
    )


def _definition(row: Canary, cipher: Cipher) -> CanaryDefinition:
    if row.type is CanaryType.SECRET_LEAK:
        assert row.secret_enc is not None  # noqa: S101 - guaranteed by a check constraint
        secret = cipher.decrypt(row.secret_enc, purpose=CANARY_SECRET).decode()
        return CanaryDefinition(row.type, row.public_label, secret=secret)
    assert row.rule_json is not None  # noqa: S101 - guaranteed by a check constraint
    return CanaryDefinition(row.type, row.public_label, rule=rule_from_json(row.rule_json))


async def canary_rows(session: AsyncSession, config_id: uuid.UUID) -> list[Canary]:
    """A config's canaries in a stable order."""
    rows = await session.scalars(
        select(Canary).where(Canary.config_id == config_id).order_by(Canary.public_label, Canary.id)
    )
    return list(rows)


# --- configs ----------------------------------------------------------------------------


def config_hash(config: TargetConfig, canaries: Sequence[CanaryDefinition]) -> str:
    """SHA-256 over the canonical JSON of everything a verdict depends on: the agent and
    its canaries. Public labels are left out; renaming a canary changes no verdict."""
    canonical = {
        "system_prompt": config.system_prompt,
        "model": config.model,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "result": tool.result,
            }
            for tool in config.tools
        ],
        "params": dict(config.params),
        "entry_points": sorted(config.entry_points),
        "canaries": sorted(
            (
                {"type": c.type.value, "secret": c.secret}
                if c.rule is None
                else {"type": c.type.value, "rule": rule_to_json(c.rule)}
                for c in canaries
            ),
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class LoadedTarget:
    """A stored config, decrypted and ready for the harness and the judge."""

    config: TargetConfig
    canaries: tuple[LoadedCanary, ...]

    @property
    def hash(self) -> str:
        return config_hash(self.config, [canary.definition for canary in self.canaries])


def _tools_to_json(tools: Sequence[MockTool]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
            "result": tool.result,
        }
        for tool in tools
    ]


async def load_target(session: AsyncSession, cipher: Cipher, row: TargetConfigRow) -> LoadedTarget:
    tools = cipher.decrypt_json(row.tools_enc, purpose=TOOLS)
    config = TargetConfig(
        system_prompt=cipher.decrypt(row.system_prompt_enc, purpose=SYSTEM_PROMPT).decode(),
        model=row.model,
        tools=tuple(MockTool(**tool) for tool in tools),
        params=row.params_json,
        entry_points=tuple(EntryPoint(slot) for slot in row.entry_points_json),
    )
    canaries = tuple(
        LoadedCanary(canary.id, _definition(canary, cipher))
        for canary in await canary_rows(session, row.id)
    )
    return LoadedTarget(config, canaries)


async def latest_config(session: AsyncSession, bounty_id: uuid.UUID) -> TargetConfigRow | None:
    return await session.scalar(
        select(TargetConfigRow)
        .where(TargetConfigRow.bounty_id == bounty_id)
        .order_by(TargetConfigRow.version.desc())
        .limit(1)
    )


def _require_editable(bounty: Bounty) -> None:
    if bounty.state not in EDITABLE:
        raise ServiceError(409, f"the config cannot be changed while the bounty is {bounty.state}")


def _write_config(row: TargetConfigRow, cipher: Cipher, config: TargetConfig) -> None:
    row.system_prompt_enc = cipher.encrypt(config.system_prompt.encode(), purpose=SYSTEM_PROMPT)
    row.tools_enc = cipher.encrypt_json(_tools_to_json(config.tools), purpose=TOOLS)
    row.model = config.model
    row.params_json = dict(config.params)
    row.entry_points_json = sorted(config.entry_points)


async def _rehash(session: AsyncSession, cipher: Cipher, row: TargetConfigRow) -> str:
    await session.flush()
    row.config_hash = (await load_target(session, cipher, row)).hash
    await session.flush()
    return row.config_hash


async def _new_version(
    session: AsyncSession, bounty: Bounty, previous: TargetConfigRow | None
) -> TargetConfigRow:
    """Start the next config version, carrying over the previous version's content and canaries.

    A version that has been live is never edited again, so that findings and
    attempts bound to its hash keep meaning what they meant.
    """
    row = TargetConfigRow(
        bounty_id=bounty.id,
        version=previous.version + 1 if previous else 1,
        config_hash="",
        system_prompt_enc=previous.system_prompt_enc if previous else b"",
        tools_enc=previous.tools_enc if previous else b"",
        model=previous.model if previous else "",
        params_json=dict(previous.params_json) if previous else {},
        entry_points_json=list(previous.entry_points_json) if previous else [],
    )
    session.add(row)
    await session.flush()
    for canary in await canary_rows(session, previous.id) if previous else []:
        session.add(
            Canary(
                config_id=row.id,
                type=canary.type,
                secret_enc=canary.secret_enc,
                rule_json=canary.rule_json,
                public_label=canary.public_label,
            )
        )
    return row


async def _editable_config(
    session: AsyncSession, bounty: Bounty, *, create: bool
) -> TargetConfigRow | None:
    """The version edits apply to: the latest one, or a new one if that has been live."""
    latest = await latest_config(session, bounty.id)
    if latest is None:
        return await _new_version(session, bounty, None) if create else None
    if latest.id == bounty.live_config_id:
        return await _new_version(session, bounty, latest)
    return latest


async def put_config(
    session: AsyncSession, cipher: Cipher, bounty: Bounty, config: TargetConfig
) -> TargetConfigRow:
    """Store the config as the bounty's working version and return it with its new hash."""
    _require_editable(bounty)
    row = await _editable_config(session, bounty, create=True)
    assert row is not None  # noqa: S101 - create=True always returns a row
    _write_config(row, cipher, config)
    await _rehash(session, cipher, row)
    return row


async def add_canary(
    session: AsyncSession, cipher: Cipher, bounty: Bounty, definition: CanaryDefinition
) -> tuple[Canary, str]:
    """Add a canary to the working config. Returns it with the config's new hash."""
    _require_editable(bounty)
    row = await _editable_config(session, bounty, create=False)
    if row is None:
        raise ServiceError(409, "upload a config before adding canaries")
    canary = Canary(
        config_id=row.id,
        type=definition.type,
        public_label=definition.public_label,
        secret_enc=None
        if definition.secret is None
        else cipher.encrypt(definition.secret.encode(), purpose=CANARY_SECRET),
        rule_json=None if definition.rule is None else rule_to_json(definition.rule),
    )
    session.add(canary)
    return canary, await _rehash(session, cipher, row)


# --- lifecycle --------------------------------------------------------------------------


async def create_bounty(
    session: AsyncSession,
    sponsor: Sponsor,
    reward_zatoshi: int,
    fee_rate: Decimal,
    title: str = "Untitled agent",
    description: str = "",
) -> Bounty:
    bounty = Bounty(
        sponsor_id=sponsor.id,
        title=title,
        description=description,
        reward_zatoshi=reward_zatoshi,
        fee_rate=fee_rate,
        deposit_code="dep-" + secrets.token_hex(8),
    )
    session.add(bounty)
    await session.flush()
    return bounty


async def lock_bounty(session: AsyncSession, bounty_id: uuid.UUID) -> Bounty | None:
    """The bounty, with its row locked until the transaction ends."""
    return await session.scalar(select(Bounty).where(Bounty.id == bounty_id).with_for_update())


async def activate(
    session: AsyncSession, cipher: Cipher, bounty: Bounty, *, allow_unfunded: bool
) -> TargetConfigRow:
    """Run the listing check, pin the working config and go live."""
    if bounty.state not in EDITABLE:
        raise ServiceError(409, f"the bounty is already {bounty.state}")
    row = await _listable(session, cipher, bounty)
    funded = await bounty_balance(session, bounty.id) >= required_balance(bounty)
    if not (funded or allow_unfunded):
        raise ServiceError(409, "the bounty must be funded before it can go live")
    bounty.state = BountyState.LIVE
    bounty.live_config_id = row.id
    await session.flush()
    return row


async def _listable(session: AsyncSession, cipher: Cipher, bounty: Bounty) -> TargetConfigRow:
    """The working config, if every canary can trip on it, it has not been broken, and,
    for a bounty that has been broken before, the paid attack no longer breaks it."""
    row = await latest_config(session, bounty.id)
    if row is None:
        raise ServiceError(409, "upload a config first")
    # Imported here: both modules build on the types defined above.
    from agentbounty.services import listing, rearm

    problems = await listing.problems(await load_target(session, cipher, row))
    if problems:
        raise ServiceError(409, "listing check failed: " + "; ".join(problems))
    broken = await session.scalar(
        select(Finding.id).where(
            Finding.bounty_id == bounty.id, Finding.config_hash == row.config_hash
        )
    )
    if broken:
        raise ServiceError(409, "this config has been broken; upload a patched config first")
    # A bounty that has been broken reopens only on a config the paid attack no longer breaks.
    await rearm.require_passed(session, bounty, row)
    return row


async def fund(
    session: AsyncSession,
    cipher: Cipher,
    bounty: Bounty,
    refund_address: str,
    network: Network,
) -> int:
    """Start funding: issue a one-time deposit code and wait for the deposit.

    Returns the zatoshi still due. The listing check runs here, because the
    payment process that later sets the bounty live cannot read the config.
    Asking again while the deposit is awaited changes nothing and returns the
    same code, so a sponsor who asks twice does not orphan a payment in flight.
    """
    if bounty.close_requested_at is not None or bounty.state is BountyState.CLOSED:
        raise ServiceError(409, "the bounty is closed or closing")
    if bounty.state is BountyState.LIVE:
        raise ServiceError(409, "the bounty is already live")
    if not validate_address(refund_address, network=network):
        raise ServiceError(422, f"refund_address is not a shielded {network} address")
    sponsor = await session.get_one(Sponsor, bounty.sponsor_id)
    sponsor.refund_address = refund_address
    if bounty.state is not BountyState.AWAITING_DEPOSIT:
        await _listable(session, cipher, bounty)
        bounty.deposit_code = "dep-" + secrets.token_hex(8)
        bounty.state = BountyState.AWAITING_DEPOSIT
    # Already covered, by an earlier overpayment say: no deposit to wait for.
    await go_live_if_funded(session, bounty)
    await session.flush()
    return max(0, required_balance(bounty) - await bounty_balance(session, bounty.id))


async def pause(session: AsyncSession, bounty: Bounty) -> None:
    """Stop taking attempts until the sponsor resumes.

    Refused while attempts are queued or running: pausing makes them not
    count, so allowing it would let a sponsor dodge an attack already submitted.
    """
    if bounty.state is not BountyState.LIVE or bounty.close_requested_at is not None:
        raise ServiceError(409, "only a live bounty can be paused")
    in_flight = await session.scalar(
        select(Attempt.id)
        .where(
            Attempt.bounty_id == bounty.id,
            Attempt.state.in_((AttemptState.QUEUED, AttemptState.RUNNING)),
        )
        .limit(1)
    )
    if in_flight:
        raise ServiceError(409, "attempts are still being judged; pause once the queue is empty")
    bounty.state = BountyState.PAUSED
    await session.flush()


async def request_close(
    session: AsyncSession, bounty: Bounty, refund_address: str | None, network: Network
) -> None:
    """Ask for the bounty to be closed and its unspent escrow refunded.

    Nothing is paid here. The bounty stops taking attempts at once; the payment
    process closes it and sends the refund once attempts already in flight have
    been judged and any break among them paid.
    """
    if bounty.state is BountyState.CLOSED:
        raise ServiceError(409, "the bounty is already closed")
    sponsor = await session.get_one(Sponsor, bounty.sponsor_id)
    if refund_address is not None:
        if not validate_address(refund_address, network=network):
            raise ServiceError(422, f"refund_address is not a shielded {network} address")
        sponsor.refund_address = refund_address
    if not sponsor.refund_address and await bounty_balance(session, bounty.id) > 0:
        raise ServiceError(422, "a refund_address is needed to return the escrow")
    if bounty.close_requested_at is None:
        bounty.close_requested_at = datetime.now(UTC)
    await session.flush()
