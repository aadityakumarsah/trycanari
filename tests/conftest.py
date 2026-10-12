from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from agentbounty.app import create_app
from agentbounty.services.crypto import generate_key
from agentbounty.settings import Settings, load_settings

from .platform import Platform

ROOT = Path(__file__).resolve().parent.parent

# The tests create and drop whole databases, so they only ever run against a local
# server. TEST_DATABASE_URL names it when DATABASE_URL points somewhere that matters.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres", None}
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.pop("DIRECT_URL", None)
_host = make_url(os.environ.get("DATABASE_URL", "postgresql+asyncpg://localhost/x")).host
if _host not in LOCAL_HOSTS:
    pytest.exit(
        f"Refusing to run the tests against {_host}: they create and drop databases."
        " Set TEST_DATABASE_URL to a local PostgreSQL server.",
        returncode=2,
    )


@pytest.fixture(autouse=True)
def _no_sign_in_provider_from_the_developers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a developer has switched on for themselves must not be on inside a test."""
    for name in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "PUBLIC_URL",
        "PROXY_SECRET",
        "INVITE_ONLY",
        "ADMIN_EMAILS",
        "CLOUDINARY_URL",
        "STARTER_CREDITS",
        "PROVEN_CREDITS",
        "MODEL_API_BASE",
        "MODEL_API_KEY",
        "WALLET_BACKEND",
        "SOLANA_RPC_URL",
        "SOLANA_SECRET_KEY",
        "SOLANA_CLUSTER",
        "ESCROW_ADDRESS",
        "EMAIL_BACKEND",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_EMAIL_TOKEN",
        "EMAIL_FROM",
    ):
        monkeypatch.delenv(name, raising=False)
    # Tests never call a real model, whatever the developer's own environment uses.
    monkeypatch.setenv("MODEL_BACKEND", "fake")


@pytest.fixture
def settings() -> Settings:
    """Settings from the environment; `make test` loads them from .env."""
    return load_settings()


async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await app.state.engine.dispose()


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for client in client_for(settings):
        yield client


def alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["configure_logging"] = False
    return config


async def _admin(database_url: str, statement: str) -> None:
    engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.execute(text(statement))
    await engine.dispose()


@contextmanager
def scratch_database() -> Iterator[str]:
    """Create an empty database on the configured server; drop it afterwards.

    Synchronous on purpose: Alembic's environment starts its own event loop,
    so migrations cannot be run from inside a running one.
    """
    server_url = load_settings().database_url
    name = f"agentbounty_test_{secrets.token_hex(6)}"
    asyncio.run(_admin(server_url, f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(server_url).set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(_admin(server_url, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    """A scratch database with every migration applied, shared by the test session."""
    from alembic import command

    with scratch_database() as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture
async def session(migrated_database_url: str) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back when the test ends."""
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            yield session
        await transaction.rollback()
    await engine.dispose()


# --- the whole service, on a database of its own ----------------------------------------


@pytest.fixture
async def database_url(migrated_database_url: str) -> AsyncIterator[str]:
    """A private copy of the migrated database, for tests that commit."""
    server_url = load_settings().database_url
    template = make_url(migrated_database_url).database
    name = f"agentbounty_test_{secrets.token_hex(6)}"
    await _admin(server_url, f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    yield make_url(server_url).set(database=name).render_as_string(hide_password=False)
    await _admin(server_url, f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def service_settings(database_url: str) -> Settings:
    return Settings(
        app_env="test",
        database_url=database_url,
        secret_encryption_key=SecretStr(generate_key()),
        model_backend="fake",
        # High enough that only the tests about the limit ever reach it.
        rate_limit_per_minute=100_000,
        # Tests change something and look at once.
        public_cache_seconds=0,
    )


@pytest.fixture
async def platform(service_settings: Settings) -> AsyncIterator[Platform]:
    app = create_app(service_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Platform(app, client)
    await app.state.engine.dispose()
