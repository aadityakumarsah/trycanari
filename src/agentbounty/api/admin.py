"""The admin API: who can hunt, who is an ambassador, who is an admin, and every
referral code.

Only for accounts that run the platform. To everyone else these routes answer
404, as if they were not there, and they are left out of the published API
description. Everything done here is written to a log that cannot be edited.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from agentbounty.api.deps import AdminDep, CipherDep, SettingsDep, Transaction
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.schemas import (
    ActionsPage,
    AdminActionOut,
    AdminCodeCreate,
    AdminCodeOut,
    CodesDeleted,
    CodesPage,
    CodeUserOut,
    PeoplePage,
    PersonOut,
    PersonUpdate,
    ProviderIn,
    ProviderOut,
    ProviderTestIn,
    ProviderTestOut,
)
from agentbounty.models import AdminAction, Sponsor
from agentbounty.services import credits, endpoint, plans, provider
from agentbounty.services.crypto import Cipher
from agentbounty.settings import Settings

logger = logging.getLogger(__name__)

# A cookie is only believed on a request from this site: the admin dependency sees to that.
router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    include_in_schema=False,
    dependencies=[Depends(limit_by_client)],
)

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


async def _person(session: AsyncSession, account: Sponsor, settings: Settings) -> PersonOut:
    owner = credits.is_owner(account, settings)
    allowed = await credits.allowance(session, account, settings)
    return PersonOut(
        id=account.id,
        email=account.email,
        name=account.name,
        picture_url=account.uploaded_picture_url or account.picture_url,
        github=account.github,
        company=account.company,
        is_admin=account.is_admin or owner,
        is_owner=owner,
        is_ambassador=account.is_ambassador,
        can_hunt=credits.can_hunt(account, settings),
        hunting_granted=account.hunting_granted,
        can_sponsor=credits.can_sponsor(account, settings),
        sponsoring_granted=account.sponsoring_granted,
        referral_code=account.referral_code,
        sponsor_code=account.sponsor_code,
        banned=credits.is_banned(account),
        credits_balance=allowed.balance,
        credits_cap=allowed.cap,
        credit_limit=account.credit_limit,
        verified_breaks=allowed.breaks,
        plan_until=account.plan_until,
        created_at=account.created_at,
    )


def record(
    session: AsyncSession, admin: Sponsor, action: str, target: Sponsor | None, detail: str
) -> None:
    """Write what an admin did to the log, in the same transaction as the doing of it."""
    session.add(
        AdminAction(
            admin_id=admin.id, action=action, target_id=target.id if target else None, detail=detail
        )
    )
    logger.info(
        "admin action",
        extra={
            "admin_id": str(admin.id),
            "action": action,
            "target_id": str(target.id) if target else None,
            "detail": detail,
        },
    )


@router.get("/people")
async def people(
    session: Transaction,
    settings: SettingsDep,
    _: AdminDep,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Limit = 50,
    offset: Offset = 0,
) -> PeoplePage:
    """A page of accounts, newest first. `q` searches names, emails, GitHub names and companies."""
    # Demo accounts are nobody: they are not listed, so they cannot be given a role either.
    matching: list[ColumnElement[bool]] = [Sponsor.is_demo.is_(False)]
    wanted = q.strip()
    if wanted:
        # The search text is data, never part of the pattern's syntax.
        like = "%" + wanted.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        matching.append(
            or_(
                Sponsor.email.ilike(like, escape="\\"),
                Sponsor.name.ilike(like, escape="\\"),
                Sponsor.github.ilike(like, escape="\\"),
                Sponsor.company.ilike(like, escape="\\"),
            )
        )
    total = await session.scalar(select(func.count()).select_from(Sponsor).where(*matching))
    found = await session.scalars(
        select(Sponsor)
        .where(*matching)
        .order_by(Sponsor.created_at.desc(), Sponsor.id)
        .limit(limit)
        .offset(offset)
    )
    return PeoplePage(
        items=[await _person(session, account, settings) for account in found.all()],
        total=int(total or 0),
    )


@router.patch("/people/{account_id}")
async def update_person(
    account_id: uuid.UUID,
    body: PersonUpdate,
    session: Transaction,
    settings: SettingsDep,
    admin: AdminDep,
) -> PersonOut:
    """Make someone an ambassador or an admin, or stop them being one; let them hunt
    without a code, or take that back; ban them; set or reset their tokens."""
    account = await session.scalar(
        select(Sponsor).where(Sponsor.id == account_id).with_for_update()
    )
    if account is None:
        raise HTTPException(404, "no such account")
    # Who runs the platform is the owner's decision alone. An admin can do everything else
    # here, but cannot make another admin or unmake one.
    changes_admin = body.is_admin is not None and body.is_admin != account.is_admin
    if changes_admin and not credits.is_owner(admin, settings):
        raise HTTPException(403, "only the owner can make or remove admins")
    for field in ("is_ambassador", "is_admin", "hunting_granted", "sponsoring_granted"):
        value = getattr(body, field)
        if value is not None and value != getattr(account, field):
            setattr(account, field, value)
            record(session, admin, f"{field}={str(value).lower()}", account, account.email)
    if body.banned is not None and body.banned != credits.is_banned(account):
        if body.banned:
            # Whoever runs the platform is not locked out of it from here: an owner never,
            # and an admin only once the owner has stopped them being one.
            if account.id == admin.id:
                raise HTTPException(403, "you cannot ban yourself")
            if credits.is_admin(account, settings):
                raise HTTPException(403, "an admin cannot be banned; remove them as an admin first")
        account.banned_at = datetime.now(UTC) if body.banned else None
        record(session, admin, f"banned={str(body.banned).lower()}", account, account.email)
    # Null is a value here: it puts the account back on its tier's cap.
    if "credit_limit" in body.model_fields_set and body.credit_limit != account.credit_limit:
        account.credit_limit = body.credit_limit
        said = "the tier's" if body.credit_limit is None else str(body.credit_limit)
        record(session, admin, "credit_limit", account, said)
    if body.plan_days is not None:
        if body.plan_days:
            until = plans.extend(account, body.plan_days)
            record(session, admin, "plan_extended", account, until.date().isoformat())
        elif account.plan_until is not None:
            plans.end(account)
            record(session, admin, "plan_ended", account, account.email)
    if body.reset_credits:
        account.credits_reset_at = datetime.now(UTC)
        record(session, admin, "credits_reset", account, account.email)
    await session.flush()
    return await _person(session, account, settings)


@router.get("/actions")
async def actions(
    session: Transaction, _: AdminDep, limit: Limit = 50, offset: Offset = 0
) -> ActionsPage:
    """A page of what admins have done, newest first. The log cannot be edited."""
    by = aliased(Sponsor)
    to = aliased(Sponsor)
    total = await session.scalar(select(func.count()).select_from(AdminAction))
    rows = await session.execute(
        select(AdminAction, by.email, to.email)
        .join(by, by.id == AdminAction.admin_id)
        .outerjoin(to, to.id == AdminAction.target_id)
        .order_by(AdminAction.created_at.desc(), AdminAction.id)
        .limit(limit)
        .offset(offset)
    )
    return ActionsPage(
        items=[
            AdminActionOut(
                admin_email=admin_email,
                action=action.action,
                target_email=target_email,
                detail=action.detail,
                created_at=action.created_at,
            )
            for action, admin_email, target_email in rows
        ],
        total=int(total or 0),
    )


@router.get("/referral-codes")
async def referral_codes(
    session: Transaction, _: AdminDep, limit: Limit = 50, offset: Offset = 0
) -> CodesPage:
    """A page of every referral code, with who made it and who used it."""
    lines, total = await credits.all_codes(session, limit=limit, offset=offset)
    return CodesPage(
        items=[
            AdminCodeOut(
                code=line.code.code,
                kind=line.code.kind,  # type: ignore[arg-type]
                used_by=line.used_by,
                max_uses=line.code.max_uses,
                uses=len(line.people),
                users=[
                    CodeUserOut(email=who.email, name=who.name, joined_at=who.joined_at)
                    for who in line.people
                ],
                owner_email=line.owner_email,
                created_at=line.code.created_at,
            )
            for line in lines
        ],
        total=total,
    )


@router.post("/referral-codes", status_code=201)
async def create_referral_codes(
    body: AdminCodeCreate, session: Transaction, admin: AdminDep
) -> list[AdminCodeOut]:
    """Make referral codes: many at once, or one of the admin's choosing. Each works
    once, unless it is made for more people."""
    made = await credits.create_codes(
        session,
        admin,
        body.count,
        body.code,
        unlimited=True,
        kind=body.kind,
        max_uses=body.max_uses,
    )
    for code in made:
        await session.refresh(code)
    shared = f", each for {body.max_uses} people" if body.max_uses > 1 else ""
    record(session, admin, "codes_created", None, f"{len(made)} {body.kind} code(s){shared}")
    return [
        AdminCodeOut(
            code=code.code,
            kind=body.kind,
            used_by=None,
            max_uses=code.max_uses,
            owner_email=admin.email,
            created_at=code.created_at,
        )
        for code in made
    ]


@router.delete("/referral-codes")
async def delete_referral_codes(
    which: Literal["used_up", "unused"], session: Transaction, admin: AdminDep
) -> CodesDeleted:
    """Delete every code with no use left, or every code nobody has used. Whoever joined
    with a code keeps the access it gave them."""
    deleted = await credits.delete_codes(session, which)
    if deleted:
        said = "used-up" if which == credits.USED_UP else "unused"
        record(session, admin, "codes_deleted", None, f"{deleted} {said} code(s)")
    return CodesDeleted(deleted=deleted)


@router.delete("/referral-codes/{code}")
async def delete_referral_code(code: str, session: Transaction, admin: AdminDep) -> CodesDeleted:
    """Delete one code, used or not. Whoever joined with it keeps their access."""
    found = await credits.delete_code(session, code[:40])
    if found is None:
        raise HTTPException(404, "no such code")
    record(session, admin, "codes_deleted", None, f"the code {found.code}")
    return CodesDeleted(deleted=1)


# --- the model provider -------------------------------------------------------------------


def _owner_only(admin: Sponsor, settings: Settings) -> None:
    """Where every agent runs, and on whose key, is the owner's decision alone."""
    if not credits.is_owner(admin, settings):
        raise HTTPException(403, "only the owner can see or change the model provider")


