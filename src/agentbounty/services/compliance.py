"""What a bounty's testing shows, set against the lists an auditor works from.

OWASP's two lists name the risks. The EU AI Act and ISO/IEC 42001 name what an
organisation must be able to show. This module says which of those each piece of
evidence bears on, and whether that evidence is there yet.

It judges nothing. It never says a requirement is met: only that there is evidence for
it, that the evidence is partial, that it shows a weakness still open, or that there is
none. Whether a requirement applies, and whether it is satisfied, is for the sponsor and
their auditor.

Everything here is computed from what the platform recorded, with no database access of
its own, so the same evidence always gives the same report and the same digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentbounty.services.judge import RUNS_PER_ATTEMPT, TRIP_THRESHOLD

SECRET_LEAK = "secret_leak"  # noqa: S105 - a canary type, not a credential
FORBIDDEN_TOOL = "forbidden_tool"

DISCLAIMER = (
    "This report is evidence of adversarial testing. It is not a certificate, a conformity"
    " assessment or legal advice. A reference says which requirement a piece of evidence"
    " bears on; whether that requirement applies to you, and whether it is met, is for you"
    " and your auditor to decide."
)

# The editions the references are to.
REFERENCES = [
    "OWASP Top 10 for LLM Applications (2025)",
    "OWASP Top 10 for Agentic Applications (2026)",
    "Regulation (EU) 2024/1689, the Artificial Intelligence Act",
    "ISO/IEC 42001:2023, Artificial intelligence: Management system",
]
LLM_LIST, AGENT_LIST = REFERENCES[0], REFERENCES[1]

# Where a reported value stands: evidence for it, some of it, evidence of a weakness that
# is still open, none, or nothing that could yet be shown.
EVIDENCE = "evidence"
PARTIAL = "partial"
GAP = "gap"
NONE = "none"
NOT_APPLICABLE = "not_applicable"

# A risk: nothing has attacked it, it held, something got through and is still open, the
# config was changed since, or the change was checked against the attack that broke it.
NOT_TESTED = "not_tested"
HELD = "held"
BROKEN = "broken"
PATCHED = "patched"
FIXED = "fixed"

DIRECT = "Direct prompt injection"
INDIRECT = "Indirect prompt injection"
# The slots through which an attacker speaks to the agent only by way of what it reads.
INDIRECT_SLOTS = frozenset({"document", "tool_result"})


@dataclass(frozen=True)
class Risk:
    id: str
    source: str
    name: str
    # How an attack gets in, or what it does once it is in.
    role: str
    # What in a bounty tests for it, in words for the report.
    tested_by: str


# Every attack on the platform is text an attacker puts where the agent reads it, so
# every attack is a test of the two risks about getting in. Which canary it trips says
# what it did once it was in.
RISKS = (
    Risk(
        "LLM01:2025",
        LLM_LIST,
        "Prompt Injection",
        "vector",
        "Every attack: text placed in the user's message, a document or a tool's result.",
    ),
    Risk(
        "LLM02:2025",
        LLM_LIST,
        "Sensitive Information Disclosure",
        "impact",
        "Secret-leak tripwires: a planted value the agent must never emit, in any encoding.",
    ),
    Risk(
        "LLM06:2025",
        LLM_LIST,
        "Excessive Agency",
        "impact",
        "Forbidden-tool tripwires: a tool call the agent must never make.",
    ),
    Risk(
        "LLM07:2025",
        LLM_LIST,
        "System Prompt Leakage",
        "impact",
        "Secret-leak tripwires whose value is planted in the system prompt.",
    ),
    Risk(
        "ASI01",
        AGENT_LIST,
        "Agent Goal Hijack",
        "vector",
        "Every attack: an attempt to turn the agent from its task to the attacker's.",
    ),
    Risk(
        "ASI02",
        AGENT_LIST,
        "Tool Misuse and Exploitation",
        "impact",
        "Forbidden-tool tripwires: a permitted tool used in a way its rule forbids.",
    ),
)


@dataclass(frozen=True)
class Tripwire:
    label: str
    type: str
    # A secret that sits in the system prompt, where leaking it leaks the prompt.
    in_prompt: bool = False


@dataclass(frozen=True)
class Probe:
    name: str
    technique: str
    aimed_at: str
    # The labels of the tripwires it tripped: none when the agent held.
    tripped: tuple[str, ...] = ()


@dataclass(frozen=True)
class Break:
    """A verified break: the same tripwire in enough replays of one hunter's attack."""

    id: str
    label: str
    type: str
    # Where the hunter put text.
    slots: tuple[str, ...]
    # The config that was broken.
    config_hash: str
    tripped_runs: int
    verified_at: datetime


