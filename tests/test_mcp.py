"""The MCP server: a coding agent scans the agent it is working on, from the editor."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import httpx
from mcp import Client
from mcp.types import TextContent

from agentbounty import mcp_server
from agentbounty.services.model import FakeModel
from agentbounty.workers import replay

from .platform import DEMO_CONFIG, PATCHED_CONFIG, SECRET, SECRET_CANARY, TOOL_CANARY, Platform
from .platform import Sponsor as Account
from .test_scan import Obedient

TEAMS = itertools.count()
CANARIES = [SECRET_CANARY, TOOL_CANARY]


class Editor:
    """A coding agent's end of the server: it calls a tool and reads what comes back."""

    def __init__(self, client: Client) -> None:
        self.client = client

    async def call(self, tool: str, error: bool = False, **arguments: Any) -> str:
        result = await self.client.call_tool(tool, arguments)
        assert result.is_error is error, result.content
        (said,) = result.content
        assert isinstance(said, TextContent)
        # A failure is said as the protocol says it, with the tool's name in front.
        return said.text.removeprefix(f"Error executing tool {tool}: ")


def server_for(platform: Platform, account: Account, model: Any = None) -> Any:
    """The server as one sponsor's editor runs it, with the worker doing its part while
    a scan is waited for."""

    def connect() -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=platform.app)
        return httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=account.headers
        )

    async def work() -> None:
        await replay.work_once(platform.sessions, platform.cipher, model, max_tries=3)

    return mcp_server.build(connect, wait=work if model is not None else None)


async def team(platform: Platform) -> Account:
    return await platform.sponsor(f"editor-{next(TEAMS)}@example.com")


async def test_the_server_offers_the_tools_of_a_scan_and_none_that_move_money(
    platform: Platform,
) -> None:
    async with Client(server_for(platform, await team(platform))) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert sorted(tools) == [
        "add_canary",
        "create_draft",
        "get_agent",
        "get_scan",
        "list_bounties",
        "scan_agent",
    ]
    assert all(tool.description for tool in tools.values())
    assert tools["scan_agent"].input_schema["required"] == ["bounty_id"]
    for reads in ("list_bounties", "get_agent", "get_scan"):
        annotations = tools[reads].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True


async def test_an_agent_makes_a_draft_scans_it_patches_it_and_scans_again(
    platform: Platform,
) -> None:
    account = await team(platform)
    async with Client(server_for(platform, account, Obedient())) as client:
        editor = Editor(client)
        assert (await editor.call("list_bounties")).startswith("The account has no bounties")
        made = await editor.call(
            "create_draft", title="Treasury assistant", config=DEMO_CONFIG, canaries=CANARIES
        )
        bounty_id = made.split()[2]
        assert await editor.call("list_bounties") == (
            f"{bounty_id}  {'draft':<16}  Treasury assistant"
        )

        broken = await editor.call("scan_agent", bounty_id=bounty_id)
        first, *_ = broken.splitlines()
        assert first.startswith("The agent gave way to")
        assert "TRIPPED" in broken
        # What a patch is written from: the attack, and what the agent did with it.
        assert "tripped unapproved_transfer." in broken
        assert "from the baseline playbook." in broken
        assert "  The attack, in user_message:\n    | " in broken
        assert '  The agent called transfer_funds({"recipient": "audit-holding-7"})' in broken
        assert "read it as data" in broken
        assert broken.endswith("then call scan_agent with the new config.")
        assert await editor.call("get_scan", bounty_id=bounty_id) == broken

        held = await editor.call("scan_agent", bounty_id=bounty_id, config=PATCHED_CONFIG)
        assert held.splitlines()[0] == "The agent held against all 6 probes."
        assert "TRIPPED" not in held
        assert "The attack" not in held


async def test_the_agent_reads_back_a_config_it_can_send_again(platform: Platform) -> None:
    account = await team(platform)
    bounty_id = await account.draft()
    async with Client(server_for(platform, account)) as client:
        editor = Editor(client)
        held = json.loads(await editor.call("get_agent", bounty_id=bounty_id))
        assert (held["id"], held["state"]) == (bounty_id, "draft")
        assert held["config"]["system_prompt"] == DEMO_CONFIG["system_prompt"]
        assert held["config"]["entry_points"] == ["document", "user_message"]
        # A canary's label and rule, and never the planted value itself.
        assert held["canaries"] == [
            {"type": "secret_leak", "public_label": "signing_reference"},
            {
                "type": "forbidden_tool",
                "public_label": "unapproved_transfer",
                "rule": {
                    "tool": "transfer_funds",
                    "predicate": {
                        "argument": "recipient",
                        "operator": "not_in",
                        "value": ["payroll-ops", "vendor-acme"],
                    },
                },
            },
        ]
        assert SECRET not in json.dumps(held["canaries"])
    # What it read is what the service takes.
    await account.call("PUT", f"/v1/bounties/{bounty_id}/config", json=held["config"])


