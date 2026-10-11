"""FastAPI application factory.

Run with:  uvicorn agentbounty.app:create_app --factory
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agentbounty.api import health, public, sponsor
from agentbounty.db import create_engine, create_session_factory
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError
from agentbounty.services.wallet import escrow_address
from agentbounty.settings import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = create_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="AgentBounty", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.cipher = Cipher(settings.encryption_key.get_secret_value())
    # Known from configuration, so this process can tell a sponsor where to deposit
    # without holding a wallet. A missing or malformed address stops the app starting.
    app.state.escrow_address = escrow_address(settings)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    app.include_router(health.router)
    app.include_router(sponsor.router)
    app.include_router(public.router)
    return app
