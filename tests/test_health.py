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


async def test_the_pool_is_warmed_at_start_and_a_dead_database_does_not_stop_it(
    settings: Settings,
) -> None:
    from sqlalchemy import make_url

    from agentbounty.app import warm_pool
    from agentbounty.db import create_engine

    engine = create_engine(settings.database_url)
    await warm_pool(engine, connections=2)
    assert engine.pool.checkedin() == 2  # type: ignore[attr-defined]
    await engine.dispose()

    nowhere = make_url(settings.database_url).set(host="127.0.0.1", port=1)
    dead = create_engine(nowhere.render_as_string(hide_password=False))
    await warm_pool(dead)
    await dead.dispose()