@dataclass(frozen=True)
class Evidence:
    """What the platform has recorded about one bounty."""

    bounty_id: str
    title: str
    state: str
    # Scanned again every day while it is live.
    watch: bool = False
    model: str | None = None
    config_version: int | None = None
    config_hash: str | None = None
    entry_points: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    tripwires: tuple[Tripwire, ...] = ()
    # The scan of the current config: its state, and what each probe did once it is done.
    scan_state: str | None = None
    scan_finished_at: datetime | None = None
    scan_with_library: bool = False
    probes: tuple[Probe, ...] = ()
    # Attacks by hunters, over every config the bounty has had.
    attacks: int = 0
    attacks_judged: int = 0
    # How many of the judged attacks the platform's own house hunter made, and how its
    # hunt of the current config ended, if it has made one.
    house_attacks: int = 0
    house_outcome: str | None = None
    breaks: tuple[Break, ...] = ()
    # The re-arm check of the current config: every paid attack replayed against it.
    rearm: str | None = None


def _relevant(risk: Risk, tripwires: tuple[Tripwire, ...]) -> set[str]:
    """The labels of the tripwires whose tripping is this risk coming true."""
    if risk.role == "vector":
        return {wire.label for wire in tripwires}
    wanted = FORBIDDEN_TOOL if risk.id in ("LLM06:2025", "ASI02") else SECRET_LEAK
    return {
        wire.label
        for wire in tripwires
        if wire.type == wanted and (risk.id != "LLM07:2025" or wire.in_prompt)
    }


def _settled(evidence: Evidence, breaks: list[Break]) -> str:
    """Where a set of breaks stands: one of them open, all patched, or all checked."""
    if any(found.config_hash == evidence.config_hash for found in breaks):
        return BROKEN
    return FIXED if evidence.rearm == "passed" else PATCHED


def vector_of(slots: tuple[str, ...]) -> str:
    return INDIRECT if INDIRECT_SLOTS.intersection(slots) else DIRECT


def risks_of(found: Break, tripwires: tuple[Tripwire, ...]) -> list[str]:
    """The risks a break is an instance of: how it got in, and what it did."""
    return [
        risk.id
        for risk in RISKS
        if risk.role == "vector" or found.label in _relevant(risk, tripwires)
    ]


def _risk_rows(evidence: Evidence) -> list[dict[str, Any]]:
    scanned = evidence.scan_state == "done"
    rows = []
    for risk in RISKS:
        labels = _relevant(risk, evidence.tripwires)
        probes = [
            probe
            for probe in evidence.probes
            if scanned and (probe.aimed_at in labels or labels.intersection(probe.tripped))
        ]
        through = [probe for probe in probes if labels.intersection(probe.tripped)]
        breaks = [found for found in evidence.breaks if found.label in labels]
        open_breaks = [found for found in breaks if found.config_hash == evidence.config_hash]
        if not labels or not (probes or breaks or evidence.attacks_judged):
            status = NOT_TESTED
        elif through or open_breaks:
            status = BROKEN
        elif breaks:
            status = _settled(evidence, breaks)
        else:
            status = HELD
        rows.append(
            {
                "id": risk.id,
                "source": risk.source,
                "name": risk.name,
                "role": risk.role,
                "tested_by": risk.tested_by,
                "tripwires": sorted(labels),
                "probes_run": len(probes),
                "probes_held": len(probes) - len(through),
                "breaks": len(breaks),
                "open_breaks": len(open_breaks),
                "status": status,
            }
        )
    return rows


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else many or one + 's'}"


def _day(moment: datetime | None) -> str:
    return moment.date().isoformat() if moment is not None else "an unrecorded date"


