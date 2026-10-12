"""Replay harness: runs an attack against a sandboxed copy of an agent.

The sandbox is this module plus two objects it is handed:

    ModelClient        the only thing that can reach the network
    MockToolExecutor   answers every tool call from a table of canned results

No real tool ever runs. This module imports no networking, process or file
library (a test checks that), and the executor holds nothing but the table,
so the model API is the only way out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from agentbounty.services.judge import RUNS_PER_ATTEMPT, Message, ToolCall
from agentbounty.services.model import (
    ChatMessage,
    ModelClient,
    ModelParams,
    ToolSpec,
)

MAX_SLOT_CHARACTERS = 20_000
DEFAULT_MAX_TOKENS_PER_TURN = 1024

# Sampling options a sponsor may set. Anything else in a config's params is refused,
# so a config cannot smuggle arbitrary fields into requests made with our API key.
ALLOWED_PARAMS = frozenset({"max_tokens", "temperature", "top_p", "stop", "seed"})


class EntryPoint(StrEnum):
    """A place in the conversation a hunter may put text."""

    USER_MESSAGE = "user_message"
    DOCUMENT = "document"


class CapReason(StrEnum):
    MAX_TURNS = "max_turns"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    TIMEOUT = "timeout"


class AttemptError(ValueError):
    """The attempt does not fit the config's declared entry points or size limits."""


@dataclass(frozen=True)
class SandboxLimits:
    # Model calls per run.
    max_turns: int = 8
    # Output tokens per run, across all turns.
    max_output_tokens: int = 4096
    # Wall-clock seconds per run.
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.max_output_tokens < 1 or self.timeout_seconds <= 0:
            raise ValueError("sandbox limits must be positive")


DEFAULT_LIMITS = SandboxLimits()


@dataclass(frozen=True)
class MockTool:
    """A tool the agent believes it has. Calling it only ever returns `result`."""

    name: str
    description: str
    parameters: Mapping[str, object]
    result: str = "ok"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


@dataclass(frozen=True)
class TargetConfig:
    system_prompt: str
    model: str
    tools: tuple[MockTool, ...] = ()
    params: Mapping[str, object] = field(default_factory=dict)
    entry_points: tuple[EntryPoint, ...] = (EntryPoint.USER_MESSAGE,)
    # The base URL of the sponsor's own model endpoint. None: the platform's provider.
    endpoint: str | None = None

    def __post_init__(self) -> None:
        if not self.system_prompt or not self.model:
            raise ValueError("a config needs a system prompt and a model")
        if not self.entry_points or len(set(self.entry_points)) != len(self.entry_points):
            raise ValueError("a config needs at least one entry point, each listed once")
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("tool names must be unique")
        unknown = set(self.params) - ALLOWED_PARAMS
        if unknown:
            raise ValueError(f"unsupported model params: {sorted(unknown)}")
        max_tokens = self.params.get("max_tokens", DEFAULT_MAX_TOKENS_PER_TURN)
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")


@dataclass(frozen=True)
class SlotFill:
    slot: EntryPoint
    content: str


Attempt = Sequence[SlotFill]


def validate_attempt(config: TargetConfig, attempt: Attempt) -> None:
    """Raise AttemptError unless the attempt fills only declared slots, each at most once."""
    validate_fills(config.entry_points, attempt)


def validate_fills(entry_points: Sequence[EntryPoint], attempt: Attempt) -> None:
    """validate_attempt, for a caller that knows the entry points but not the whole config."""
    if not attempt:
        raise AttemptError("an attempt needs at least one fill")
    seen: set[EntryPoint] = set()
    for fill in attempt:
        if fill.slot not in entry_points:
            raise AttemptError(f"'{fill.slot}' is not an entry point of this config")
        if fill.slot in seen:
            raise AttemptError(f"'{fill.slot}' is filled more than once")
        seen.add(fill.slot)
        if not fill.content:
            raise AttemptError(f"the fill for '{fill.slot}' is empty")
        if len(fill.content) > MAX_SLOT_CHARACTERS:
            raise AttemptError(
                f"the fill for '{fill.slot}' exceeds {MAX_SLOT_CHARACTERS} characters"
            )


def build_conversation(config: TargetConfig, attempt: Attempt) -> list[ChatMessage]:
    """A fresh conversation: the sponsor's system prompt, then one user turn.

    Hunter text goes into that user turn and nowhere else. A document comes
    first, in <document> tags, followed by the user message if there is one.
    """
    validate_attempt(config, attempt)
    fills = {fill.slot: fill.content for fill in attempt}
    parts = []
    if EntryPoint.DOCUMENT in fills:
        parts.append(f"<document>\n{fills[EntryPoint.DOCUMENT]}\n</document>")
    if EntryPoint.USER_MESSAGE in fills:
        parts.append(fills[EntryPoint.USER_MESSAGE])
    return [
        ChatMessage("system", config.system_prompt),
        ChatMessage("user", "\n\n".join(parts)),
    ]


@dataclass(frozen=True)
class RecordedToolCall:
    name: str
    arguments: Mapping[str, object]
    result: str


