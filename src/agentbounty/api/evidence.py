"""Printable sponsor-owned evidence pages for verified findings."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.templating import Jinja2Templates

from agentbounty.api.deps import CipherDep, OwnBounty, Transaction
from agentbounty.models import Attempt, Sponsor
from agentbounty.services import reports

PACKAGE = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=PACKAGE / "templates")
CSS = PACKAGE / "static" / "evidence.css"
JS = PACKAGE / "static" / "evidence.js"

router = APIRouter(tags=["evidence"], include_in_schema=False)

HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self';"
    " img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


@router.get("/evidence-assets/evidence.css")
async def stylesheet() -> FileResponse:
    return FileResponse(
        CSS, media_type="text/css", headers={"Cache-Control": "public, max-age=86400"}
    )


@router.get("/evidence-assets/evidence.js")
async def script() -> FileResponse:
    return FileResponse(
        JS,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/evidence/bounties/{bounty_id}/findings/{finding_id}", response_class=HTMLResponse)
async def finding_page(
    request: Request,
    finding_id: uuid.UUID,
    session: Transaction,
    bounty: OwnBounty,
    cipher: CipherDep,
) -> Response:
    found = next(
        (
            item
            for item in await reports.findings_report(session, cipher, bounty)
            if item.finding.id == finding_id
        ),
        None,
    )
    if found is None:
        raise HTTPException(404, "finding not found")
    attempt = await session.get_one(Attempt, found.finding.attempt_id)
    hunter = await session.get(Sponsor, attempt.account_id) if attempt.account_id else None
    runs: list[dict[str, Any]] = []
    for run in found.runs:
        by_message: dict[int, list[Any]] = {}
        for item in run.evidence:
            by_message.setdefault(item.message_index, []).append(item)
        runs.append(
            {
                "run_index": run.run_index,
                "messages": [
                    {"message": message, "evidence": by_message.get(index, [])}
                    for index, message in enumerate(run.transcript)
                ],
                "tripped": bool(run.evidence),
            }
        )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="evidence/finding.html",
        context={
            "bounty": bounty,
            "report": found,
            "hunter": hunter,
            "runs": runs,
        },
        headers=HEADERS,
    )
