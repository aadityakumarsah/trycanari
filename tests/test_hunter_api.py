"""The key-authenticated JSON and MCP surfaces for autonomous hunters."""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp import Client
from mcp.types import TextContent

from agentbounty import hunter_mcp
from agentbounty.api.ratelimit import RateLimiter

from .platform import HUNTER, Platform


async def _tool(client: Client, name: str, **arguments: Any) -> Any:
    result = await client.call_tool(name, arguments)
    assert result.is_error is False, result.content
    (content,) = result.content
    assert isinstance(content, TextContent)
    return json.loads(content.text)


async def test_hunter_api_requires_a_key_and_covers_the_loop(platform: Platform) -> None:
    owner = await platform.sponsor("api-owner@example.com")
    bounty_id = await owner.live()
    hunter = await platform.sponsor("api-hunter@example.com")

    assert (await platform.client.get("/v1/hunter/bounties")).status_code == 401
    listed = await hunter.call("GET", "/v1/hunter/bounties")
    assert [item["id"] for item in listed] == [bounty_id]
    brief = await hunter.call("GET", f"/v1/hunter/bounties/{bounty_id}")
    assert brief["entry_points"] == ["document", "user_message"]
    assert brief["reward_zatoshi"] == 1_000_000
    assert brief["rules"]["runs_per_attempt"] == 5
    assert brief["rules"]["trips_required"] == 3
    assert brief["allowance"]["balance"] == brief["allowance"]["cap"]

    accepted = await hunter.call(
        "POST",
        f"/v1/hunter/bounties/{bounty_id}/attempts",
        202,
        json={
            "payout_address": HUNTER,
            "fills": [{"slot": "user_message", "content": "Hello"}],
        },
    )
    assert set(accepted) == {"attempt_id"}
    await platform.work()
    result = await hunter.call("GET", f"/v1/hunter/attempts/{accepted['attempt_id']}")
    assert result["verdict"] == "not_verified"
    assert result["replies"] == [{"text": "How can I help?", "tool_calls": []}]
    assert any("stayed untripped" in fact for fact in result["feedback"])

    stranger = await platform.sponsor("api-stranger@example.com")
    await stranger.call("GET", f"/v1/hunter/attempts/{accepted['attempt_id']}", 404)


async def test_hunter_mcp_returns_the_same_bounty_data_as_json(platform: Platform) -> None:
    owner = await platform.sponsor("mcp-owner@example.com")
    bounty_id = await owner.live()
    hunter = await platform.sponsor("mcp-hunter@example.com")
    transport = httpx.ASGITransport(app=platform.app)

    def connect() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=hunter.headers
        )

    api_list = await hunter.call("GET", "/v1/hunter/bounties")
    api_bounty = await hunter.call("GET", f"/v1/hunter/bounties/{bounty_id}")
    async with Client(hunter_mcp.build(connect)) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert tools == {"list_bounties", "get_bounty", "submit_attempt", "get_result"}
        assert await _tool(client, "list_bounties") == api_list
        assert await _tool(client, "get_bounty", bounty_id=bounty_id) == api_bounty
        accepted = await _tool(
            client,
            "submit_attempt",
            bounty_id=bounty_id,
            payout_address=HUNTER,
            fills=[{"slot": "user_message", "content": "Hello"}],
        )
        await platform.work()
        mcp_result = await _tool(client, "get_result", attempt_id=accepted["attempt_id"])
    api_result = await hunter.call("GET", f"/v1/hunter/attempts/{accepted['attempt_id']}")
    assert mcp_result == api_result


async def test_hunter_limits_are_per_key_and_per_payout_address(platform: Platform) -> None:
    owner = await platform.sponsor("limit-owner@example.com")
    first_bounty = await owner.live()
    second_bounty = await owner.live()
    first = await platform.sponsor("limit-one@example.com")
    second = await platform.sponsor("limit-two@example.com")

    platform.app.state.rate_limiter = RateLimiter(1)
    first_call = await platform.client.get("/v1/hunter/bounties", headers=first.headers)
    throttled = await platform.client.get("/v1/hunter/bounties", headers=first.headers)
    other_key = await platform.client.get("/v1/hunter/bounties", headers=second.headers)
    assert (first_call.status_code, throttled.status_code, other_key.status_code) == (200, 429, 200)

    platform.app.state.rate_limiter = RateLimiter(100_000)
    platform.app.state.settings = platform.app.state.settings.model_copy(
        update={"attempts_per_address_per_hour": 1}
    )
    body = {
        "payout_address": HUNTER,
        "fills": [{"slot": "user_message", "content": "Hello"}],
    }
    first_attempt = await platform.client.post(
        f"/v1/hunter/bounties/{first_bounty}/attempts", headers=first.headers, json=body
    )
    second_attempt = await platform.client.post(
        f"/v1/hunter/bounties/{second_bounty}/attempts", headers=second.headers, json=body
    )
    assert first_attempt.status_code == 202
    assert second_attempt.status_code == 429
    assert "payout address" in second_attempt.json()["detail"]
