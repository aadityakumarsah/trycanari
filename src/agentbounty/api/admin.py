"""The admin API: who is an ambassador, who is an admin, and every referral code.

Only for accounts that run the platform. To everyone else these routes answer
404, as if they were not there.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_, select

from agentbounty.api.deps import AdminDep, SettingsDep, Transaction
from agentbounty.api.schemas import (
    AdminCodeCreate,
    AdminCodeOut,
    PersonOut,
    PersonUpdate,
)
from agentbounty.models import Sponsor
from agentbounty.services import credits
from agentbounty.settings import Settings

# A cookie is only believed on a request from this site: the admin dependency sees to that.
router = APIRouter(prefix="/v1/admin", tags=["admin"])

# What an admin's grant of access is recorded as, where a referral code would be.
GRANTED = "GRANTED-BY-ADMIN"


def _person(account: Sponsor, settings: Settings) -> PersonOut:
    owner = account.google_sub is not None and account.email in settings.owners
    return PersonOut(
        id=account.id,
        email=account.email,
        name=account.name,
        picture_url=account.picture_url,
        github=account.github,
        company=account.company,
        is_admin=account.is_admin or owner,
        is_owner=owner,
        is_ambassador=account.is_ambassador,
        can_hunt=not settings.invite_only or account.referral_code is not None,
        referral_code=account.referral_code,
        created_at=account.created_at,
    )


@router.get("/people")
async def people(
    session: Transaction,
    settings: SettingsDep,
    _: AdminDep,
    q: Annotated[str, Query(max_length=100)] = "",
) -> list[PersonOut]:
    """Accounts, newest first. `q` searches names, emails, GitHub names and companies."""
    query = select(Sponsor).order_by(Sponsor.created_at.desc(), Sponsor.id).limit(200)
    wanted = q.strip()
    if wanted:
        like = f"%{wanted.replace('%', '').replace('_', '')}%"
        query = query.where(
            or_(
                Sponsor.email.ilike(like),
                Sponsor.name.ilike(like),
                Sponsor.github.ilike(like),
                Sponsor.company.ilike(like),
            )
        )
    return [_person(account, settings) for account in await session.scalars(query)]


@router.patch("/people/{account_id}")
async def update_person(
    account_id: uuid.UUID,
    body: PersonUpdate,
    session: Transaction,
    settings: SettingsDep,
    admin: AdminDep,
) -> PersonOut:
    """Make someone an ambassador or an admin, or stop them being one, or let them hunt."""
    account = await session.scalar(
        select(Sponsor).where(Sponsor.id == account_id).with_for_update()
    )
    if account is None:
        raise HTTPException(404, "no such account")
    if body.is_admin is False and account.id == admin.id:
        # Otherwise the last admin could lock everyone out, themselves included.
        raise HTTPException(409, "you cannot remove your own admin access")
    if body.is_ambassador is not None:
        account.is_ambassador = body.is_ambassador
    if body.is_admin is not None:
        account.is_admin = body.is_admin
    if body.can_hunt and account.referral_code is None:
        account.referral_code = GRANTED
    await session.flush()
    return _person(account, settings)


@router.get("/referral-codes")
async def referral_codes(session: Transaction, _: AdminDep) -> list[AdminCodeOut]:
    """Every referral code, with who made it and how many people it has brought in."""
    return [
        AdminCodeOut(
            code=code.code,
            max_uses=code.max_uses,
            used=used,
            owner_email=owner,
            created_at=code.created_at,
        )
        for code, used, owner in await credits.all_codes(session)
    ]


@router.post("/referral-codes", status_code=201)
async def create_referral_code(
    body: AdminCodeCreate, session: Transaction, admin: AdminDep
) -> AdminCodeOut:
    """Make a referral code of any size, optionally with a code of the admin's choosing."""
    code = await credits.create_code(session, admin, body.max_uses, body.code, unlimited=True)
    await session.refresh(code)
    return AdminCodeOut(
        code=code.code,
        max_uses=code.max_uses,
        used=0,
        owner_email=admin.email,
        created_at=code.created_at,
    )