async def _provider_out(session: AsyncSession, cipher: Cipher, settings: Settings) -> ProviderOut:
    using = await provider.current(session, cipher, settings)
    return ProviderOut(
        source=using.source if using else "none",
        api_base=using.base if using else None,
        key_hint=using.key_hint if using else None,
        updated_at=using.updated_at if using else None,
        models_in_use=await provider.models_in_use(session),
    )


async def _address_and_key(
    body: ProviderIn, session: AsyncSession, cipher: Cipher, settings: Settings
) -> tuple[str, str]:
    """The address in its stored form and the key to use with it, or a refusal."""
    allow_private = settings.allow_private_model_endpoints
    try:
        base = endpoint.normalize(body.api_base, allow_private=allow_private)
        await endpoint.check_reachable_host(base, allow_private=allow_private)
    except endpoint.EndpointError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.api_key is not None:
        return base, body.api_key.get_secret_value()
    # A key is reused only with the address it is already used with.
    using = await provider.current(session, cipher, settings)
    if using is None or endpoint.normalize(using.base, allow_private=True) != base:
        raise HTTPException(422, "enter the API key for this provider")
    return base, using.key


@router.get("/model")
async def read_provider(
    session: Transaction, cipher: CipherDep, settings: SettingsDep, admin: AdminDep
) -> ProviderOut:
    """Which model provider the platform uses, and where that choice comes from."""
    _owner_only(admin, settings)
    return await _provider_out(session, cipher, settings)


