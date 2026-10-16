"""Managed assurance engagements: private scope, lifecycle, and readiness evidence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import AssuranceEngagement, Bounty, TargetConfig
from agentbounty.services import bounty as bounty_service
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError

WORKFLOW = "assurance.workflow"
FEARED_FAILURE = "assurance.feared_failure"
IDENTITY_CONTROLS = "assurance.identity_controls"
HUMAN_APPROVAL = "assurance.human_approval"
OPERATOR_NOTES = "assurance.operator_notes"
LIMITATIONS = "assurance.limitations"

ACTIVE = {"requested", "scoping", "testing", "remediation", "retest"}


@dataclass(frozen=True)
class Scope:
    workflow: str
    feared_failure: str
    identity_controls: str
    human_approval: str


@dataclass(frozen=True)
class Readiness:
    agent_configured: bool
    boundaries_defined: bool
    attack_surface_declared: bool
    consequence_documented: bool
    identity_documented: bool
    approval_documented: bool

    @property
    def ready(self) -> bool:
        return all(
            (
                self.agent_configured,
                self.boundaries_defined,
                self.attack_surface_declared,
                self.consequence_documented,
                self.identity_documented,
                self.approval_documented,
            )
        )


def _encrypt(cipher: Cipher, value: str, purpose: str) -> bytes:
    return cipher.encrypt(value.strip().encode(), purpose=purpose)


def _decrypt(cipher: Cipher, value: bytes, purpose: str) -> str:
    return cipher.decrypt(value, purpose=purpose).decode()


def optional_text(cipher: Cipher, value: bytes | None, purpose: str) -> str:
    return _decrypt(cipher, value, purpose) if value is not None else ""


def scope_of(cipher: Cipher, engagement: AssuranceEngagement) -> Scope:
    return Scope(
        workflow=_decrypt(cipher, engagement.workflow_enc, WORKFLOW),
        feared_failure=_decrypt(cipher, engagement.feared_failure_enc, FEARED_FAILURE),
        identity_controls=_decrypt(cipher, engagement.identity_controls_enc, IDENTITY_CONTROLS),
        human_approval=_decrypt(cipher, engagement.human_approval_enc, HUMAN_APPROVAL),
    )


async def for_bounty(
    session: AsyncSession, bounty_id: uuid.UUID, *, lock: bool = False
) -> AssuranceEngagement | None:
    query = select(AssuranceEngagement).where(AssuranceEngagement.bounty_id == bounty_id)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def save(
    session: AsyncSession,
    cipher: Cipher,
    bounty: Bounty,
    *,
    service: str,
    stage: str,
    deployment: str,
    untrusted_inputs: list[str],
    sensitive_data: list[str],
    consequential_actions: list[str],
    workflow: str,
    feared_failure: str,
    identity_controls: str,
    human_approval: str,
) -> AssuranceEngagement:
    engagement = await for_bounty(session, bounty.id, lock=True)
    if engagement is not None and engagement.status in {
        "testing",
        "remediation",
        "retest",
        "complete",
        "cancelled",
    }:
        raise ServiceError(409, "the assessment scope is locked while evidence is being produced")
    values: dict[str, Any] = {
        "service": service,
        "stage": stage,
        "deployment": deployment,
        "untrusted_inputs_json": list(dict.fromkeys(untrusted_inputs)),
        "sensitive_data_json": list(dict.fromkeys(sensitive_data)),
        "consequential_actions_json": list(dict.fromkeys(consequential_actions)),
        "workflow_enc": _encrypt(cipher, workflow, WORKFLOW),
        "feared_failure_enc": _encrypt(cipher, feared_failure, FEARED_FAILURE),
        "identity_controls_enc": _encrypt(cipher, identity_controls, IDENTITY_CONTROLS),
        "human_approval_enc": _encrypt(cipher, human_approval, HUMAN_APPROVAL),
        "updated_at": datetime.now(UTC),
    }
    if engagement is None:
        engagement = AssuranceEngagement(bounty_id=bounty.id, **values)
        session.add(engagement)
    else:
        for name, value in values.items():
            setattr(engagement, name, value)
    await session.flush()
    return engagement


async def readiness(
    session: AsyncSession, cipher: Cipher, bounty: Bounty, engagement: AssuranceEngagement
) -> Readiness:
    config = await bounty_service.latest_config(session, bounty.id)
    canaries = await bounty_service.canary_rows(session, config.id) if config is not None else []
    scope = scope_of(cipher, engagement)
    return Readiness(
        agent_configured=config is not None,
        boundaries_defined=bool(canaries),
        attack_surface_declared=bool(engagement.untrusted_inputs_json),
        consequence_documented=bool(scope.feared_failure.strip()),
        identity_documented=bool(scope.identity_controls.strip()),
        approval_documented=bool(scope.human_approval.strip()),
    )


async def require_current_config(session: AsyncSession, bounty: Bounty) -> TargetConfig:
    config = await bounty_service.latest_config(session, bounty.id)
    if config is None:
        raise ServiceError(409, "configure the agent before starting assessment testing")
    return config


async def submit(session: AsyncSession, cipher: Cipher, bounty: Bounty) -> AssuranceEngagement:
    engagement = await for_bounty(session, bounty.id, lock=True)
    if engagement is None:
        raise ServiceError(409, "save the assessment scope before requesting the sprint")
    if engagement.status not in {"draft", "requested"}:
        raise ServiceError(409, "this assessment can no longer be requested")
    engagement.status = "requested"
    engagement.requested_at = engagement.requested_at or datetime.now(UTC)
    engagement.updated_at = datetime.now(UTC)
    # A request may be submitted before setup is complete: hands-on scoping is part of
    # the managed service. Readiness is returned so the missing work remains explicit.
    await readiness(session, cipher, bounty, engagement)
    await session.flush()
    return engagement


def apply_admin_update(
    cipher: Cipher,
    engagement: AssuranceEngagement,
    *,
    status: str | None,
    quoted_price_cents: int | None,
    operator_notes: str | None,
    limitations: str | None,
) -> None:
    now = datetime.now(UTC)
    if status is not None:
        if engagement.status in {"complete", "cancelled"} and status != engagement.status:
            raise ServiceError(409, "a completed or cancelled assessment cannot be reopened")
        engagement.status = status
        if status in ACTIVE and engagement.started_at is None:
            engagement.started_at = now
        if status == "complete":
            engagement.completed_at = now
        elif engagement.completed_at is not None:
            engagement.completed_at = None
    if quoted_price_cents is not None:
        engagement.quoted_price_cents = quoted_price_cents
    if operator_notes is not None:
        engagement.operator_notes_enc = (
            _encrypt(cipher, operator_notes, OPERATOR_NOTES) if operator_notes.strip() else None
        )
    if limitations is not None:
        engagement.limitations_enc = (
            _encrypt(cipher, limitations, LIMITATIONS) if limitations.strip() else None
        )
    engagement.updated_at = now
