"""Replay harness: runs an attack against a sandboxed copy of an agent.

The sandbox is this module plus two objects it is handed:

    ModelClient        the only thing that can reach the network
    MockToolExecutor   answers every tool call from a table of canned results

No real tool ever runs. This module imports no networking, process or file
library (a test checks that), and the executor holds nothing but the table,
so the model API is the only way out.

An attack is a set of fills, one per entry point the sponsor put in scope. The
sponsor owns the frame around each fill -- the document's name, the tool that
carries the planted text, the URL of the page, the heading on a memory note --
and the hunter owns only the text inside it. `Scaffold` is that frame.
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
    # What a tool the agent reads from hands back: a web page it fetches, a file it opens.
    # The hunter never speaks to the agent; the agent finds the text while doing its task.
    TOOL_RESULT = "tool_result"
    # The body of a page the agent fetches, from a URL the sponsor names. The same
    # mechanism as TOOL_RESULT, framed as an HTTP response from that one URL.
    WEB_PAGE = "web_page"
    # A note the agent carries in from an earlier session, replayed as prior history
    # before the live user says anything.
    MEMORY = "memory"
    # A script of user turns, fed one at a time as the agent finishes each, so an attack
    # can build over a conversation instead of arriving all at once.
    MULTI_TURN = "multi_turn"


# How many user turns a multi-turn script holds unless the sponsor says otherwise, and the
# most any sponsor may allow. A run's model-call budget (SandboxLimits.max_turns) still
# applies across the whole script.
DEFAULT_SCRIPTED_TURNS = 3
MAX_SCRIPTED_TURNS = 8
MAX_LABEL_CHARACTERS = 300

# The heading a memory note is given when the sponsor does not name one.
DEFAULT_MEMORY_LABEL = "Notes carried over from an earlier session"
# The agent's reply to the memory it was handed. The harness writes it, not the agent, so
# it is kept out of the transcript: the judge must only ever see what the agent emitted.
MEMORY_ACK = "Noted."


class CapReason(StrEnum):
    MAX_TURNS = "max_turns"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    TIMEOUT = "timeout"


class AttemptError(ValueError):
    """The attempt does not fit the config's declared entry points or size limits."""


@dataclass(frozen=True)
class SandboxLimits:
    # Model calls per run, across every scripted user turn.
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
    # It reads from somewhere a stranger can write: the web, an inbox, a shared file. A
    # hunter's text can only ever be carried by a tool marked this way.
    untrusted: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


@dataclass(frozen=True)
class Scaffold:
    """The trusted frame around one entry point. The sponsor writes all of it.

    `label` is what the agent is told it is looking at: a filename for a document, the
    URL for a web page, the heading on a memory note. `tool` names the tool whose
    return value carries the hunter's text, for `tool_result` and `web_page`. `turns`
    is how many user turns a `multi_turn` script may hold.

    Fields that do not apply to a slot are ignored, so one shape covers every slot.
    """

    label: str = ""
    tool: str | None = None
    turns: int = DEFAULT_SCRIPTED_TURNS

    def __post_init__(self) -> None:
        if len(self.label) > MAX_LABEL_CHARACTERS:
            raise ValueError(f"a scaffold label is at most {MAX_LABEL_CHARACTERS} characters")
        if self.tool is not None and not self.tool:
            raise ValueError("a scaffold that names a tool needs its name")
        if not 1 <= self.turns <= MAX_SCRIPTED_TURNS:
            raise ValueError(f"a scripted attack is 1 to {MAX_SCRIPTED_TURNS} turns")


EMPTY_SCAFFOLD = Scaffold()
# Slots whose fill is carried by a tool, rather than written into the conversation.
CARRIED = (EntryPoint.TOOL_RESULT, EntryPoint.WEB_PAGE)
# Slots that write the live user's turn.
SPEAKS = (EntryPoint.USER_MESSAGE, EntryPoint.MULTI_TURN)


def scaffold_to_json(scaffold: Mapping[EntryPoint, Scaffold]) -> dict[str, dict[str, object]]:
    """The scaffold as stored and hashed: only the fields a sponsor actually set.

    A field left at its default is left out, so a config that declares no scaffold
    keeps the hash it always had.
    """
    stored: dict[str, dict[str, object]] = {}
    for slot, frame in scaffold.items():
        fields: dict[str, object] = {}
        if frame.label:
            fields["label"] = frame.label
        if frame.tool is not None:
            fields["tool"] = frame.tool
        if frame.turns != DEFAULT_SCRIPTED_TURNS:
            fields["turns"] = frame.turns
        if fields:
            stored[str(slot)] = fields
    return stored


