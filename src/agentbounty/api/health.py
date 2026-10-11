"""Liveness and database readiness."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.db import get_session

logger = logging.getLogger(__name__)

DATABASE_TIMEOUT_SECONDS = 3.0

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


@router.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
async def health(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthResponse | JSONResponse:
    """Report ok only if the database answers a query in time."""
    try:
        async with asyncio.timeout(DATABASE_TIMEOUT_SECONDS):
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        # The detail goes to the log, not the response: it can name hosts and users.
        logger.warning("health check failed: %s: %s", type(exc).__name__, exc)
        body = HealthResponse(status="unavailable")
        return JSONResponse(status_code=503, content=body.model_dump())
    return HealthResponse(status="ok")