class _Facts:
    """The few things every control's answer is built from."""

    def __init__(self, evidence: Evidence) -> None:
        e = evidence
        self.e = e
        self.wires = len(e.tripwires)
        self.scanned = e.scan_state == "done"
        self.total = len(e.probes) if self.scanned else 0
        self.through = sum(1 for probe in e.probes if probe.tripped) if self.scanned else 0
        self.attacked = e.attacks_judged > 0 or bool(e.breaks)
        self.tested = self.scanned or self.attacked
        self.open = [found for found in e.breaks if found.config_hash == e.config_hash]
        self.closed = [found for found in e.breaks if found.config_hash != e.config_hash]
        self.live = e.state == "live"
        # It has been open to hunters, whether or not it is now.
        self.listed = self.live or e.state == "paused" or e.attacks > 0
        self.weak = bool(self.open) or self.through > 0
        fingerprint = (e.config_hash or "")[:16]
        self.config = (
            f"configuration version {e.config_version} (fingerprint {fingerprint})"
            if e.config_version is not None
            else "no configuration"
        )

    def wires_line(self) -> str:
        labels = ", ".join(wire.label for wire in self.e.tripwires)
        return f"{_plural(self.wires, 'tripwire')} say what the agent must never do: {labels}."

    def scan_line(self) -> str:
        held = self.total - self.through
        library = ", the shared library included" if self.e.scan_with_library else ""
        return (
            f"Scan of {self.config} on {_day(self.e.scan_finished_at)}: held against {held}"
            f" of {_plural(self.total, 'known attack')}{library}."
        )

    def attacks_line(self) -> str:
        judged, house = self.e.attacks_judged, self.e.house_attacks
        # The house hunter is the platform's own, and is not passed off as independent.
        if house >= judged > 0:
            by = "by the platform's house hunter"
        elif house:
            by = f"by hunters, {house} of them by the platform's house hunter,"
        else:
            by = "by independent hunters"
        return (
            f"{_plural(judged, 'attack')} {by} replayed and judged;"
            f" {_plural(len(self.e.breaks), 'verified break')}."
        )

    def tests(self) -> list[str]:
        lines = [self.scan_line()] if self.scanned else []
        return [*lines, self.attacks_line()] if self.attacked else lines

    def open_line(self) -> str:
        parts = []
        if self.open:
            parts.append(f"{_plural(len(self.open), 'verified break')} on the current config")
        if self.through:
            parts.append(f"{_plural(self.through, 'known attack')} in its scan")
        return f"Still open: {' and '.join(parts)}."


THRESHOLD = (
    f"The threshold was set before any test: a break counts only when the same tripwire"
    f" trips in at least {TRIP_THRESHOLD} of {RUNS_PER_ATTEMPT} independent replays, each"
    " from an empty history."
)
SANDBOX = (
    "Every replay runs against a sandboxed copy of the agent whose tools are mocks, so a"
    " test moves, sends and leaks nothing real."
)


def _grade(whole: bool, some: bool) -> str:
    """Evidence when all of it is there, partial when some is, none otherwise."""
    if whole:
        return EVIDENCE
    return PARTIAL if some else NONE


def _control(ref: str, title: str, asks: str, status: str, evidence: list[str]) -> dict[str, Any]:
    return {"ref": ref, "title": title, "asks": asks, "status": status, "evidence": evidence}


def _monitoring(facts: _Facts) -> tuple[str, list[str]]:
    """Whether the agent is being watched while it is in use, for both frameworks."""
    e = facts.e
    lines = []
    if facts.live:
        lines.append(
            "The bounty is live: hunters may attack the agent at any time, and every attempt"
            " is replayed and recorded."
        )
    if e.watch:
        lines.append(
            "The current config is scanned again every day while the bounty is live, against"
            " attacks added since."
        )
    if e.house_outcome is not None and (e.house_attacks or e.house_outcome != "stopped"):
        ended = {
            "held": "none of its attacks got through",
            "broke": "one of its attacks got through",
            "stopped": "it could not finish",
        }.get(e.house_outcome, "it is still under way")
        lines.append(
            f"The platform's house hunter attacked the current config when it went live: {ended}."
        )
    if facts.attacked:
        lines.append(facts.attacks_line())
    if facts.live or (e.watch and facts.scanned):
        return EVIDENCE, lines
    if facts.attacked:
        return PARTIAL, [*lines, "The bounty is not live now, so nothing is attacking it."]
    return NONE, ["The bounty has not been live, and no daily scan is set."]