@router.post("/model/test")
async def test_provider(
    body: ProviderTestIn,
    request: Request,
    session: Transaction,
    cipher: CipherDep,
    settings: SettingsDep,
    admin: AdminDep,
) -> ProviderTestOut:
    """Try a provider before it is saved: ask which models it offers and, given a model
    name, send it one short message."""
    _owner_only(admin, settings)
    base, key = await _address_and_key(body, session, cipher, settings)
    model = request.app.state.model_connector(base, key)
    try:
        names = await model.models()
        if body.model is not None:
            result = await endpoint.test(model, body.model)
            ok, detail, taken = result.ok, result.detail, result.milliseconds
        elif names:
            ok, detail, taken = True, f"The provider answered and lists {len(names)} models.", 0
        else:
            ok, taken = False, 0
            detail = "The provider did not list any models. Check the address and the key."
    finally:
        await model.aclose()
    in_use = await provider.models_in_use(session)
    # Only a provider that says what it offers can be said to lack something.
    missing = [name for name in in_use if name not in names] if names else []
    return ProviderTestOut(
        ok=ok, detail=detail, milliseconds=taken, models=names, missing_models=missing
    )


@router.put("/model")
async def set_provider(
    body: ProviderIn,
    request: Request,
    session: Transaction,
    cipher: CipherDep,
    settings: SettingsDep,
    admin: AdminDep,
) -> ProviderOut:
    """Use this provider from now on, in place of the one in the server's settings. The
    workers pick the change up within seconds; nothing is restarted."""
    _owner_only(admin, settings)
    base, key = await _address_and_key(body, session, cipher, settings)
    await provider.save(session, cipher, base, key, admin.id)
    record(session, admin, "model_provider=set", None, urlsplit(base).hostname or base)
    request.app.state.models_listed = None
    return await _provider_out(session, cipher, settings)


@router.delete("/model")
async def reset_provider(
    request: Request,
    session: Transaction,
    cipher: CipherDep,
    settings: SettingsDep,
    admin: AdminDep,
) -> ProviderOut:
    """Go back to the provider in the server's own settings."""
    _owner_only(admin, settings)
    if await provider.clear(session):
        record(session, admin, "model_provider=reset", None, "back to the server's settings")
    request.app.state.models_listed = None
    return await _provider_out(session, cipher, settings)
