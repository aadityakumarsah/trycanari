"""Sponsor endpoints: authenticated with the API key issued at sign-up."""

from __future__ import annotations

from fastapi import APIRouter

from agentbounty.api.deps import CipherDep, OwnBounty, SettingsDep, SponsorDep, Transaction
from agentbounty.api.schemas import (
    ActivateOut,
    BountyCreate,
    BountyCreated,
    CanaryIn,
    CanaryOut,
    ConfigIn,
    ConfigOut,
    SecretCanaryIn,
    SponsorCreate,
    SponsorCreated,
)
from agentbounty.models import CanaryType
from agentbounty.services import bounty as bounty_service
from agentbounty.services.bounty import CanaryDefinition
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import MockTool, TargetConfig
from agentbounty.services.judge import ArgumentPredicate, ToolRule

router = APIRouter(prefix="/v1", tags=["sponsor"])


@router.post("/sponsors", status_code=201)
async def create_sponsor(body: SponsorCreate, session: Transaction) -> SponsorCreated:
    sponsor, api_key = await bounty_service.create_sponsor(session, body.email)
    return SponsorCreated(sponsor_id=sponsor.id, api_key=api_key)


@router.post("/bounties", status_code=201)
async def create_bounty(
    body: BountyCreate, session: Transaction, sponsor: SponsorDep, settings: SettingsDep
) -> BountyCreated:
    bounty = await bounty_service.create_bounty(
        session, sponsor, body.reward_zatoshi, settings.fee_rate
    )
    return BountyCreated(id=bounty.id, state=bounty.state)


@router.put("/bounties/{bounty_id}/config")
async def put_config(
    body: ConfigIn, session: Transaction, bounty: OwnBounty, cipher: CipherDep
) -> ConfigOut:
    try:
        config = TargetConfig(
            system_prompt=body.system_prompt,
            model=body.model,
            tools=tuple(
                MockTool(tool.name, tool.description, tool.parameters, tool.result)
                for tool in body.tools
            ),
            params=body.params,
            entry_points=tuple(body.entry_points),
        )
    except ValueError as exc:
        raise ServiceError(422, str(exc)) from exc
    row = await bounty_service.put_config(session, cipher, bounty, config)
    return ConfigOut(version=row.version, config_hash=row.config_hash)


@router.post("/bounties/{bounty_id}/canaries", status_code=201)
async def add_canary(
    body: CanaryIn, session: Transaction, bounty: OwnBounty, cipher: CipherDep
) -> CanaryOut:
    try:
        if isinstance(body, SecretCanaryIn):
            definition = CanaryDefinition(
                CanaryType.SECRET_LEAK, body.public_label, secret=body.secret
            )
        else:
            predicate = (
                None
                if body.predicate is None
                else ArgumentPredicate(
                    body.predicate.argument, body.predicate.operator, body.predicate.value
                )
            )
            definition = CanaryDefinition(
                CanaryType.FORBIDDEN_TOOL, body.public_label, rule=ToolRule(body.tool, predicate)
            )
    except ValueError as exc:
        raise ServiceError(422, str(exc)) from exc
    canary, config_hash = await bounty_service.add_canary(session, cipher, bounty, definition)
    return CanaryOut(id=canary.id, config_hash=config_hash)


@router.post("/bounties/{bounty_id}/activate")
async def activate(
    session: Transaction, bounty: OwnBounty, cipher: CipherDep, settings: SettingsDep
) -> ActivateOut:
    """Run the listing check, pin the config and go live."""
    row = await bounty_service.activate(
        session, cipher, bounty, allow_unfunded=settings.allow_unfunded_activation
    )
    return ActivateOut(state=bounty.state, config_hash=row.config_hash)