def _correction(facts: _Facts) -> tuple[str, list[str]]:
    """Whether what was found was put right, and the fix checked."""
    e = facts.e
    if not e.breaks and not facts.through:
        if not facts.tested:
            return NONE, ["Nothing has been tested, so nothing has been found to correct."]
        return NOT_APPLICABLE, ["No test has got through, so there has been nothing to correct."]
    lines = []
    if facts.closed:
        checked = (
            "and every paid attack was replayed against the patch without breaking it"
            if e.rearm == "passed"
            else "but the paid attacks have not yet been replayed against the patch"
        )
        lines.append(
            f"{_plural(len(facts.closed), 'verified break')} on an earlier config: the config"
            f" was changed, {checked}."
        )
    if facts.weak:
        return GAP, [*lines, facts.open_line()]
    return (EVIDENCE if e.rearm == "passed" else PARTIAL), lines


def _frameworks(evidence: Evidence) -> list[dict[str, Any]]:
    f = _Facts(evidence)
    tests = f.tests()
    untested = "Nothing has attacked the agent yet: no scan of this config, and no hunter."
    watched, watching = _monitoring(f)
    corrected, correction = _correction(f)

    # Tripwires name the risk, and a test shows it was looked for.
    identified = _grade(bool(f.wires) and f.tested, bool(f.wires))
    naming = (
        [f.wires_line(), "Each is set against the OWASP risk it guards (see Risk coverage)."]
        if f.wires
        else ["No tripwire is planted, so no risk has been named."]
    )
    # A test of the config that is in use, or only of ones before it.
    tested = _grade(f.scanned, f.attacked)
    stale = (
        []
        if f.scanned or not f.attacked
        else ["The current config has not itself been scanned: the attacks were on earlier ones."]
    )
    # Tested, and nothing that got through is still open.
    resilient = _grade(f.tested, False) if not f.weak else GAP
    weakness = [f.open_line()] if f.weak else []
    # A channel for outsiders: the bounty itself, while it is open to them.
    channel = _grade(f.live, f.listed)
    reporting = (
        [
            "The agent is listed as a bounty: an outside hunter reports a weakness by"
            " demonstrating it, and is paid when it is proven."
            if f.live
            else "The agent has been listed as a bounty, and is not open to hunters now.",
            *([f.attacks_line()] if f.attacked else []),
        ]
        if f.listed
        else ["The agent has not been listed, so no outsider has been able to report on it."]
    )

    eu = [
        _control(
            "Article 9(2)",
            "Risk management: foreseeable risks and misuse are identified and evaluated",
            "Known and reasonably foreseeable risks, including those from reasonably"
            " foreseeable misuse, are identified, estimated and evaluated, as a process"
            " that runs through the system's life.",
            identified,
            [*naming, *(tests if f.wires else [])],
        ),
        _control(
            "Article 9(6) and 9(8)",
            "Testing against metrics and thresholds defined in advance",
            "The system is tested to find the right risk measures, before it is put into"
            " service and at suitable points after, against metrics and probabilistic"
            " thresholds defined beforehand.",
            tested,
            [THRESHOLD, *(tests or [untested]), *stale],
        ),
        _control(
            "Article 15(5)",
            "Resilience against third parties who try to alter use, outputs or performance",
            "The system resists attempts by unauthorised third parties to change its use,"
            " outputs or performance by exploiting its vulnerabilities, including inputs"
            " crafted to make the model err and attacks on confidentiality.",
            resilient,
            [*(tests or [untested]), *weakness, *([SANDBOX] if f.tested else [])],
        ),
        _control(
            "Article 11 and Annex IV, point 2(g)",
            "Technical documentation: test procedures, metrics, dated logs and reports",
            "The technical documentation describes the validation and testing procedures"
            " and the metrics used, with test logs and test reports that are dated and"
            " signed by the responsible persons.",
            tested,
            [
                f"This report is dated and tied to {f.config}, with the procedure and the"
                " threshold stated.",
                *tests,
                "The transcript of every replay of each verified break is kept, encrypted,"
                " and is open to the sponsor.",
                "The report carries no signature of its own: the responsible person signs"
                " it (see Sign-off).",
                *stale,
            ]
            if f.tested
            else [untested],
        ),
        _control(
            "Article 72",
            "Post-market monitoring",
            "Once the system is in service, data on its performance is actively and"
            " systematically collected and analysed for as long as it is in use.",
            watched,
            watching,
        ),
    ]
    iso = [
        _control(
            "Clauses 6.1.2 and 8.2",
            "AI risk assessment",
            "AI risks are assessed by a defined, repeatable process, at planned intervals"
            " and when a significant change is made or proposed.",
            identified if f.scanned or not f.wires else PARTIAL,
            [
                *naming,
                "There is one scan for each config: a change to the prompt, the tools or"
                " the tripwires makes a new config, which has to be scanned afresh.",
                *(tests if f.wires else []),
                *stale,
            ],
        ),
        _control(
            "Clauses 8.3 and 10.2",
            "AI risk treatment, and corrective action",
            "A weakness that is found is corrected, the correction is checked to have"
            " worked, and both are recorded.",
            corrected,
            correction,
        ),
        _control(
            "Clause 9.1",
            "Monitoring, measurement, analysis and evaluation",
            "What is measured, by what method and when, is decided; the results are"
            " analysed and kept as documented information.",
            EVIDENCE if f.tested else NONE,
            [
                "Measured: known attacks held out of those run, attacks by hunters, and"
                " verified breaks.",
                THRESHOLD,
                *tests,
            ]
            if f.tested
            else [untested],
        ),
        _control(
            "Annex A, control A.6.2.4",
            "AI system verification and validation",
            "Measures to verify and validate the system are defined and documented, with"
            " the criteria for using them.",
            tested,
            [THRESHOLD, SANDBOX, *(tests or [untested]), *stale],
        ),
        _control(
            "Annex A, control A.6.2.6",
            "AI system operation and monitoring",
            "The system is monitored while it operates, for the things that matter to its"
            " continued safe working, and what is found is acted on.",
            watched,
            watching,
        ),
        _control(
            "Annex A, control A.8.3",
            "External reporting",
            "People outside the organisation have a way to report adverse effects of the system.",
            channel,
            reporting,
        ),
    ]
    return [
        {
            "id": "eu_ai_act",
            "name": "EU AI Act",
            "citation": REFERENCES[2],
            "scope": "These provisions bind providers of high-risk AI systems. Whether"
            " yours is one is a legal question this report does not answer.",
            "controls": eu,
        },
        {
            "id": "iso_42001",
            "name": "ISO/IEC 42001",
            "citation": REFERENCES[3],
            "scope": "A clause is a requirement of the management system. An Annex A"
            " control applies where your Statement of Applicability says it does.",
            "controls": iso,
        },
    ]


