from __future__ import annotations

import httpx

from agentbounty.settings import Settings

from .conftest import client_for


async def test_health_is_ok_when_the_database_answers(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_is_503_when_the_database_is_unreachable(settings: Settings) -> None:
    # Port 1 on loopback refuses connections.
    unreachable = settings.model_copy(
        update={"database_url": "postgresql+asyncpg://nobody@127.0.0.1:1/none"}
    )
    async for client in client_for(unreachable):
        response = await client.get("/health")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
