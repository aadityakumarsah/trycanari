"""What a sponsor may read about their own bounties: status, findings and the ledger."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import (
    Attempt,
    AttemptState,
    Bounty,
    Canary,
    Deposit,
    Finding,
    LedgerEntry,
    Payout,
    Refund,
    Run,
    Sponsor,
)
from agentbounty.services import compliance, house, rearm, scan
from agentbounty.services.attempts import read_evidence, read_transcript
from agentbounty.services.bounty import LoadedTarget, latest_config, load_target
from agentbounty.services.crypto import Cipher
from agentbounty.services.ledger import bounty_balance
from agentbounty.services.payment import required_balance


@dataclass(frozen=True)
class BountySummary:
    bounty: Bounty
    balance_zatoshi: int
    required_zatoshi: int


@dataclass(frozen=True)
class BountyDetail:
    summary: BountySummary
    refund_address: str | None
    # The working config, decrypted for its owner, or None before one is uploaded.
    target: LoadedTarget | None
    config_version: int | None
    config_hash: str | None
    config_is_live: bool


async def summarise(session: AsyncSession, bounty: Bounty) -> BountySummary:
    return BountySummary(bounty, await bounty_balance(session, bounty.id), required_balance(bounty))


async def sponsor_bounties(session: AsyncSession, sponsor: Sponsor) -> list[BountySummary]:
    bounties = await session.scalars(
        select(Bounty)
        .where(Bounty.sponsor_id == sponsor.id, Bounty.deleted_at.is_(None))
        .order_by(Bounty.created_at, Bounty.id)
    )
    return [await summarise(session, bounty) for bounty in bounties]


async def bounty_detail(session: AsyncSession, cipher: Cipher, bounty: Bounty) -> BountyDetail:
    row = await latest_config(session, bounty.id)
    sponsor = await session.get_one(Sponsor, bounty.sponsor_id)
    return BountyDetail(
        summary=await summarise(session, bounty),
        refund_address=sponsor.refund_address,
        target=await load_target(session, cipher, row) if row else None,
        config_version=row.version if row else None,
        config_hash=row.config_hash if row else None,
        config_is_live=row is not None and row.id == bounty.live_config_id,
    )


@dataclass(frozen=True)
class EvidenceItem:
    canary: Canary | None
    message_index: int
    location: str
    # Secret leaks: the encoding the secret was found in.
    representation: str | None
    # What the agent emitted at that point: the reply, or the offending call's arguments.
    matched_text: str


@dataclass(frozen=True)
class RunReport:
    run_index: int
    transcript: list[dict[str, Any]]
    evidence: list[EvidenceItem]


@dataclass(frozen=True)
class FindingReport:
    finding: Finding
    canary: Canary
    # The fills the hunter submitted: the minimal reproduction, and a regression test.
    attack: list[dict[str, Any]]
    payout: Payout | None
    runs: list[RunReport]


def _matched_text(transcript: list[dict[str, Any]], item: dict[str, Any]) -> str:
    index = item.get("message_index")
    if not isinstance(index, int) or not 0 <= index < len(transcript):
        return ""
    message = transcript[index]
    location = str(item.get("location") or "")
    if location.startswith("tool_call:"):
        name = location.removeprefix("tool_call:")
        arguments = item.get("arguments")
        if arguments is None:
            arguments = next(
                (c["arguments"] for c in message.get("tool_calls", []) if c["name"] == name), {}
            )
        return f"{name}({json.dumps(arguments, indent=2)})"
    return str(message.get("content", ""))


async def findings_report(
    session: AsyncSession, cipher: Cipher, bounty: Bounty
) -> list[FindingReport]:
    findings = await session.scalars(
        select(Finding)
        .where(Finding.bounty_id == bounty.id)
        .order_by(Finding.verified_at, Finding.id)
    )
    reports = []
    for finding in findings:
        attempt = await session.get_one(Attempt, finding.attempt_id)
        runs = await session.scalars(
            select(Run).where(Run.attempt_id == attempt.id).order_by(Run.run_index)
        )
        run_reports = []
        for run in runs:
            transcript = read_transcript(cipher, run)
            evidence = [
                EvidenceItem(
                    canary=await session.get(Canary, uuid.UUID(item["canary_id"])),
                    message_index=item["message_index"],
                    location=item["location"],
                    representation=item.get("representation"),
                    matched_text=_matched_text(transcript, item),
                )
                for item in read_evidence(cipher, run)
            ]
            run_reports.append(RunReport(run.run_index, transcript, evidence))
        reports.append(
            FindingReport(
                finding=finding,
                canary=await session.get_one(Canary, finding.canary_id),
                attack=attempt.payload_json,
                payout=await session.scalar(select(Payout).where(Payout.finding_id == finding.id)),
                runs=run_reports,
            )
        )
    return reports


@dataclass(frozen=True)
class LedgerLine:
    kind: str
    amount_zatoshi: int
    # The chain transaction behind the entry, once there is one.
    txid: str | None
    created_at: datetime


async def ledger_report(session: AsyncSession, bounty: Bounty) -> tuple[list[LedgerLine], int]:
    """The bounty's ledger, oldest first, and its balance."""
    entries = list(
        await session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.bounty_id == bounty.id)
            .order_by(LedgerEntry.created_at, LedgerEntry.kind, LedgerEntry.id)
        )
    )
    txids: dict[uuid.UUID, str | None] = {}
    for table in (Deposit, Payout, Refund):
        found = await session.execute(
            select(table.id, table.txid).where(table.id.in_([e.ref_id for e in entries]))
        )
        txids.update({row.id: row.txid for row in found})
    lines = [
        LedgerLine(e.kind.value, e.amount_zatoshi, txids.get(e.ref_id), e.created_at)
        for e in entries
    ]
    return lines, await bounty_balance(session, bounty.id)