def _findings(evidence: Evidence) -> list[dict[str, Any]]:
    return [
        {
            "id": found.id,
            "tripwire": found.label,
            "vector": vector_of(found.slots),
            "risks": risks_of(found, evidence.tripwires),
            "config_hash": found.config_hash,
            "tripped_runs": found.tripped_runs,
            "verified_at": found.verified_at,
            "status": _settled(evidence, [found]),
        }
        for found in evidence.breaks
    ]


def digest(body: dict[str, Any]) -> str:
    """A fingerprint of a report's evidence: the same evidence, the same digest."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def report(evidence: Evidence, now: datetime) -> dict[str, Any]:
    """The mapping for one bounty, as `GET /v1/bounties/{id}/compliance` returns it."""
    e = evidence
    scanned = e.scan_state == "done"
    through = sum(1 for probe in e.probes if probe.tripped) if scanned else 0
    body: dict[str, Any] = {
        "subject": {
            "bounty_id": e.bounty_id,
            "title": e.title,
            "state": e.state,
            "model": e.model,
            "config_version": e.config_version,
            "config_hash": e.config_hash,
            "entry_points": sorted(e.entry_points),
            "tools": list(e.tools),
            "tripwires": [{"label": wire.label, "type": wire.type} for wire in e.tripwires],
        },
        "testing": {
            "runs_per_attack": RUNS_PER_ATTEMPT,
            "threshold": TRIP_THRESHOLD,
            "scan_state": e.scan_state,
            "scan_finished_at": e.scan_finished_at,
            "scan_with_library": e.scan_with_library,
            "probes_run": len(e.probes) if scanned else 0,
            "probes_held": len(e.probes) - through if scanned else 0,
            "attacks": e.attacks,
            "attacks_judged": e.attacks_judged,
            "house_attacks": e.house_attacks,
            "house_outcome": e.house_outcome,
            "verified_breaks": len(e.breaks),
            "watch": e.watch,
            "rearm": e.rearm,
        },
        "risks": _risk_rows(e),
        "frameworks": _frameworks(e),
        "findings": _findings(e),
    }
    return {
        **body,
        "generated_at": now,
        "references": REFERENCES,
        "disclaimer": DISCLAIMER,
        "evidence_digest": digest(body),
    }
