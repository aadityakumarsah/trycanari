"""FastAPI application factory.

Run with:  uvicorn agentbounty.app:create_app --factory
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from agentbounty.api import admin, agent_guide, google, health, public, session, spa, sponsor
from agentbounty.api.ratelimit import RateLimiter
from agentbounty.db import create_engine, create_session_factory
from agentbounty.logs import configure_logging
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError
from agentbounty.services.wallet import escrow_address
from agentbounty.settings import Settings, load_settings

logger = logging.getLogger(__name__)


async def warm_pool(engine: AsyncEngine, connections: int = 3) -> None:
    """Open a few database connections before the first visitor arrives.

    Connecting to a hosted database takes a second or two, and a page asks for
    several things at once, so without this the first page after a start waits
    on several connections being made.
    """

    async def connect() -> None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    try:
        async with asyncio.timeout(20):
            await asyncio.gather(*(connect() for _ in range(connections)))
    except Exception:
        # The database being down must not stop the app starting: /health reports it.
        logger.warning("could not warm the database pool", exc_info=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    if settings.app_env != "test":
        configure_logging()
    engine = create_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # In the background: the app must not wait on the database to start answering.
        warming = asyncio.create_task(warm_pool(engine))
        yield
        warming.cancel()
        await engine.dispose()

    app = FastAPI(
        title="trycanari API",
        description="Bug bounties for AI agents. The guide is at /docs; this is the reference.",
        lifespan=lifespan,
        # /docs belongs to the web app's documentation; the interactive reference lives here.
        docs_url="/api-docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.cipher = Cipher(settings.secret_encryption_key.get_secret_value())
    # Known from configuration, so this process can tell a sponsor where to deposit
    # without holding a wallet. A missing or malformed address stops the app starting.
    app.state.escrow_address = escrow_address(settings)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    # Tests put a transport here to stand in for Google. None means the real network.
    app.state.google_transport = None
    # Public answers kept for a few seconds (see api/public.py).
    app.state.public_cache = {}

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    app.include_router(health.router)
    app.include_router(sponsor.router)
    app.include_router(public.router)
    app.include_router(session.router)
    app.include_router(google.router)
    app.include_router(admin.router)
    app.include_router(agent_guide.router)
    # Last: it answers every path the routes above did not claim.
    spa.install(app)
    return app
