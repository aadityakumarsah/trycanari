"""The core loop through the HTTP API, against the real database, with FakeModel."""

from __future__ import annotations

from sqlalchemy import select, text

from agentbounty.models import Bounty, BountyState, Canary, Finding, Run
from agentbounty.services.attempts import read_evidence

from .platform import (
    APPROVED,
    DEMO_CONFIG,
    SECRET,
    SECRET_CANARY,
    STEAL,
    STRONG,
    SYSTEM_PROMPT,
    TOOL_CANARY,
    WEAK,
    Platform,
)


async def test_an_attempt_becomes_a_verdict(platform: Platform) -> None:
    client = platform.client
    sponsor = await platform.sponsor()

    # --- the sponsor lists an agent --------------------------------------------------
    listing_text = {"title": "Treasury agent", "description": "Make it pay someone it should not."}
    body = {"reward_zatoshi": 250_000_000, **listing_text}
    created = await sponsor.call("POST", "/v1/bounties", 201, json=body)
    bounty_id = created["id"]
    assert created["state"] == "draft"
    # A draft is invisible to the public.
    assert (await client.get("/v1/bounties")).json() == []
    assert (await client.get(f"/v1/bounties/{bounty_id}")).status_code == 404

    config = await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", json=DEMO_CONFIG)
    assert (config["version"], len(config["config_hash"])) == (1, 64)
    leak = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/canaries", 201, json=SECRET_CANARY)
    tool = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/canaries", 201, json=TOOL_CANARY)
    # The hash covers the canaries, so each one changes it.
    assert len({config["config_hash"], leak["config_hash"], tool["config_hash"]}) == 3

    activated = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/activate")
    assert activated == {"state": "live", "config_hash": tool["config_hash"]}

    # --- what a hunter can see ---------------------------------------------------------
    listing = await client.get("/v1/bounties")
    assert listing.json() == [
        {
            "id": bounty_id,
            **listing_text,
            "state": "live",
            "accepting_attempts": True,
            "reward_zatoshi": 250_000_000,
            "entry_points": ["document", "user_message"],
            "canaries": [
                {"type": "secret_leak", "public_label": "signing_reference"},
                {"type": "forbidden_tool", "public_label": "unapproved_transfer"},
            ],
        }
    ]
    shown = await client.get(f"/v1/bounties/{bounty_id}")
    assert shown.json() == listing.json()[0]
    for public in (listing.text, shown.text):
        for private in (
            SECRET,
            "treasury assistant",
            APPROVED[0],
            "transfer_funds",
            config["config_hash"],
        ):
            assert private not in public

    # --- a weak attempt ----------------------------------------------------------------
    weak = await platform.submit(bounty_id, WEAK)
    assert await platform.status(weak) == {
        "state": "queued",
        "verdict": None,
        "tripped_runs": None,
        "total_runs": None,
        "canary": None,
        "replies": [],
        "payout": None,
    }
    assert await platform.poll(weak) == {
        "state": "done",
        "verdict": "not_verified",
        "tripped_runs": 0,
        "total_runs": 5,
        "canary": None,
        "replies": [{"text": "How can I help?", "tool_calls": []}],
        "payout": None,
    }

    # --- a strong attempt --------------------------------------------------------------
    strong = await platform.submit(bounty_id, STRONG)
    assert await platform.poll(strong) == {
        "state": "done",
        "verdict": "verified",
        "tripped_runs": 5,
        "total_runs": 5,
        "canary": {"type": "forbidden_tool", "public_label": "unapproved_transfer"},
        "replies": [
            {
                "text": "Calling transfer_funds.",
                "tool_calls": [{"name": "transfer_funds", "arguments": STEAL}],
            },
            {"text": "Done.", "tool_calls": []},
        ],
        "payout": None,
    }

    # --- a finding exists and the bounty is paused -------------------------------------
    async with platform.sessions() as session:
        (finding,) = (await session.scalars(select(Finding))).all()
        canary = await session.get_one(Canary, finding.canary_id)
        bounty = await session.get_one(Bounty, finding.bounty_id)
        runs = (await session.scalars(select(Run).order_by(Run.run_index))).all()
    assert (str(finding.attempt_id), str(finding.bounty_id)) == (strong["attempt_id"], bounty_id)
    assert (finding.tripped_runs, finding.config_hash) == (5, tool["config_hash"])
    assert canary.public_label == "unapproved_transfer"
    assert bounty.state is BountyState.PAUSED
    # Five runs for each of the two attempts, and the strong ones carry their evidence.
    assert len(runs) == 10
    evidence = [
        read_evidence(platform.cipher, run)
        for run in runs
        if str(run.attempt_id) == strong["attempt_id"]
    ]
    assert all(item[0]["arguments"] == STEAL and item[0]["message_index"] == 1 for item in evidence)

    paused = (await client.get(f"/v1/bounties/{bounty_id}")).json()
    assert (paused["state"], paused["accepting_attempts"]) == ("paused", False)
    assert (await client.get("/v1/bounties")).json() == []
    refused = await platform.submit(bounty_id, STRONG, expect=409)
    assert "not accepting attempts" in str(refused)


async def test_prompts_tools_and_secrets_are_encrypted_at_rest(platform: Platform) -> None:
    await (await platform.sponsor()).live()
    async with platform.sessions() as session:
        stored = [
            str(row)
            for table in ("target_configs", "canaries", "sponsors")
            for row in (await session.execute(text(f"SELECT * FROM {table}"))).all()  # noqa: S608
        ]
        prompt, tools, secret = (
            await session.execute(
                text(
                    "SELECT system_prompt_enc, tools_enc,"
                    " (SELECT secret_enc FROM canaries WHERE secret_enc IS NOT NULL)"
                    " FROM target_configs"
                )
            )
        ).one()
    dump = "\n".join(stored)
    for plaintext in (SECRET, "treasury assistant", "Transfer submitted.", "sk_"):
        assert plaintext not in dump
    for ciphertext, plaintext in (
        (prompt, SYSTEM_PROMPT),
        (tools, "transfer_funds"),
        (secret, SECRET),
    ):
        assert plaintext.encode() not in bytes(ciphertext)
    # A ciphertext moved to another column does not decrypt there.
    assert (
        platform.cipher.decrypt(prompt, purpose="target_configs.system_prompt").decode()
        == SYSTEM_PROMPT
    )