async def compliance_evidence(
    session: AsyncSession, cipher: Cipher, bounty: Bounty
) -> compliance.Evidence:
    """Everything recorded about a bounty that its audit report is drawn from."""
    detail = await bounty_detail(session, cipher, bounty)
    target = detail.target
    tripwires = tuple(
        compliance.Tripwire(
            label=canary.definition.public_label,
            type=canary.definition.type.value,
            in_prompt=bool(canary.definition.secret)
            and target is not None
            and str(canary.definition.secret) in target.config.system_prompt,
        )
        for canary in (target.canaries if target else ())
    )
    found, _ = await scan.latest(session, bounty)
    done = found is not None and found.state == "done"
    probes = tuple(
        compliance.Probe(
            name=str(result["name"]),
            technique=str(result["technique"]),
            aimed_at=str(result["aimed_at"]),
            tripped=tuple(result["tripped"]),
        )
        for result in (scan.results_of(cipher, found) if found is not None and done else [])
    )
    settled = Attempt.state == AttemptState.DONE
    attacks, judged, by_house = (
        await session.execute(
            select(
                func.count(Attempt.id),
                func.count(Attempt.id).filter(settled),
                func.count(Attempt.id).filter(settled, func.coalesce(Sponsor.is_house, false())),
            )
            .outerjoin(Sponsor, Sponsor.id == Attempt.account_id)
            .where(Attempt.bounty_id == bounty.id)
        )
    ).one()
    hunt = await house.latest(session, bounty, detail.config_hash)
    broken = await session.execute(
        select(Finding, Canary, Attempt.payload_json)
        .join(Canary, Canary.id == Finding.canary_id)
        .join(Attempt, Attempt.id == Finding.attempt_id)
        .where(Finding.bounty_id == bounty.id)
        .order_by(Finding.verified_at, Finding.id)
    )
    breaks = tuple(
        compliance.Break(
            id=str(finding.id),
            label=canary.public_label,
            type=canary.type.value,
            slots=tuple(str(fill["slot"]) for fill in payload),
            config_hash=finding.config_hash,
            tripped_runs=finding.tripped_runs,
            verified_at=finding.verified_at,
        )
        for finding, canary, payload in broken
    )
    check = (
        await rearm.check_for(session, bounty.id, detail.config_hash)
        if detail.config_hash is not None
        else None
    )
    return compliance.Evidence(
        bounty_id=str(bounty.id),
        title=bounty.title,
        state=bounty.state.value,
        watch=bounty.watch,
        model=target.config.model if target else None,
        config_version=detail.config_version,
        config_hash=detail.config_hash,
        entry_points=tuple(sorted(target.config.entry_points)) if target else (),
        tools=tuple(tool.name for tool in target.config.tools) if target else (),
        tripwires=tripwires,
        scan_state=found.state if found is not None else None,
        scan_finished_at=found.finished_at if found is not None else None,
        scan_with_library=found.with_library if found is not None else False,
        probes=probes,
        attacks=attacks,
        attacks_judged=judged,
        house_attacks=by_house,
        house_outcome=None if hunt is None else (hunt.outcome or "running"),
        breaks=breaks,
        rearm=check.state.value if check is not None else None,
    )
