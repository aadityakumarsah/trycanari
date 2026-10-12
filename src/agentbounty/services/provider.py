"""The platform's model provider: where agents run when a sponsor names no endpoint
of their own.

It comes from the environment (MODEL_API_BASE, MODEL_API_KEY) unless the owner has
set another in the admin page, which then wins. The owner's choice is stored
encrypted, so the provider and its key can be changed without a redeploy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import Bounty, BountyState, PlatformSetting
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services.crypto import Cipher
from agentbounty.settings import Settings

NAME = "model"
PURPOSE = "platform_settings.model"


@dataclass(frozen=True)
class Provider:
    base: str
    key: str = field(repr=False)
    source: Literal["admin", "environment"]
    updated_at: datetime | None = None
    updated_by: uuid.UUID | None = None

    @property
    def key_hint(self) -> str:
        """Enough of the key to recognise it by, and no more."""
        return f"…{self.key[-4:]}" if len(self.key) >= 12 else "…"


def from_environment(settings: Settings) -> Provider | None:
    if settings.model_backend != "real" or not settings.model_api_base:
        return None
    key = settings.model_api_key.get_secret_value() if settings.model_api_key else ""
    return Provider(settings.model_api_base, key, "environment") if key else None


async def stored(session: AsyncSession, cipher: Cipher) -> Provider | None:
    """What the owner set, if anything."""
    row = await session.get(PlatformSetting, NAME)
    if row is None:
        return None
    value = cipher.decrypt_json(row.value_enc, purpose=PURPOSE)
    return Provider(value["base"], value["key"], "admin", row.updated_at, row.updated_by)


async def current(session: AsyncSession, cipher: Cipher, settings: Settings) -> Provider | None:
    """The provider in use: the owner's, else the environment's, else none (the
    built-in test model)."""
    return await stored(session, cipher) or from_environment(settings)


async def save(
    session: AsyncSession, cipher: Cipher, base: str, key: str, admin_id: uuid.UUID
) -> Provider:
    value = cipher.encrypt_json({"base": base, "key": key}, purpose=PURPOSE)
    row = await session.get(PlatformSetting, NAME)
    now = datetime.now(UTC)
    if row is None:
        session.add(
            PlatformSetting(name=NAME, value_enc=value, updated_by=admin_id, updated_at=now)
        )
    else:
        row.value_enc, row.updated_by, row.updated_at = value, admin_id, now
    await session.flush()
    return Provider(base, key, "admin", now, admin_id)


async def clear(session: AsyncSession) -> bool:
    """Go back to the environment's provider. False if nothing was set."""
    row = await session.get(PlatformSetting, NAME)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def models_in_use(session: AsyncSession) -> list[str]:
    """The model names open bounties run on the platform's provider. A new provider
    that does not offer one of them would break that bounty's replays."""
    names = await session.scalars(
        select(TargetConfigRow.model)
        .join(Bounty, Bounty.live_config_id == TargetConfigRow.id)
        .where(
            Bounty.state.in_((BountyState.LIVE, BountyState.PAUSED)),
            Bounty.deleted_at.is_(None),
            TargetConfigRow.endpoint_base.is_(None),
        )
        .distinct()
    )
    return sorted(names)
