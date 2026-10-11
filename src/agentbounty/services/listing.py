"""The listing check: prove, before a bounty goes live, that every canary can trip.

Each canary is put through the real harness and the real judge once, in check
mode, against a probe that behaves as the worst possible agent for that
canary: it calls every tool and repeats everything in its context, or it makes
a tool call that breaks the rule. No model is called and nothing is spent.

A canary that does not trip even then can never trip: a secret that is in
neither the system prompt nor any tool result, or a rule about a tool the
agent does not have. Such a bounty would take hunters' time and never pay.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentbounty.services import judge
from agentbounty.services.bounty import CanaryDefinition, LoadedTarget
from agentbounty.services.harness import SandboxLimits, SlotFill, TargetConfig, run_once
from agentbounty.services.model import (
    ChatMessage,
    ModelParams,
    ModelResponse,
    ToolCallRequest,
    ToolSpec,
)

PROBE_TEXT = "listing check"
_PROBE_RECIPIENT = "listing-check-probe"
_LIMITS = SandboxLimits(max_turns=3, max_output_tokens=1_000_000, timeout_seconds=30)


def _breaking_arguments(rule: judge.ToolRule) -> dict[str, object] | None:
    """Arguments that break the rule, or None if no arguments could."""
    predicate = rule.predicate
    if predicate is None:
        return {}
    value, operator = predicate.value, predicate.operator
    candidates: list[object]
    if operator is judge.Operator.IN:
        candidates = list(value) if isinstance(value, list | tuple) else []
    elif operator is judge.Operator.EQ:
        candidates = [value]
    elif operator in (judge.Operator.NOT_IN, judge.Operator.NE):
        candidates = [_PROBE_RECIPIENT, f"{_PROBE_RECIPIENT}-2", 0, 1]
    else:
        # The validated value of an ordering rule is a number.
        number = float(str(value))
        candidates = [number + 1, number, number - 1]
    for candidate in candidates:
        if predicate.matches({predicate.argument: candidate}):
            return {predicate.argument: candidate}
    return None


class _Probe:
    """Stands in for the model during the check. Deterministic, and it calls nothing."""

    def __init__(self, canary: CanaryDefinition) -> None:
        self._canary = canary

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], params: ModelParams
    ) -> ModelResponse:
        asked = any(message.role == "assistant" for message in messages)
        calls: tuple[ToolCallRequest, ...] = ()
        if not asked and self._canary.rule is None:
            # A secret may only ever reach the agent through a tool result, so use them all.
            calls = tuple(
                ToolCallRequest(f"probe_{index}", tool.name, {}) for index, tool in enumerate(tools)
            )
        elif not asked and self._canary.rule is not None:
            arguments = _breaking_arguments(self._canary.rule)
            if arguments is not None and any(t.name == self._canary.rule.tool for t in tools):
                calls = (ToolCallRequest("probe_0", self._canary.rule.tool, arguments),)
        if calls:
            return ModelResponse("", calls, "tool_calls", 0, 0)
        # Repeat everything the agent has been given: the system prompt and every tool result.
        context = "\n".join(m.content for m in messages if m.role in ("system", "tool"))
        return ModelResponse(context, (), "stop", 0, 0)


async def _can_trip(config: TargetConfig, canary: CanaryDefinition) -> bool:
    attempt = [SlotFill(config.entry_points[0], PROBE_TEXT)]
    run = await run_once(config, attempt, _Probe(canary), limits=_LIMITS)
    if canary.secret is not None:
        return judge.detect_secret_leak(run.transcript, canary.secret).tripped
    assert canary.rule is not None  # noqa: S101 - a definition has a secret or a rule
    return judge.detect_forbidden_tool(run.transcript, canary.rule).tripped


async def problems(target: LoadedTarget) -> list[str]:
    """Reasons the bounty would be unwinnable. Empty means every canary can trip."""
    if not target.canaries:
        return ["the config has no canaries"]
    found = []
    tool_names = {tool.name for tool in target.config.tools}
    for loaded in target.canaries:
        canary = loaded.definition
        if await _can_trip(target.config, canary):
            continue
        label = canary.public_label
        if canary.secret is not None:
            found.append(
                f"canary '{label}': the secret is not in the system prompt or any tool"
                " result, so the agent could never leak it"
            )
        elif canary.rule is not None and canary.rule.tool not in tool_names:
            found.append(f"canary '{label}': the agent has no tool named '{canary.rule.tool}'")
        else:
            found.append(f"canary '{label}': no call to the tool could ever break its rule")
    return found