def scaffold_from_json(
    stored: Mapping[str, Mapping[str, object]] | None,
) -> dict[EntryPoint, Scaffold]:
    """Read back what scaffold_to_json wrote, ignoring slots this version does not know."""
    known = {point.value for point in EntryPoint}
    frames: dict[EntryPoint, Scaffold] = {}
    for slot, fields in (stored or {}).items():
        if slot not in known:
            continue
        label, tool, turns = fields.get("label"), fields.get("tool"), fields.get("turns")
        frames[EntryPoint(slot)] = Scaffold(
            label=label if isinstance(label, str) else "",
            tool=tool if isinstance(tool, str) else None,
            turns=turns if type(turns) is int else DEFAULT_SCRIPTED_TURNS,
        )
    return frames


def fetched_page(url: str, body: str) -> str:
    """What the fetch tool hands back: the sponsor's URL, and the hunter's page under it."""
    return f"GET {url}\n200 OK\ncontent-type: text/html\n\n{body}"


@dataclass(frozen=True)
class TargetConfig:
    system_prompt: str
    model: str
    tools: tuple[MockTool, ...] = ()
    params: Mapping[str, object] = field(default_factory=dict)
    entry_points: tuple[EntryPoint, ...] = (EntryPoint.USER_MESSAGE,)
    # The base URL of the sponsor's own model endpoint. None: the platform's provider.
    endpoint: str | None = None
    # What the agent is asked to do when the hunter does not write the user's message: the
    # job during which it comes across what the hunter planted.
    task: str = ""
    # The sponsor's frame around each entry point, for the slots that have one.
    scaffold: Mapping[EntryPoint, Scaffold] = field(default_factory=dict)

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
        self._check_scaffold()

    def frame(self, slot: EntryPoint) -> Scaffold:
        """The sponsor's frame for a slot, empty where they declared none."""
        return self.scaffold.get(slot, EMPTY_SCAFFOLD)

    def carrier(self, slot: EntryPoint) -> tuple[str, ...]:
        """The tools that hand back this slot's fill.

        A slot that names its tool has exactly that one. `tool_result` without a named
        tool is carried by every tool marked untrusted, which is how it has always
        worked and what a config written before scaffolds still means.
        """
        named = self.frame(slot).tool
        if named is not None:
            return (named,)
        if slot is EntryPoint.TOOL_RESULT:
            return tuple(tool.name for tool in self.tools if tool.untrusted)
        return ()

    def _check_scaffold(self) -> None:
        in_scope = set(self.entry_points)
        stray = set(self.scaffold) - in_scope
        if stray:
            raise ValueError(f"scaffold for slots that are not in scope: {sorted(stray)}")
        if in_scope.issuperset(SPEAKS):
            raise ValueError(
                "user_message and multi_turn both write the user's turns: declare one of them"
            )
        tools = {tool.name: tool for tool in self.tools}
        for slot in CARRIED:
            if slot not in in_scope:
                continue
            # The hunter never speaks here, so the agent needs a job that takes it past
            # the planted text.
            if not self.task:
                raise ValueError(
                    f"the {slot} entry point needs a task: what the agent is asked to do"
                )
            named = self.frame(slot).tool
            if named is None:
                if slot is EntryPoint.WEB_PAGE:
                    raise ValueError("the web_page entry point needs the tool that fetches it")
                if not any(tool.untrusted for tool in self.tools):
                    raise ValueError(
                        "the tool-result entry point needs a tool marked as reading"
                        " untrusted content"
                    )
            elif named not in tools:
                raise ValueError(f"the {slot} entry point names a tool the agent does not have")
            elif not tools[named].untrusted:
                raise ValueError(
                    f"'{named}' carries the {slot} entry point, so it must be marked as"
                    " reading untrusted content"
                )
        if in_scope.issuperset(CARRIED):
            carriers = {slot: self.carrier(slot) for slot in CARRIED}
            if self.frame(EntryPoint.TOOL_RESULT).tool is None:
                raise ValueError(
                    "with both tool_result and web_page in scope, tool_result must name the"
                    " tool that carries it"
                )
            if set(carriers[EntryPoint.TOOL_RESULT]) & set(carriers[EntryPoint.WEB_PAGE]):
                raise ValueError("tool_result and web_page cannot be carried by the same tool")
        if EntryPoint.WEB_PAGE in in_scope:
            url = self.frame(EntryPoint.WEB_PAGE).label
            if not url:
                raise ValueError("the web_page entry point needs, as its label, the URL to fetch")
            if url not in self.task and url not in self.system_prompt:
                raise ValueError(
                    "name the web_page URL in the task or the system prompt, so the agent has"
                    " a reason to fetch it"
                )
        if EntryPoint.MEMORY in in_scope and not (
            self.task or in_scope & {*SPEAKS, EntryPoint.DOCUMENT}
        ):
            raise ValueError(
                "the memory entry point needs a task, or a slot that writes the user's turn:"
                " a memory on its own gives the agent nothing to do"
            )


