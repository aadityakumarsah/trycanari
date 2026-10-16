"""Durable finding evidence, regression replay and the compounding attack corpus."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from agentbounty.models import Run, SavedAttack
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import regression
from agentbounty.services.attempts import read_transcript
from agentbounty.services.bounty import load_target
from agentbounty.services.model import FakeModel

from .platform import STRONG, Platform, Sponsor


async def verified(platform: Platform, email: str = "evidence@example.com") -> tuple[Sponsor, str]:
    sponsor = await platform.sponsor(email)
    bounty_id = await sponsor.funded(platform)
    attempt = await platform.submit(bounty_id, STRONG)
    await platform.work()
    await platform.payments.tick()
    assert (await platform.status(attempt))["verdict"] == "verified"
    return sponsor, bounty_id


async def test_finding_page_highlights_the_tripping_message(platform: Platform) -> None:
    sponsor, bounty_id = await verified(platform)
    (finding,) = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/findings")
    page = await platform.client.get(
        f"/evidence/bounties/{bounty_id}/findings/{finding['id']}", headers=sponsor.headers
    )
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "URGENT:" in page.text
    assert "transfer_funds" in page.text
    assert page.text.count('class="run"') == 5
    assert 'class="message highlight" data-message-index="1"' in page.text
    assert finding["payout"]["txid"] in page.text
    assert "Print or save as PDF" in page.text


async def test_saved_regression_replays_the_exact_attack_and_reproduces_verdict(
    platform: Platform,
) -> None:
    sponsor, bounty_id = await verified(platform, "regression@example.com")
    (shown,) = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/regressions")
    assert shown["canary_type"] == "forbidden_tool"
    assert shown["attack"] == [{"slot": "user_message", "content": STRONG, "turns": []}]

    async with platform.sessions() as session:
        case = await session.get_one(SavedAttack, uuid.UUID(shown["id"]))
        row = await session.scalar(
            select(TargetConfigRow).where(
                TargetConfigRow.bounty_id == uuid.UUID(bounty_id),
                TargetConfigRow.config_hash == case.broken_config_hash,
            )
        )
        assert row is not None
        target = await load_target(session, platform.cipher, row)
        reproduced = await regression.replay(case, target, FakeModel())
        runs = list(await session.scalars(select(Run)))

    assert reproduced.verified is True
    assert reproduced.judgement.tripped_runs == 5
    assert case.payload_json == shown["attack"]
    # The corpus record has no canary definition; transcripts remain ciphertext at rest.
    assert set(case.__dict__) >= {"canary_type", "payload_json", "broken_config_hash"}
    assert all(STRONG.encode() not in run.transcript_enc for run in runs)
    assert any(STRONG in str(read_transcript(platform.cipher, run)) for run in runs)


async def test_corpus_can_run_every_saved_attack_of_one_type(platform: Platform) -> None:
    source, _ = await verified(platform, "corpus-source@example.com")
    # The source did not opt into the older shared feed; the durable corpus still owns it.
    assert source is not None
    target = await platform.sponsor("corpus-target@example.com")
    bounty_id = await target.draft()
    path = f"/v1/bounties/{bounty_id}/scan"
    await target.call("POST", path, 202, params={"corpus_type": "forbidden_tool"})
    await platform.work()
    result = await target.call("GET", path)
    corpus = [probe for probe in result["probes"] if probe["source"] == "library"]
    assert result["corpus_type"] == "forbidden_tool"
    assert len(corpus) == 1
    assert corpus[0]["tripped"] == ["unapproved_transfer"]
