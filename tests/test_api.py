"""Authentication, ownership, validation and the listing check."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agentbounty.app import create_app
from agentbounty.settings import Settings

from .platform import (
    DEMO_CONFIG,
    HUNTER,
    SECRET,
    SECRET_CANARY,
    STRONG,
    TOOL_CANARY,
    WEAK,
    Platform,
)


async def test_sponsor_endpoints_need_the_owners_key(platform: Platform) -> None:
    owner = await platform.sponsor()
    other = await platform.sponsor("other@example.com")
    bounty_id = await owner.draft()
    client = platform.client

    paths = [
        ("PUT", f"/v1/bounties/{bounty_id}/config", DEMO_CONFIG),
        ("POST", f"/v1/bounties/{bounty_id}/canaries", TOOL_CANARY),
        ("POST", f"/v1/bounties/{bounty_id}/activate", None),
    ]
    for method, path, body in paths:
        assert (await client.request(method, path, json=body)).status_code == 401
        wrong = {"Authorization": "Bearer sk_not-a-real-key"}
        assert (await client.request(method, path, json=body, headers=wrong)).status_code == 401
        # Another sponsor cannot tell the bounty exists.
        await other.call(method, path, 404, json=body)
    assert (await client.post("/v1/bounties", json={"reward_zatoshi": 5})).status_code == 401
    basic = {"Authorization": "Basic abc"}
    assert (
        await client.post("/v1/bounties", json={"reward_zatoshi": 5}, headers=basic)
    ).status_code == 401

    duplicate = await client.post("/v1/sponsors", json={"email": "team@example.com"})
    assert duplicate.status_code == 409
    assert (await client.post("/v1/sponsors", json={"email": "not-an-email"})).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"reward_zatoshi": 0},
        {"reward_zatoshi": -5},
        {"reward_zatoshi": 1.5},
        {"reward_zatoshi": "100"},
        {"reward_zatoshi": 21_000_000 * 100_000_000 + 1},
        {"reward_zatoshi": 100, "fee_rate": 0},
    ],
    ids=["zero", "negative", "float", "string", "more than all ZEC", "sponsor-chosen fee"],
)
async def test_rewards_are_positive_integer_zatoshi(
    platform: Platform, body: dict[str, Any]
) -> None:
    await (await platform.sponsor()).call("POST", "/v1/bounties", 422, json=body)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"params": {"stream": True}}, "unsupported model params"),
        ({"params": {"max_tokens": 0}}, "positive integer"),
        ({"entry_points": []}, "entry point"),
        ({"tools": [DEMO_CONFIG["tools"][0], DEMO_CONFIG["tools"][0]]}, "unique"),
    ],
)
async def test_malformed_configs_are_refused(
    platform: Platform, change: dict[str, Any], reason: str
) -> None:
    sponsor = await platform.sponsor()
    bounty_id = (await sponsor.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": 5}))["id"]
    path = f"/v1/bounties/{bounty_id}/config"
    refused = await sponsor.call("PUT", path, 422, json={**DEMO_CONFIG, **change})
    assert reason in refused["detail"]
    for invalid in ({**DEMO_CONFIG, "system_prompt": ""}, {**DEMO_CONFIG, "unknown": 1}):
        await sponsor.call("PUT", path, 422, json=invalid)


async def test_malformed_canaries_are_refused(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = (await sponsor.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": 5}))["id"]
    path = f"/v1/bounties/{bounty_id}/canaries"
    early = await sponsor.call("POST", path, 409, json=TOOL_CANARY)
    assert "upload a config" in early["detail"]
    await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", json=DEMO_CONFIG)

    short = await sponsor.call("POST", path, 422, json={**SECRET_CANARY, "secret": "hunter2"})
    assert "at least 12 characters" in short["detail"]
    not_a_list = {
        **TOOL_CANARY,
        "predicate": {"argument": "recipient", "operator": "in", "value": "x"},
    }
    assert "needs a list" in (await sponsor.call("POST", path, 422, json=not_a_list))["detail"]
    for invalid in (
        {"type": "tripwire", "public_label": "x"},
        {**SECRET_CANARY, "tool": "transfer_funds"},
        {**TOOL_CANARY, "predicate": {"argument": "a", "operator": "matches", "value": 1}},
    ):
        await sponsor.call("POST", path, 422, json=invalid)


async def test_the_listing_check_refuses_unwinnable_bounties(platform: Platform) -> None:
    sponsor = await platform.sponsor()

    async def new_bounty(config: dict[str, Any]) -> str:
        created = await sponsor.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": 5})
        bounty_id: str = created["id"]
        await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", json=config)
        return bounty_id

    async def activate(bounty_id: str) -> str:
        refused = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/activate", 409)
        detail: str = refused["detail"]
        return detail

    empty = (await sponsor.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": 5}))["id"]
    assert "upload a config" in await activate(empty)
    assert "no canaries" in await activate(await new_bounty(DEMO_CONFIG))

    no_secret = await new_bounty({**DEMO_CONFIG, "system_prompt": "You are a treasury assistant."})
    await sponsor.call("POST", f"/v1/bounties/{no_secret}/canaries", 201, json=SECRET_CANARY)
    detail = await activate(no_secret)
    assert "signing_reference" in detail
    assert "could never leak it" in detail
    assert SECRET not in detail

    no_tool = await new_bounty({**DEMO_CONFIG, "tools": []})
    await sponsor.call("POST", f"/v1/bounties/{no_tool}/canaries", 201, json=TOOL_CANARY)
    assert "no tool named 'transfer_funds'" in await activate(no_tool)

    # A secret that only a tool returns can still be leaked, so that listing is fine.
    tool = {**DEMO_CONFIG["tools"][0], "result": f"Reference: {SECRET}"}
    via_tool = await new_bounty(
        {**DEMO_CONFIG, "system_prompt": "You look things up.", "tools": [tool]}
    )
    await sponsor.call("POST", f"/v1/bounties/{via_tool}/canaries", 201, json=SECRET_CANARY)
    await sponsor.call("POST", f"/v1/bounties/{via_tool}/activate")


async def test_a_live_config_cannot_be_edited(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.live()
    for method, path, body in (
        ("PUT", "config", DEMO_CONFIG),
        ("POST", "canaries", TOOL_CANARY),
        ("POST", "activate", None),
    ):
        refused = await sponsor.call(method, f"/v1/bounties/{bounty_id}/{path}", 409, json=body)
        assert "live" in refused["detail"]


async def test_editing_a_draft_changes_the_hash_and_keeps_the_version(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    path = f"/v1/bounties/{bounty_id}/config"
    same = await sponsor.call("PUT", path, json=DEMO_CONFIG)
    again = await sponsor.call("PUT", path, json=DEMO_CONFIG)
    changed = await sponsor.call("PUT", path, json={**DEMO_CONFIG, "model": "other-model"})
    assert same == again
    assert (changed["version"], changed["config_hash"] != same["config_hash"]) == (1, True)
    # Key order in the upload does not matter: the hash is over canonical JSON.
    reordered = dict(reversed(list(DEMO_CONFIG.items())))
    assert await sponsor.call("PUT", path, json=reordered) == same


async def test_attempts_must_fit_the_declared_entry_points(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    message_only = await sponsor.live({**DEMO_CONFIG, "entry_points": ["user_message"]})
    undeclared = await platform.submit(message_only, "x", slot="document", expect=422)
    assert "not an entry point" in str(undeclared)
    await platform.submit(message_only, "x", slot="tool_result", expect=422)
    await platform.submit(message_only, "x" * 20_001, expect=422)
    await platform.submit(message_only, "", expect=422)

    client = platform.client
    path = f"/v1/bounties/{message_only}/attempts"
    twice = [{"slot": "user_message", "content": "a"}, {"slot": "user_message", "content": "b"}]
    for body in (
        {"payout_address": "utest1x", "fills": twice},
        {"payout_address": "utest1x", "fills": []},
        {"payout_address": "", "fills": [twice[0]]},
        {"fills": [twice[0]]},
    ):
        assert (await client.post(path, json=body)).status_code == 422

    draft = await sponsor.draft()
    await platform.submit(draft, WEAK, expect=404)
    await platform.submit("00000000-0000-0000-0000-000000000000", WEAK, expect=404)
    await platform.submit("not-a-uuid", WEAK, expect=422)


async def test_an_attempt_can_be_read_only_with_its_status_token(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).live()
    mine = await platform.submit(bounty_id, STRONG)
    theirs = await platform.submit(bounty_id, WEAK)
    client = platform.client
    path = f"/v1/attempts/{mine['attempt_id']}"

    assert (await client.get(path)).status_code == 401
    for token in ("wrong", theirs["status_token"]):
        response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert (response.status_code, response.json()) == (404, {"detail": "attempt not found"})
    missing = "/v1/attempts/00000000-0000-0000-0000-000000000000"
    headers = {"Authorization": f"Bearer {mine['status_token']}"}
    assert (await client.get(missing, headers=headers)).status_code == 404
    assert (await platform.status(mine))["state"] == "queued"


async def test_in_production_a_bounty_cannot_go_live_unfunded(service_settings: Settings) -> None:
    production = service_settings.model_copy(
        update={
            "app_env": "production",
            "model_backend": "real",
            "model_api_base": "https://model.test/v1",
            "model_api_key": SecretStr("key"),
            "wallet_backend": "zcash",
            "zipher_cli_path": "/usr/local/bin/zipher-cli",
            "zipher_data_dir": "/wallets/escrow",
            "escrow_address": HUNTER,
            "payout_max_zatoshi": 10**9,
            "payout_daily_max_zatoshi": 10**10,
        }
    )
    app = create_app(production)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        sponsor = await Platform(app, client).sponsor()
        bounty_id = await sponsor.draft()
        refused = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/activate", 409)
        assert "must be funded" in refused["detail"]
        assert (await client.get("/v1/bounties")).json() == []
    await app.state.engine.dispose()
