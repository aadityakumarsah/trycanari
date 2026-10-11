from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from agentbounty.app import create_app
from agentbounty.settings import Settings, load_settings


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
