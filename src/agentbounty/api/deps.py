"""Dependencies shared by the routers."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.api.session import session_identity, sponsor_of
from agentbounty.db import get_transaction
from agentbounty.models import Bounty, Sponsor
from agentbounty.services import billing, credits
from agentbounty.services import bounty as bounty_service
from agentbounty.services.crypto import Cipher
from agentbounty.settings import Settings

Transaction = Annotated[AsyncSession, Depends(get_transaction)]


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_cipher(request: Request) -> Cipher:
    cipher: Cipher = request.app.state.cipher
    return cipher


def get_biller(request: Request) -> billing.BillingBackend | None:
    """The invoice backend, or None where this deployment bills nobody."""
    biller: billing.BillingBackend | None = request.app.state.biller
    return biller


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return token.strip()


async def current_sponsor(
    request: Request,
    session: Transaction,
    authorization: Annotated[str | None, Header()] = None,
) -> Sponsor:
    """The caller: a script with a bearer API key, or a browser with a session cookie.

    A bearer token wins. A cookie is honoured only on a request that provably
    came from this site (see api/session.py).
    """
    if authorization is not None:
        sponsor = await bounty_service.sponsor_for_key(session, bearer_token(authorization))
        if sponsor is None:
            raise HTTPException(401, "unknown API key", headers={"WWW-Authenticate": "Bearer"})
        return sponsor
    sponsor = await sponsor_of(session, session_identity(request))
    if sponsor is None:
        raise HTTPException(
            401, "sign in or send an API key", headers={"WWW-Authenticate": "Bearer"}
        )
    return sponsor


async def current_admin(
    request: Request, sponsor: Annotated[Sponsor, Depends(current_sponsor)]
) -> Sponsor:
    """An account that runs the platform. To anyone else the admin API does not exist."""
    if not credits.is_admin(sponsor, request.app.state.settings):
        raise HTTPException(404, "not found")
    return sponsor


async def maybe_sponsor(
    request: Request,
    session: Transaction,
    authorization: Annotated[str | None, Header()] = None,
) -> Sponsor | None:
    """The account making a request that may also be made without one."""
    if authorization is None and session_identity(request) is None:
        return None
    return await current_sponsor(request, session, authorization)


async def own_bounty(
    bounty_id: uuid.UUID,
    session: Transaction,
    sponsor: Annotated[Sponsor, Depends(current_sponsor)],
) -> Bounty:
    """The sponsor's bounty, locked for the request. Someone else's bounty is a 404."""
    bounty = await bounty_service.lock_bounty(session, bounty_id)
    if bounty is None or bounty.sponsor_id != sponsor.id or bounty.deleted_at is not None:
        raise HTTPException(404, "bounty not found")
    return bounty


SettingsDep = Annotated[Settings, Depends(get_settings)]
CipherDep = Annotated[Cipher, Depends(get_cipher)]
BillerDep = Annotated[billing.BillingBackend | None, Depends(get_biller)]
SponsorDep = Annotated[Sponsor, Depends(current_sponsor)]
MaybeSponsor = Annotated[Sponsor | None, Depends(maybe_sponsor)]
AdminDep = Annotated[Sponsor, Depends(current_admin)]
OwnBounty = Annotated[Bounty, Depends(own_bounty)]
BearerToken = Annotated[str, Depends(bearer_token)]
