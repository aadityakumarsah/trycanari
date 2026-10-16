"""The server-rendered hunter workspace.

Every mutation is an ordinary form request and every live update is an HTMX fragment.
Draft attack text is never put in browser storage; submitted attacks live in the attempts
table and can be reopened from the account-owned history.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from agentbounty.api.deps import CipherDep, SettingsDep, SponsorDep, Transaction
from agentbounty.models import Attempt, Bounty, BountyState
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import attempts, credits, hunts, studio
from agentbounty.services import bounty as bounty_service
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import EntryPoint, fills_from_json
from agentbounty.services.wallet import address_rule

PACKAGE = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=PACKAGE / "templates")
CSS = PACKAGE / "static" / "studio.css"
STYLESHEET_PATH = "/studio-assets/studio-b313559.css"

router = APIRouter(tags=["studio"], include_in_schema=False)

HTML_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' https://unpkg.com;"
    " style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none';"
    " form-action 'self'",
}


def _html(request: Request, name: str, context: dict[str, Any], status: int = 200) -> Response:
    return TEMPLATES.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status,
        headers=HTML_HEADERS,
    )


async def _form(request: Request) -> dict[str, list[str]]:
    body = await request.body()
    if len(body) > 160_000:
        raise studio.StudioInputError("the attack form is too large")
    try:
        return parse_qs(body.decode(), keep_blank_values=True, max_num_fields=100)
    except (UnicodeDecodeError, ValueError) as exc:
        raise studio.StudioInputError("the attack form could not be read") from exc


async def _target(
    session: AsyncSession, cipher: CipherDep, bounty_id: uuid.UUID
) -> tuple[Bounty, bounty_service.LoadedTarget]:
    bounty = await session.get(Bounty, bounty_id)
    if bounty is None or bounty.live_config_id is None or bounty.deleted_at is not None:
        raise HTTPException(404, "bounty not found")
    row = await session.get_one(TargetConfigRow, bounty.live_config_id)
    return bounty, await bounty_service.load_target(session, cipher, row)


async def _history(session: AsyncSession, account_id: uuid.UUID, bounty_id: uuid.UUID) -> list[Any]:
    return await hunts.listing(session, account_id, bounty_id=bounty_id, limit=None)


async def _owned_fills(
    session: AsyncSession,
    attempt_id: uuid.UUID | None,
    account_id: uuid.UUID,
    bounty_id: uuid.UUID,
) -> tuple[list[Any], str]:
    if attempt_id is None:
        return [], ""
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None or attempt.account_id != account_id or attempt.bounty_id != bounty_id:
        raise HTTPException(404, "attempt not found")
    return fills_from_json(attempt.payload_json), attempt.payout_address


def _editor_context(
    bounty_id: uuid.UUID, target: bounty_service.LoadedTarget, fills: list[Any]
) -> dict[str, Any]:
    return {
        "bounty_id": bounty_id,
        "config": target.config,
        "fields": studio.fields_for(target.config, fills),
        "slot_names": studio.SLOT_NAMES,
        "slot_help": studio.SLOT_HELP,
    }


@router.get("/studio-assets/studio.css")
@router.get(STYLESHEET_PATH)
async def stylesheet() -> FileResponse:
    return FileResponse(
        CSS,
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/studio/{bounty_id}", response_class=HTMLResponse)
async def page(
    request: Request,
    bounty_id: uuid.UUID,
    session: Transaction,
    account: SponsorDep,
    cipher: CipherDep,
    settings: SettingsDep,
    fork: uuid.UUID | None = None,
) -> Response:
    bounty, target = await _target(session, cipher, bounty_id)
    fills, fork_address = await _owned_fills(session, fork, account.id, bounty.id)
    history = await _history(session, account.id, bounty.id)
    allowed = await credits.allowance(session, account, settings)
    last_address = fork_address or (history[0].attempt.payout_address if history else "")
    context = {
        "bounty": bounty,
        "target": target,
        "history": history,
        "allowance": allowed,
        "techniques": studio.TECHNIQUES,
        "payout_address": last_address,
        "preview": studio.assemble_preview(target.config, fills),
        "accepting": bounty.state is BountyState.LIVE and bounty.close_requested_at is None,
        **_editor_context(bounty.id, target, fills),
    }
    return _html(request, "studio/page.html", context)


@router.post("/studio/{bounty_id}/preview", response_class=HTMLResponse)
async def preview(
    request: Request,
    bounty_id: uuid.UUID,
    session: Transaction,
    account: SponsorDep,
    cipher: CipherDep,
) -> Response:
    del account
    _, target = await _target(session, cipher, bounty_id)
    try:
        fills = studio.read_fills(await _form(request), target.config, require_content=False)
        assembled = studio.assemble_preview(target.config, fills)
        return _html(request, "studio/_preview.html", {"preview": assembled})
    except (studio.StudioInputError, ValueError) as exc:
        return _html(request, "studio/_preview.html", {"preview": None, "error": str(exc)}, 422)


@router.post("/studio/{bounty_id}/techniques/{technique_key}", response_class=HTMLResponse)
async def technique(
    request: Request,
    bounty_id: uuid.UUID,
    technique_key: str,
    session: Transaction,
    account: SponsorDep,
    cipher: CipherDep,
) -> Response:
    del account
    _, target = await _target(session, cipher, bounty_id)
    try:
        data = await _form(request)
        fills = studio.read_fills(data, target.config, require_content=False)
        slot = EntryPoint(data.get("technique_slot", [""])[-1])
        fills = studio.insert_technique(target.config, fills, slot, technique_key)
        return _html(request, "studio/_editor.html", _editor_context(bounty_id, target, fills))
    except (studio.StudioInputError, ValueError) as exc:
        return _html(request, "studio/_error.html", {"error": str(exc)}, 422)


@router.post("/studio/{bounty_id}/attempts", response_class=HTMLResponse)
async def submit(
    request: Request,
    bounty_id: uuid.UUID,
    session: Transaction,
    account: SponsorDep,
    cipher: CipherDep,
    settings: SettingsDep,
) -> Response:
    _, target = await _target(session, cipher, bounty_id)
    try:
        data = await _form(request)
        fills = studio.read_fills(data, target.config)
        payout_address = data.get("payout_address", [""])[-1].strip()

        async def charge() -> int:
            return await credits.charge(session, account.id, bounty_id, fills, settings)

        attempt, _ = await attempts.submit_attempt(
            session,
            bounty_id,
            fills,
            payout_address,
            address_rule(settings),
            attempts.SubmissionLimits(
                per_address_per_hour=settings.attempts_per_address_per_hour,
                per_bounty_per_hour=settings.attempts_per_bounty_per_hour,
                bounty_daily_tokens=settings.bounty_daily_token_budget,
                platform_daily_tokens=settings.platform_daily_token_budget,
            ),
            account_id=account.id,
            charge=charge,
        )
        status = await attempts.own_attempt_status(session, cipher, attempt.id, account.id)
        return _html(
            request,
            "studio/_result.html",
            {"bounty_id": bounty_id, "attempt": attempt, "status": status, "why": ()},
            202,
        )
    except (studio.StudioInputError, ServiceError, ValueError) as exc:
        message = exc.message if isinstance(exc, ServiceError) else str(exc)
        code = exc.status if isinstance(exc, ServiceError) else 422
        return _html(request, "studio/_error.html", {"error": message}, code)


@router.get("/studio/{bounty_id}/attempts/{attempt_id}", response_class=HTMLResponse)
async def result(
    request: Request,
    bounty_id: uuid.UUID,
    attempt_id: uuid.UUID,
    session: Transaction,
    account: SponsorDep,
    cipher: CipherDep,
) -> Response:
    attempt = await session.scalar(
        select(Attempt).where(Attempt.id == attempt_id, Attempt.bounty_id == bounty_id)
    )
    if attempt is None or attempt.account_id != account.id:
        raise HTTPException(404, "attempt not found")
    status = await attempts.own_attempt_status(session, cipher, attempt.id, account.id)
    return _html(
        request,
        "studio/_result.html",
        {
            "bounty_id": bounty_id,
            "attempt": attempt,
            "status": status,
            "why": studio.why_not_verified(status),
        },
    )