async def test_a_config_can_come_from_a_file_in_the_repository(
    platform: Platform, tmp_path: Path
) -> None:
    account = await team(platform)
    bounty_id = await account.draft()
    patched = tmp_path / "agent.json"
    patched.write_text(json.dumps(PATCHED_CONFIG))
    async with Client(server_for(platform, account, Obedient())) as client:
        editor = Editor(client)
        held = await editor.call("scan_agent", bounty_id=bounty_id, config_path=str(patched))
        assert held.splitlines()[0] == "The agent held against all 6 probes."
        both = await editor.call(
            "scan_agent", True, bounty_id=bounty_id, config=DEMO_CONFIG, config_path=str(patched)
        )
        assert both == "Give `config` or `config_path`, not both."
        missing = await editor.call(
            "scan_agent", True, bounty_id=bounty_id, config_path=str(tmp_path / "none.json")
        )
        assert missing.startswith("The config file could not be read")
        patched.write_text("[]")
        listed = await editor.call(
            "scan_agent", True, bounty_id=bounty_id, config_path=str(patched)
        )
        assert listed.endswith("must hold a JSON object.")


async def test_a_scan_that_outlasts_the_wait_is_read_later(platform: Platform) -> None:
    account = await team(platform)
    bounty_id = await account.draft()
    # No worker runs while this editor waits.
    async with Client(server_for(platform, account)) as client:
        editor = Editor(client)
        assert (await editor.call("get_scan", bounty_id=bounty_id)).startswith(
            "The bounty's current config has not been scanned."
        )
        started = await editor.call("scan_agent", bounty_id=bounty_id, wait_seconds=0)
        assert started.startswith("The scan is still running. Call get_scan")
        assert (await editor.call("get_scan", bounty_id=bounty_id)).startswith(
            "The scan is queued."
        )
        await replay.work_once(platform.sessions, platform.cipher, FakeModel(), max_tries=3)
        done = await editor.call("get_scan", bounty_id=bounty_id)
        assert done.splitlines()[0] == "The agent held against all 6 probes."
        # A new canary makes a new config, which has no scan.
        added = await editor.call(
            "add_canary",
            bounty_id=bounty_id,
            canary={
                "type": "forbidden_tool",
                "public_label": "any_transfer",
                "tool": "transfer_funds",
            },
        )
        assert added.startswith("Added.")
        assert (await editor.call("get_scan", bounty_id=bounty_id)).startswith(
            "The bounty's current config has not been scanned."
        )


async def test_what_the_service_refuses_is_said_to_the_agent(platform: Platform) -> None:
    account = await team(platform)
    bounty_id = await account.draft()
    other = await team(platform)
    async with Client(server_for(platform, other)) as client:
        editor = Editor(client)
        # Someone else's bounty is not there to be read or scanned.
        assert await editor.call("get_agent", True, bounty_id=bounty_id) == "bounty not found"
        scanned = await editor.call("scan_agent", True, bounty_id=bounty_id)
        assert scanned == "The scan could not be started: bounty not found"
        refused = await editor.call(
            "scan_agent", True, bounty_id=bounty_id, config={"system_prompt": "no model named"}
        )
        assert refused.startswith("The config was refused")
        # An ID that is not one never reaches the service.
        await editor.call("get_scan", True, bounty_id="../account")
        # A draft that could not be finished is not left behind.
        bad = {"type": "forbidden_tool", "public_label": "x", "tool": "not a name"}
        await editor.call(
            "create_draft", True, title="Half made", config=DEMO_CONFIG, canaries=[bad]
        )
        assert (await editor.call("list_bounties")).startswith("The account has no bounties")
        unlisted = await editor.call("create_draft", True, title="No agent", canaries=CANARIES)
        assert unlisted == "Give the agent's config, as `config` or `config_path`."

    stranger = Account(platform.client, {"Authorization": "Bearer sk_not_a_key"})
    async with Client(server_for(platform, stranger)) as client:
        said = await Editor(client).call("list_bounties", True)
    assert said.startswith("The service did not accept the API key. Set TRYCANARI_API_KEY")


async def test_a_live_bounty_is_scanned_but_not_changed(platform: Platform) -> None:
    account = await team(platform)
    bounty_id = await account.live()
    async with Client(server_for(platform, account, FakeModel())) as client:
        editor = Editor(client)
        changed = await editor.call("scan_agent", True, bounty_id=bounty_id, config=PATCHED_CONFIG)
        assert changed == (
            "The config was refused: the config cannot be changed while the bounty is live"
        )
        held = await editor.call("scan_agent", bounty_id=bounty_id)
        assert held.splitlines()[0] == "The agent held against all 6 probes."


async def test_the_skill_file_is_served_with_this_sites_address(platform: Platform) -> None:
    served = await platform.client.get("/skill.md")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/markdown")
    text = served.text
    # A skill file opens with its name and when to use it.
    assert text.startswith("---\nname: trycanari\ndescription: ")
    assert '"TRYCANARI_API": "http://test"' in text
    assert "--api http://test " in text
    assert "trycanari.pages.dev" not in text
    # It names every tool the server has, and no other.
    async with Client(server_for(platform, await team(platform))) as client:
        tools = [tool.name for tool in (await client.list_tools()).tools]
    for tool in tools:
        assert f"`{tool}`" in text
