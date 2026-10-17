"""TryCanari's isolated model-output control API.

No existing bounty, replay, payment, or account workflow depends on this router.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from agentbounty.api.deps import CipherDep, SponsorDep, Transaction
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.models import ControlProject
from agentbounty.services import control
from agentbounty.services.errors import ServiceError

router = APIRouter(
    prefix="/v1/control",
    tags=["control"],
    dependencies=[Depends(limit_by_client)],
)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvariantIn(Body):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: control.InvariantKind
    description: str = Field(min_length=3, max_length=500)
    value: str | int | dict[str, object] | None = None


class AttributionIn(Body):
    component: str = Field(min_length=1, max_length=200)
    score: float = Field(ge=-1_000_000, le=1_000_000)
    source: str = Field(min_length=1, max_length=200)


class InspectIn(Body):
    prompt: str = Field(default="", max_length=20_000)
    response: str = Field(min_length=1, max_length=100_000)
    context: str = Field(default="", max_length=200_000)
    invariants: list[InvariantIn] = Field(min_length=1, max_length=50)
    attributions: list[AttributionIn] = Field(default_factory=list, max_length=2_000)


class CheckOut(BaseModel):
    invariant_id: str
    kind: control.InvariantKind
    verdict: control.Verdict
    confidence: float
    evidence: list[str]
    evidence_kind: Literal["behavioral_probe"]


class InspectOut(BaseModel):
    run_id: str
    input_digest: str
    response_digest: str
    overall: control.Verdict
    checks: list[CheckOut]
    trace: dict[str, object]


def _invariants(body: InspectIn) -> tuple[control.Invariant, ...]:
    return tuple(
        control.Invariant(item.id, item.kind, item.description, item.value)
        for item in body.invariants
    )


def _checks(body: InspectIn) -> tuple[control.Check, ...]:
    return control.inspect(body.response, body.context, _invariants(body))


def _overall(checks: tuple[control.Check, ...]) -> control.Verdict:
    if any(item.verdict == "fail" for item in checks):
        return "fail"
    if any(item.verdict == "unknown" for item in checks):
        return "unknown"
    return "pass"


@router.get("/capabilities")
async def capabilities(_: SponsorDep) -> dict[str, object]:
    return {
        "primitives": ["detect", "trace", "steer", "extract", "synthesize"],
        "invariants": ["grounded", "contains", "excludes", "json_schema", "max_length"],
        "latent_trace": {
            "requires": "activation-capable model adapter",
            "text_only_provider": "unavailable",
        },
        "retention": "stateless",
    }


@router.post("/inspect", response_model=InspectOut)
async def inspect(body: InspectIn, _: SponsorDep) -> InspectOut:
    checks = _checks(body)
    attributions = (
        control.Attribution(item.component, item.score, item.source)
        for item in body.attributions
    )
    return InspectOut(
        run_id=control.run_id(),
        input_digest=control.digest(f"{body.prompt}\0{body.context}"),
        response_digest=control.digest(body.response),
        overall=_overall(checks),
        checks=[
            CheckOut(
                invariant_id=item.invariant_id,
                kind=item.kind,
                verdict=item.verdict,
                confidence=item.confidence,
                evidence=list(item.evidence),
                evidence_kind=item.evidence_kind,
            )
            for item in checks
        ],
        trace=control.trace(attributions),
    )


class WorkflowOut(InspectOut):
    principles: list[str]
    steering_plan: list[dict[str, str]]
    edge_cases: list[dict[str, str]]


def _workflow(body: InspectIn) -> WorkflowOut:
    invariants = _invariants(body)
    checks = _checks(body)
    attributions = (
        control.Attribution(item.component, item.score, item.source)
        for item in body.attributions
    )
    check_output = [
        CheckOut(
            invariant_id=item.invariant_id,
            kind=item.kind,
            verdict=item.verdict,
            confidence=item.confidence,
            evidence=list(item.evidence),
            evidence_kind=item.evidence_kind,
        )
        for item in checks
    ]
    return WorkflowOut(
        run_id=control.run_id(),
        input_digest=control.digest(f"{body.prompt}\0{body.context}"),
        response_digest=control.digest(body.response),
        overall=_overall(checks),
        checks=check_output,
        trace=control.trace(attributions),
        principles=list(control.principles(invariants, checks)),
        steering_plan=list(control.steering_plan(checks)),
        edge_cases=[case for invariant in invariants for case in control.synthesize(invariant)],
    )


def _only_requested(result: WorkflowOut, requested: list[control.Operation]) -> WorkflowOut:
    """Hide primitive outputs that the project did not authorize for this run."""
    data = result.model_dump()
    allowed = set(requested)
    if "detect" not in allowed:
        data["checks"] = []
    if "trace" not in allowed:
        data["trace"] = {
            "status": "not_requested",
            "evidence_kind": None,
            "reason": "Trace was not requested for this run.",
            "components": [],
        }
    if "steer" not in allowed:
        data["steering_plan"] = []
    if "extract" not in allowed:
        data["principles"] = []
    if "synthesize" not in allowed:
        data["edge_cases"] = []
    return WorkflowOut.model_validate(data)


@router.post("/workflow", response_model=WorkflowOut)
async def workflow(body: InspectIn, _: SponsorDep) -> WorkflowOut:
    return _workflow(body)


class ProjectCreate(Body):
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    adapter_kind: Literal["text", "activation"] = "text"
    operations: list[control.Operation] = Field(
        default_factory=lambda: list(control.OPERATIONS), min_length=1, max_length=5
    )


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    model: str
    adapter_kind: str
    operations: list[str]
    created_at: datetime


def _project_out(project: ControlProject) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        model=project.model,
        adapter_kind=project.adapter_kind,
        operations=project.operations_json,
        created_at=project.created_at,
    )


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate, session: Transaction, sponsor: SponsorDep
) -> ProjectOut:
    operations = list(dict.fromkeys(body.operations))
    project = ControlProject(
        sponsor_id=sponsor.id,
        name=body.name.strip(),
        model=body.model.strip(),
        adapter_kind=body.adapter_kind,
        operations_json=operations,
    )
    session.add(project)
    await session.flush()
    return _project_out(project)


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(session: Transaction, sponsor: SponsorDep) -> list[ProjectOut]:
    rows = await session.scalars(
        select(ControlProject)
        .where(ControlProject.sponsor_id == sponsor.id)
        .order_by(ControlProject.created_at.desc())
    )
    return [_project_out(project) for project in rows]


class ProjectWorkflowIn(InspectIn):
    operations: list[control.Operation] = Field(
        default_factory=lambda: list(control.OPERATIONS), min_length=1, max_length=5
    )


class StoredWorkflowOut(WorkflowOut):
    id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime


@router.post("/projects/{project_id}/workflow", response_model=StoredWorkflowOut)
async def project_workflow(
    project_id: uuid.UUID,
    body: ProjectWorkflowIn,
    session: Transaction,
    sponsor: SponsorDep,
    cipher: CipherDep,
) -> StoredWorkflowOut:
    project = await control.own_project(session, project_id, sponsor.id)
    requested = list(dict.fromkeys(body.operations))
    control.require_operations(project.operations_json, requested)
    if body.attributions and project.adapter_kind != "activation":
        raise ServiceError(
            422, "attributions require a project with an activation-capable adapter"
        )
    result = _only_requested(_workflow(body), requested)
    result_data = result.model_dump()
    events: list[tuple[str, dict[str, object]]] = [
        ("run.created", {"operations": requested, "model": project.model}),
    ]
    if "detect" in requested:
        events.append(("behavior.detected", {"overall": result.overall}))
    if "trace" in requested:
        events.append(("behavior.traced", {"status": result.trace["status"]}))
    if "steer" in requested:
        events.append(("steering.planned", {"actions": len(result.steering_plan)}))
    if "extract" in requested:
        events.append(("principles.extracted", {"count": len(result.principles)}))
    if "synthesize" in requested:
        events.append(("edge_cases.synthesized", {"count": len(result.edge_cases)}))
    row = await control.save_run(
        session,
        cipher,
        project,
        input_digest=result.input_digest,
        response_digest=result.response_digest,
        overall=result.overall,
        result=result_data,
        events=events,
    )
    return StoredWorkflowOut(
        **result_data,
        id=row.id,
        project_id=project.id,
        created_at=row.created_at,
    )


class RunSummaryOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    input_digest: str
    response_digest: str
    overall: str
    created_at: datetime


@router.get("/projects/{project_id}/runs", response_model=list[RunSummaryOut])
async def list_runs(
    project_id: uuid.UUID, session: Transaction, sponsor: SponsorDep
) -> list[RunSummaryOut]:
    rows = await control.project_runs(session, project_id, sponsor.id)
    return [
        RunSummaryOut(
            id=row.id,
            project_id=row.project_id,
            input_digest=row.input_digest,
            response_digest=row.response_digest,
            overall=row.overall,
            created_at=row.created_at,
        )
        for row in rows
    ]


class EventOut(BaseModel):
    action: str
    detail: dict[str, object]
    created_at: datetime


class StoredRunOut(BaseModel):
    id: uuid.UUID
    project: ProjectOut
    result: WorkflowOut
    events: list[EventOut]
    created_at: datetime


@router.get("/runs/{run_id}", response_model=StoredRunOut)
async def read_run(
    run_id: uuid.UUID,
    session: Transaction,
    sponsor: SponsorDep,
    cipher: CipherDep,
) -> StoredRunOut:
    run, project, events = await control.own_run(session, run_id, sponsor.id)
    decrypted = cipher.decrypt_json(run.result_enc, purpose=control.RUN_RESULT)
    result = WorkflowOut.model_validate(decrypted)
    return StoredRunOut(
        id=run.id,
        project=_project_out(project),
        result=result,
        events=[
            EventOut(action=item.action, detail=item.detail_json, created_at=item.created_at)
            for item in events
        ],
        created_at=run.created_at,
    )
