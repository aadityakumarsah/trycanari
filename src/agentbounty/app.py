"""FastAPI application factory.

Run with:  uvicorn agentbounty.app:create_app --factory
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from agentbounty.api import (
    admin,
    agent_guide,
    assurance,
    chain,
    control,
    evidence,
    google,
    health,
    hunter,
    pilot,
    public,
    session,
    spa,
    sponsor,
    studio,
    webhooks,
)
from agentbounty.api.ratelimit import RateLimiter
from agentbounty.db import create_engine, create_session_factory
from agentbounty.logs import configure_logging
from agentbounty.services import billing, endpoint, mail, notify
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


async def mail_loop(app: FastAPI, sender: mail.EmailSender) -> None:
    """Send the emails notifications ask for, for as long as the app runs.

    It lives in the API process because it needs no secret the API lacks and no
    process of its own. Several API processes may run it at once: each email is
    claimed under a row lock before it is sent.
    """
    settings: Settings = app.state.settings
    while True:
        try:
            await notify.send_pending(app.state.session_factory, sender, settings)
        except Exception:
            logger.exception("sending email failed")
        await asyncio.sleep(settings.email_poll_seconds)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    if settings.app_env != "test":
        configure_logging()
    engine = create_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # In the background: the app must not wait on the database to start answering.
        warming = asyncio.create_task(warm_pool(engine))
        # Tests drain the outbox themselves, with a sender of their own.
        sender = mail.make_sender(settings) if settings.app_env != "test" else None
        mailing = asyncio.create_task(mail_loop(app, sender)) if sender else None
        yield
        warming.cancel()
        if mailing is not None and sender is not None:
            mailing.cancel()
            await sender.aclose()
        biller: billing.BillingBackend | None = app.state.biller
        if biller is not None:
            await biller.aclose()
        await engine.dispose()

    app = FastAPI(
        title="trycanari API",
        description=(
            "Managed assurance, continuous regression and bug bounties for AI agents. "
            "The guide is at /docs; this is the reference."
        ),
        lifespan=lifespan,
        # /docs belongs to the web app's documentation; the interactive reference lives here.
        docs_url="/api-docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.cipher = Cipher(settings.secret_encryption_key.get_secret_value())
    # Invoices for a managed assessment. None when this deployment bills nobody; a
    # test puts its own backend here.
    app.state.biller = billing.make_biller(settings) if settings.app_env != "test" else None
    # Known from configuration, so this process can tell a sponsor where to deposit
    # without holding a wallet. A missing or malformed address stops the app starting.
    app.state.escrow_address = escrow_address(settings)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    # Tests put a transport here to stand in for Google. None means the real network.
    app.state.google_transport = None
    app.state.cloudinary_transport = None
    app.state.solana_transport = None
    app.state.model_transport = None
    # How a sponsor's own model endpoint is opened: never at an internal address.
    app.state.model_connector = endpoint.connector(settings)
    app.state.models_listed = None
    # Public answers kept for a few seconds (see api/public.py).
    app.state.public_cache = {}
    app.state.public_cache_locks = {}

    # Pages, scripts and API answers go out compressed: the app's script is a third the size.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Headers every answer carries, whatever route made it."""
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/v1/"):
            # Answers about an account must never be kept by a browser or a proxy and
            # shown to the next person, and nothing here belongs in a search engine.
            headers.setdefault("Cache-Control", "no-store")
            headers.setdefault("X-Robots-Tag", "noindex")
        if settings.app_env == "production":
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400:
            # A successful state change is visible on public pages immediately in this
            # process; edge caches retain it for at most five seconds.
            app.state.public_cache.clear()
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        incident = uuid.uuid4().hex[:12]
        logger.exception("database request failed", extra={"incident": incident})
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The data service is reconnecting. This page will retry automatically.",
                "incident": incident,
            },
            headers={"Retry-After": "2"},
        )

    app.include_router(health.router)
    app.include_router(sponsor.router)
    app.include_router(public.router)
    app.include_router(hunter.router)
    app.include_router(session.router)
    app.include_router(google.router)
    app.include_router(pilot.public)
    app.include_router(assurance.sponsor)
    app.include_router(admin.router)
    app.include_router(pilot.admin)
    app.include_router(assurance.admin)
    app.include_router(chain.router)
    app.include_router(webhooks.router)
    app.include_router(control.router)
    app.include_router(agent_guide.router)
    app.include_router(evidence.router)
    app.include_router(studio.router)
    # Last: it answers every path the routes above did not claim.
    spa.install(app)
    return app
