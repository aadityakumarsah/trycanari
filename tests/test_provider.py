"""The platform's model provider, which the owner can change while the service runs."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select, text

from agentbounty.models import AdminAction
from agentbounty.services import endpoint, provider
from agentbounty.services.model import ChatMessage, FakeModel, ModelParams, RealModel
from agentbounty.workers.replay import PlatformModel

from .platform import Platform
from .test_admin import account, club, owner, promote

__all__ = ["club"]

BASE = "https://models.example/v1"
OTHER = "https://other.example/v1"
KEY = "sk-provider-key-abcd1234"
ASK = [ChatMessage("user", "hello")]
PARAMS = ModelParams("big-model", 16)


@pytest.fixture(autouse=True)
def names_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    known = {
        "models.example": ["93.184.216.34"],
        "other.example": ["93.184.216.35"],
        "inside.example": ["10.0.0.7"],
    }

    async def resolve(host: str, port: int) -> list[str]:
        return known.get(host, [])

    monkeypatch.setattr(endpoint, "_resolve", resolve)


class Provider:
    """Stands in for a model provider, and records what reached it."""

    def __init__(self, *, models: tuple[str, ...] = ("big-model", "small-model")) -> None:
        self.requests: list[httpx.Request] = []
        self.models = models
        self.accepts = KEY

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {self.accepts}":
            return httpx.Response(401, text="bad key")
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": name} for name in self.models]})
        said = f"from {request.url.host}"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": said}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    def connect(self, base: str, key: str) -> RealModel:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return RealModel(base, key, client=client)


async def test_the_provider_is_the_owners_alone(club: Platform) -> None:
    boss = await owner(club)
    helper = await account(club, "admin@example.com")
    await promote(club, "admin@example.com")
    someone = await account(club, "someone@example.com")
    body = {"api_base": BASE, "api_key": KEY}
    for method, path in (
        ("GET", "/v1/admin/model"),
        ("PUT", "/v1/admin/model"),
        ("DELETE", "/v1/admin/model"),
        ("POST", "/v1/admin/model/test"),
    ):
        # An admin who is not the owner is told no; to anyone else it does not exist.
        as_admin = await club.client.request(method, path, headers=helper, json=body)
        assert as_admin.status_code == 403, path
        assert "only the owner" in as_admin.text
        stranger = await club.client.request(method, path, headers=someone, json=body)
        assert stranger.status_code == 404, path
        assert (await club.client.request(method, path, json=body)).status_code == 401, path
    assert (await club.client.get("/v1/admin/model", headers=boss)).status_code == 200


async def test_the_owner_tests_saves_and_resets_a_provider(club: Platform) -> None:
    stand_in = Provider()
    club.app.state.model_connector = stand_in.connect
    boss = await owner(club)
    client = club.client

    # Out of the box: the built-in test model, and nothing saved.
    start = (await client.get("/v1/admin/model", headers=boss)).json()
    assert (start["source"], start["api_base"], start["key_hint"]) == ("none", None, None)

    # Trying a provider changes nothing.
    tried = await client.post(
        "/v1/admin/model/test",
        headers=boss,
        json={"api_base": BASE + "/", "api_key": KEY, "model": "big-model"},
    )
    assert tried.status_code == 200, tried.text
    assert tried.json() == {
        "ok": True,
        "detail": "The model answered: from models.example",
        "milliseconds": tried.json()["milliseconds"],
        "models": ["big-model", "small-model"],
        "missing_models": [],
    }
    listed = await client.post(
        "/v1/admin/model/test", headers=boss, json={"api_base": BASE, "api_key": KEY}
    )
    assert listed.json()["detail"] == "The provider answered and lists 2 models."
    wrong = await client.post(
        "/v1/admin/model/test", headers=boss, json={"api_base": BASE, "api_key": "sk-wrong-key"}
    )
    assert (wrong.json()["ok"], wrong.json()["models"]) == (False, [])
    assert (await client.get("/v1/admin/model", headers=boss)).json()["source"] == "none"

    saved = await client.put(
        "/v1/admin/model", headers=boss, json={"api_base": BASE, "api_key": KEY}
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["source"], saved.json()["api_base"]) == ("admin", BASE)
    assert saved.json()["key_hint"] == "…1234"
    assert KEY not in saved.text
    # Stored as ciphertext, and written to the log without the key.
    async with club.sessions() as session:
        stored = (
            await session.execute(text("SELECT value_enc FROM platform_settings"))
        ).scalar_one()
        logged = (await session.scalars(select(AdminAction))).all()
    assert KEY.encode() not in bytes(stored)
    assert BASE.encode() not in bytes(stored)
    assert [(entry.action, entry.detail) for entry in logged] == [
        ("model_provider=set", "models.example")
    ]

    # The key is kept for its own address, and asked for again for any other.
    again = await client.put("/v1/admin/model", headers=boss, json={"api_base": BASE})
    assert again.status_code == 200
    moved = await client.put("/v1/admin/model", headers=boss, json={"api_base": OTHER})
    assert moved.status_code == 422
    assert "enter the API key" in moved.text

    # Sponsors now pick from the new provider's models, and test against it.
    sponsor = await club.sponsor("sponsor@example.com")
    offered = await sponsor.call("GET", "/v1/models")
    assert offered == {"provider": "models.example", "models": ["big-model", "small-model"]}
    bounty = await sponsor.draft()
    checked = await sponsor.call(
        "POST", f"/v1/bounties/{bounty}/model-test", json={"model": "big-model"}
    )
    assert checked["detail"] == "The model answered: from models.example"

    reset = await client.delete("/v1/admin/model", headers=boss)
    assert reset.json()["source"] == "none"
    assert await sponsor.call("GET", "/v1/models") == {"provider": None, "models": []}
    async with club.sessions() as session:
        actions = [entry.action for entry in await session.scalars(select(AdminAction))]
    assert sorted(actions) == ["model_provider=reset", "model_provider=set", "model_provider=set"]


@pytest.mark.parametrize(
    ("address", "why"),
    [
        ("https://inside.example/v1", "public internet"),
        ("http://models.example/v1", "https://"),
        ("https://nowhere.example/v1", "could not be found"),
        ("https://user:pw@models.example/v1", "key field"),
    ],
)
async def test_a_provider_address_that_may_not_be_used_is_refused(
    club: Platform, address: str, why: str
) -> None:
    boss = await owner(club)
    body = {"api_base": address, "api_key": KEY}
    for method, path in (("PUT", "/v1/admin/model"), ("POST", "/v1/admin/model/test")):
        refused = await club.client.request(method, path, headers=boss, json=body)
        assert refused.status_code == 422, path
        assert why in refused.text
    assert (await club.client.get("/v1/admin/model", headers=boss)).json()["source"] == "none"


async def test_a_new_provider_that_lacks_a_model_in_use_is_pointed_out(club: Platform) -> None:
    stand_in = Provider(models=("small-model",))
    club.app.state.model_connector = stand_in.connect
    boss = await owner(club)
    sponsor = await club.sponsor("sponsor@example.com")
    await sponsor.live()  # runs on "demo-model"

    shown = (await club.client.get("/v1/admin/model", headers=boss)).json()
    assert shown["models_in_use"] == ["demo-model"]
    tried = await club.client.post(
        "/v1/admin/model/test", headers=boss, json={"api_base": BASE, "api_key": KEY}
    )
    assert tried.json()["missing_models"] == ["demo-model"]


async def test_the_worker_follows_the_owners_choice_without_a_restart(club: Platform) -> None:
    first, second = Provider(), Provider()
    opened: list[str] = []

    def connect(base: str, key: str) -> RealModel:
        opened.append(base)
        return (first if base == BASE else second).connect(base, key)

    model = PlatformModel(FakeModel())

    async def refresh() -> bool:
        return await model.refresh(club.sessions, club.cipher, connect)

    # Nothing set: the model the worker started on.
    assert await refresh() is False
    assert (await model.complete(ASK, [], PARAMS)).text == "How can I help?"

    await owner(club)
    async with club.sessions.begin() as session:
        me = (await session.execute(text("SELECT id FROM sponsors"))).scalar_one()
        await provider.save(session, club.cipher, BASE, KEY, me)
    assert await refresh() is True
    assert (await model.complete(ASK, [], PARAMS)).text == "from models.example"
    # Unchanged: nothing is reopened.
    assert await refresh() is False
    assert opened == [BASE]

    async with club.sessions.begin() as session:
        await provider.save(session, club.cipher, OTHER, KEY, me)
    assert await refresh() is True
    assert (await model.complete(ASK, [], PARAMS)).text == "from other.example"
    sent = json.loads(second.requests[-1].content)
    assert (sent["model"], second.requests[-1].headers["authorization"]) == (
        "big-model",
        f"Bearer {KEY}",
    )

    async with club.sessions.begin() as session:
        await provider.clear(session)
    assert await refresh() is True
    assert (await model.complete(ASK, [], PARAMS)).text == "How can I help?"
    await model.aclose()
