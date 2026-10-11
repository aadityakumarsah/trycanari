from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from agentbounty.app import create_app
from agentbounty.settings import Settings, load_settings

ROOT = Path(__file__).resolve().parent.parent


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
