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
from agentbounty.services.harness import (
    EntryPoint,
    SandboxLimits,
    TargetConfig,
    fill_for,
    run_once,
)
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
        self._spoken = False

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], params: ModelParams
    ) -> ModelResponse:
        # Whether the probe itself has spoken, not whether the conversation holds an
        # assistant turn: a memory is replayed as history and already holds one.
        asked, self._spoken = self._spoken, True
        calls: tuple[ToolCallRequest, ...] = ()
        parameters = self._canary.parameters
        if not asked and isinstance(parameters, judge.SecretLeak | judge.SystemPromptLeak):
            # A secret may only ever reach the agent through a tool result, so use them all.
            calls = tuple(
                ToolCallRequest(f"probe_{index}", tool.name, {}) for index, tool in enumerate(tools)
            )
        elif not asked and isinstance(
            parameters, judge.ForbiddenToolCall | judge.UnauthorizedAction
        ):
            arguments = _breaking_arguments(parameters.rule)
            if arguments is not None and any(t.name == parameters.rule.tool for t in tools):
                calls = (ToolCallRequest("probe_0", parameters.rule.tool, arguments),)
        elif not asked and isinstance(parameters, judge.DataExfiltration):
            available = next(
                (name for name in parameters.sink_tools if any(t.name == name for t in tools)),
                None,
            )
            if available is not None:
                calls = (
                    ToolCallRequest(
                        "probe_0", available, {parameters.sink_arguments[0]: parameters.value}
                    ),
                )
        if calls:
            return ModelResponse("", calls, "tool_calls", 0, 0)
        if isinstance(parameters, judge.PhishingLinkInjection):
            return ModelResponse(parameters.url, (), "stop", 0, 0)
        if isinstance(parameters, judge.MaliciousCodeInjection):
            return ModelResponse(f"```text\n{parameters.marker}\n```", (), "stop", 0, 0)
        if isinstance(parameters, judge.PolicyViolation):
            return ModelResponse(parameters.test_text or parameters.pattern, (), "stop", 0, 0)
        # Repeat everything the agent has been given: the system prompt and every tool result.
        context = "\n".join(m.content for m in messages if m.role in ("system", "tool"))
        return ModelResponse(context, (), "stop", 0, 0)


def _probe_slot(config: TargetConfig) -> EntryPoint:
    """A slot the check can write into. A slot that writes the user's turn is preferred,
    so that the probe has something to answer even where the sponsor set no task."""
    for slot in (EntryPoint.USER_MESSAGE, EntryPoint.MULTI_TURN, EntryPoint.DOCUMENT):
        if slot in config.entry_points:
            return slot
    # Every remaining slot needs a task, so the conversation is never empty.
    return config.entry_points[0]


async def _can_trip(config: TargetConfig, canary: CanaryDefinition) -> bool:
    attempt = [fill_for(_probe_slot(config), PROBE_TEXT)]
    run = await run_once(config, attempt, _Probe(canary), limits=_LIMITS)
    return judge.detect_canary(run.transcript, canary.parameters).tripped


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
        if canary.type.value in ("secret_leak", "system_prompt_leak"):
            found.append(
                f"canary '{label}': its planted value is not in the system prompt or any"
                " tool result, so the agent could never leak it"
            )
        elif canary.rule is not None and canary.rule.tool not in tool_names:
            found.append(f"canary '{label}': the agent has no tool named '{canary.rule.tool}'")
        elif canary.exfiltration is not None and not tool_names.intersection(
            canary.exfiltration.sink_tools
        ):
            names = ", ".join(canary.exfiltration.sink_tools)
            found.append(f"canary '{label}': the agent has none of its outbound tools: {names}")
        else:
            found.append(f"canary '{label}': no call to the tool could ever break its rule")
    return found