@dataclass(frozen=True)
class SlotFill:
    """One slot of an attack, as the hunter wrote it.

    Every slot carries `content`, except `multi_turn`, which carries `turns`: the user
    turns to send, in order.
    """

    slot: EntryPoint
    content: str = ""
    turns: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Everything the hunter wrote here, for measuring its size and its cost."""
        return self.content or "\n\n".join(self.turns)


Attempt = Sequence[SlotFill]


def fill_for(slot: EntryPoint, text: str) -> SlotFill:
    """A fill carrying `text` in whichever field the slot takes."""
    if slot is EntryPoint.MULTI_TURN:
        return SlotFill(slot, turns=(text,))
    return SlotFill(slot, text)


StoredFill = Mapping[str, object]


def fills_to_json(fills: Sequence[SlotFill]) -> list[dict[str, str | list[str]]]:
    """An attack as it is stored and shown: one object per slot, with every field.

    `turns` is empty for every slot but multi_turn, and `content` is empty for that
    one. The shape does not vary, so an attack stored, mailed, scanned or read back
    through the API is always the same object.
    """
    return [
        {"slot": fill.slot.value, "content": fill.content, "turns": list(fill.turns)}
        for fill in fills
    ]


def fills_from_json(stored: Sequence[StoredFill]) -> list[SlotFill]:
    """Read back what fills_to_json wrote, dropping slots this version does not know."""
    known = {point.value for point in EntryPoint}
    fills = []
    for item in stored:
        slot, content, turns = item.get("slot"), item.get("content"), item.get("turns")
        if not isinstance(slot, str) or slot not in known:
            continue
        fills.append(
            SlotFill(
                EntryPoint(slot),
                content if isinstance(content, str) else "",
                tuple(turn for turn in turns if isinstance(turn, str))
                if isinstance(turns, list)
                else (),
            )
        )
    return fills


def validate_attempt(config: TargetConfig, attempt: Attempt) -> None:
    """Raise AttemptError unless the attempt fills only declared slots, each at most once."""
    validate_fills(config.entry_points, attempt, config.scaffold, task=config.task)


def validate_fills(
    entry_points: Sequence[EntryPoint],
    attempt: Attempt,
    scaffold: Mapping[EntryPoint, Scaffold] | None = None,
    *,
    task: str = "",
) -> None:
    """validate_attempt, for a caller that knows the entry points but not the whole config."""
    if not attempt:
        raise AttemptError("an attempt needs at least one fill")
    frames = scaffold or {}
    seen: set[EntryPoint] = set()
    for fill in attempt:
        if fill.slot not in entry_points:
            raise AttemptError(f"'{fill.slot}' is not an entry point of this config")
        if fill.slot in seen:
            raise AttemptError(f"'{fill.slot}' is filled more than once")
        seen.add(fill.slot)
        if fill.slot is EntryPoint.MULTI_TURN:
            allowed = frames.get(fill.slot, EMPTY_SCAFFOLD).turns
            if fill.content:
                raise AttemptError("the fill for 'multi_turn' carries turns, not content")
            if not fill.turns:
                raise AttemptError("the fill for 'multi_turn' is empty")
            if len(fill.turns) > allowed:
                raise AttemptError(
                    f"the fill for 'multi_turn' has {len(fill.turns)} turns;"
                    f" this agent takes at most {allowed}"
                )
            if any(not turn for turn in fill.turns):
                raise AttemptError("a turn of the fill for 'multi_turn' is empty")
        else:
            if fill.turns:
                raise AttemptError(f"the fill for '{fill.slot}' carries content, not turns")
            if not fill.content:
                raise AttemptError(f"the fill for '{fill.slot}' is empty")
        if len(fill.text) > MAX_SLOT_CHARACTERS:
            raise AttemptError(
                f"the fill for '{fill.slot}' exceeds {MAX_SLOT_CHARACTERS} characters"
            )
    # Somebody has to say something. An attempt that only plants text in a memory or a
    # tool result needs a live user turn to come from somewhere: a slot it fills, or the
    # task the sponsor set.
    if not task and not seen & {*SPEAKS, EntryPoint.DOCUMENT}:
        raise AttemptError(
            "this attempt leaves the agent nothing to act on: fill a slot that writes the"
            " user's turn"
        )


@dataclass(frozen=True)
class Plan:
    """One run laid out: the messages it opens with, the user turns still to come, and
    what each carrying tool hands back in place of its canned result."""

    messages: tuple[ChatMessage, ...]
    pending_turns: tuple[str, ...]
    planted: Mapping[str, str]


def build_plan(config: TargetConfig, attempt: Attempt) -> Plan:
    """Lay out one run of the attempt against a fresh copy of the agent.

    Hunter text reaches the agent in four ways and no others: the user's turn (one
    message, or the first of a script), a document inside that turn, a note replayed
    as prior history, and what a tool marked untrusted hands back. A document comes
    first, in <document> tags; where the hunter writes no message, the user asks for
    the sponsor's task.
    """
    validate_attempt(config, attempt)
    fills = {fill.slot: fill for fill in attempt}

    messages = [ChatMessage("system", config.system_prompt)]
    if EntryPoint.MEMORY in fills:
        label = config.frame(EntryPoint.MEMORY).label or DEFAULT_MEMORY_LABEL
        note = fills[EntryPoint.MEMORY].content
        messages.append(ChatMessage("user", f"{label}:\n<memory>\n{note}\n</memory>"))
        # So that the memory is history and not part of what the live user says.
        messages.append(ChatMessage("assistant", MEMORY_ACK))

    parts = []
    if EntryPoint.DOCUMENT in fills:
        name = config.frame(EntryPoint.DOCUMENT).label
        opening = f"<document name={name!r}>" if name else "<document>"
        parts.append(f"{opening}\n{fills[EntryPoint.DOCUMENT].content}\n</document>")
    pending: tuple[str, ...] = ()
    if EntryPoint.USER_MESSAGE in fills:
        parts.append(fills[EntryPoint.USER_MESSAGE].content)
    elif EntryPoint.MULTI_TURN in fills:
        scripted = fills[EntryPoint.MULTI_TURN].turns
        parts.append(scripted[0])
        pending = scripted[1:]
    elif config.task:
        parts.append(config.task)
    messages.append(ChatMessage("user", "\n\n".join(parts)))

    planted: dict[str, str] = {}
    if EntryPoint.TOOL_RESULT in fills:
        for name in config.carrier(EntryPoint.TOOL_RESULT):
            planted[name] = fills[EntryPoint.TOOL_RESULT].content
    if EntryPoint.WEB_PAGE in fills:
        frame = config.frame(EntryPoint.WEB_PAGE)
        page = fetched_page(frame.label, fills[EntryPoint.WEB_PAGE].content)
        for name in config.carrier(EntryPoint.WEB_PAGE):
            planted[name] = page
    return Plan(tuple(messages), pending, planted)


def build_conversation(config: TargetConfig, attempt: Attempt) -> list[ChatMessage]:
    """The messages a run opens with. Later scripted turns are fed in by the agent loop."""
    return list(build_plan(config, attempt).messages)


def opening_transcript(messages: Sequence[ChatMessage]) -> list[Message]:
    """What a run's transcript starts with.

    Everything the agent was given, less the two things the harness wrote itself: the
    system prompt, which is the sponsor's secret, and the acknowledgement of a memory,
    which the agent did not say and which the judge must therefore never see.
    """
    return [
        Message(message.role, message.content)
        for message in messages
        if message.role != "system"
        and not (message.role == "assistant" and message.content == MEMORY_ACK)
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
    `planted` maps a tool name to what it returns in place of its canned result:
    the hunter's text for the tool that carries their fill (see build_plan).
    """

    def __init__(self, tools: Sequence[MockTool], planted: Mapping[str, str] | None = None) -> None:
        carried = planted or {}
        self._results = {tool.name: carried.get(tool.name, tool.result) for tool in tools}
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
    # Every user, assistant and tool message of the run. The system prompt is left out:
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
    # Scripted user turns not yet sent, in order.
    pending: list[str] = field(default_factory=list)
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
            if not run.pending:
                return
            # The agent has finished its turn, so the next scripted turn goes in. The
            # model-call budget is shared across the whole script.
            spoken = run.pending.pop(0)
            messages.append(ChatMessage("user", spoken))
            run.transcript.append(Message("user", spoken))
            continue
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
    plan = build_plan(config, attempt)
    messages = list(plan.messages)
    run = _Run(
        transcript=opening_transcript(plan.messages),
        executor=MockToolExecutor(config.tools, plan.planted),
        pending=list(plan.pending_turns),
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