class MockToolExecutor:
    """Answers tool calls from a table and records them. It can do nothing else.

    It is synchronous and holds no client, socket or callable, only the canned
    results, so there is nothing through which a call could have an effect.
    """

    def __init__(self, tools: Sequence[MockTool]) -> None:
        self._results = {tool.name: tool.result for tool in tools}
        self._calls: list[RecordedToolCall] = []

    def execute(self, name: str, arguments: Mapping[str, object]) -> str:
        result = self._results.get(name, f"Error: unknown tool '{name}'")
        self._calls.append(RecordedToolCall(name, dict(arguments), result))
        return result

    @property
    def calls(self) -> tuple[RecordedToolCall, ...]:
        return tuple(self._calls)


@dataclass(frozen=True)
class RunResult:
    run_index: int
    # The user turn, then every assistant and tool message. The system prompt is left out:
    # transcripts are stored and shown, and the prompt is a secret.
    transcript: tuple[Message, ...]
    tool_calls: tuple[RecordedToolCall, ...]
    input_tokens: int
    output_tokens: int
    # Set when the run was ended by a limit instead of by the agent finishing.
    capped: CapReason | None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class _Run:
    """The state of one run, kept outside the loop so a timeout does not lose it."""

    transcript: list[Message]
    executor: MockToolExecutor
    input_tokens: int = 0
    output_tokens: int = 0
    capped: CapReason | None = None


async def _agent_loop(
    run: _Run,
    messages: list[ChatMessage],
    config: TargetConfig,
    model: ModelClient,
    limits: SandboxLimits,
) -> None:
    tools = [tool.spec for tool in config.tools]
    options = {key: value for key, value in config.params.items() if key != "max_tokens"}
    per_turn = config.params.get("max_tokens", DEFAULT_MAX_TOKENS_PER_TURN)
    assert type(per_turn) is int  # noqa: S101 - checked when the config was built

    for _ in range(limits.max_turns):
        remaining = limits.max_output_tokens - run.output_tokens
        if remaining <= 0:
            run.capped = CapReason.MAX_OUTPUT_TOKENS
            return
        params = ModelParams(config.model, min(per_turn, remaining), options)
        response = await model.complete(messages, tools, params)
        run.input_tokens += response.input_tokens
        run.output_tokens += response.output_tokens
        messages.append(ChatMessage("assistant", response.text, response.tool_calls))
        run.transcript.append(
            Message(
                "assistant",
                response.text,
                tuple(ToolCall(call.name, call.arguments) for call in response.tool_calls),
            )
        )
        if not response.tool_calls:
            # A reply cut off by the run's token budget is a capped run; one cut off by the
            # sponsor's own per-turn setting is just how that agent behaves.
            if response.finish_reason == "length" and params.max_output_tokens < per_turn:
                run.capped = CapReason.MAX_OUTPUT_TOKENS
            return
        for call in response.tool_calls:
            result = run.executor.execute(call.name, call.arguments)
            messages.append(ChatMessage("tool", result, tool_call_id=call.id))
            run.transcript.append(Message("tool", result))
    run.capped = CapReason.MAX_TURNS


async def run_once(
    config: TargetConfig,
    attempt: Attempt,
    model: ModelClient,
    *,
    limits: SandboxLimits = DEFAULT_LIMITS,
    run_index: int = 0,
) -> RunResult:
    """One replay, from an empty history, with its own tool executor.

    A run that hits a limit ends cleanly and is marked capped; what the agent
    did before that is kept. An error from the model is not caught: a run that
    failed is not evidence of anything, so the caller decides whether to retry.
    """
    messages = build_conversation(config, attempt)
    run = _Run(
        transcript=[Message("user", messages[-1].content)], executor=MockToolExecutor(config.tools)
    )
    try:
        async with asyncio.timeout(limits.timeout_seconds) as deadline:
            await _agent_loop(run, messages, config, model, limits)
    except TimeoutError:
        # Only our own deadline is a cap. A timeout raised inside the model client is an error.
        if not deadline.expired():
            raise
        run.capped = CapReason.TIMEOUT
    return RunResult(
        run_index=run_index,
        transcript=tuple(run.transcript),
        tool_calls=run.executor.calls,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        capped=run.capped,
    )


async def run_attempt(
    config: TargetConfig,
    attempt: Attempt,
    model: ModelClient,
    *,
    runs: int = RUNS_PER_ATTEMPT,
    limits: SandboxLimits = DEFAULT_LIMITS,
    on_run: Callable[[int], Awaitable[None]] | None = None,
) -> tuple[RunResult, ...]:
    """Replay the attempt `runs` times, each from scratch, one after another.

    `on_run` is told how many runs have finished, after each one.
    """
    if runs < 1:
        raise ValueError("runs must be at least 1")
    validate_attempt(config, attempt)
    results = []
    for index in range(runs):
        results.append(await run_once(config, attempt, model, limits=limits, run_index=index))
        if on_run is not None:
            await on_run(index + 1)
    return tuple(results)
