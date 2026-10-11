"""The model, behind one interface.

ModelClient is the only thing in a replay that can reach the network.
FakeModel is deterministic and needs no key; RealModel calls a chat
completions API in the OpenAI function-calling format, which Anthropic's
OpenAI-compatible endpoint also accepts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "tool_calls", "length"]


@dataclass(frozen=True)
class ToolCallRequest:
    """A tool call the model asked for."""

    id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str = ""
    # Assistant messages only.
    tool_calls: tuple[ToolCallRequest, ...] = ()
    # Tool messages only: the call this is the result of.
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """A tool as the model sees it: a name, a description and a JSON Schema for its arguments."""

    name: str
    description: str
    parameters: Mapping[str, object]


@dataclass(frozen=True)
class ModelParams:
    model: str
    max_output_tokens: int
    # Sampling options passed to the API as-is, such as temperature.
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: tuple[ToolCallRequest, ...]
    finish_reason: FinishReason
    input_tokens: int
    output_tokens: int


class ModelError(RuntimeError):
    """The model API failed or answered with something unusable."""


class ModelClient(Protocol):
    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], params: ModelParams
    ) -> ModelResponse: ...


# --- fake -------------------------------------------------------------------------------

REVEAL_DIRECTIVE = "[[reveal]]"
_CALL_DIRECTIVE = re.compile(r"\[\[call\s+(?P<name>[\w.-]+)\s*")
_CLOSE_DIRECTIVE = re.compile(r"\s*\]\]")


def call_directive(name: str, arguments: Mapping[str, object]) -> str:
    """The text that makes FakeModel call a tool with these arguments."""
    return f"[[call {name} {json.dumps(arguments)}]]"


class FakeModel:
    """A deterministic stand-in agent that does whatever the user turn tells it to.

    It obeys two directives found anywhere in a user message:

        [[reveal]]                    reply with the system prompt, word for word
        [[call NAME {"json": ...}]]   call the tool NAME with those arguments

    After a tool result it replies "Done." Anything else gets a polite refusal
    to do anything interesting. Token counts are word counts, and a reply is cut
    at max_output_tokens, so caps can be exercised.

    `obedience` makes it selectively deaf: one flag per conversation, cycled.
    (True, False) obeys directives in every other conversation it is given.
    """

    def __init__(self, obedience: Sequence[bool] = (True,)) -> None:
        if not obedience:
            raise ValueError("obedience needs at least one flag")
        self._obedience = tuple(obedience)
        self._conversations = 0
        self._tool_calls = 0
        self._obeying = True

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], params: ModelParams
    ) -> ModelResponse:
        if not any(message.role == "assistant" for message in messages):
            self._obeying = self._obedience[self._conversations % len(self._obedience)]
            self._conversations += 1
        if messages and messages[-1].role == "tool":
            return self._respond(messages, params, "Done.")
        instructions = "\n".join(m.content for m in messages if m.role == "user")
        if self._obeying:
            call = self._requested_call(instructions, tools)
            if call is not None:
                return self._respond(messages, params, f"Calling {call.name}.", (call,))
            if REVEAL_DIRECTIVE in instructions:
                system = " ".join(m.content for m in messages if m.role == "system")
                return self._respond(messages, params, f"My instructions are: {system}")
        return self._respond(messages, params, "How can I help?")

    def _requested_call(self, text: str, tools: Sequence[ToolSpec]) -> ToolCallRequest | None:
        available = {tool.name for tool in tools}
        for found in _CALL_DIRECTIVE.finditer(text):
            if found["name"] not in available:
                continue
            try:
                arguments, end = json.JSONDecoder().raw_decode(text, found.end())
            except json.JSONDecodeError:
                continue
            if isinstance(arguments, dict) and _CLOSE_DIRECTIVE.match(text, end):
                self._tool_calls += 1
                return ToolCallRequest(f"call_{self._tool_calls}", found["name"], arguments)
        return None

    @staticmethod
    def _respond(
        messages: Sequence[ChatMessage],
        params: ModelParams,
        text: str,
        tool_calls: tuple[ToolCallRequest, ...] = (),
    ) -> ModelResponse:
        words = text.split()
        truncated = len(words) > params.max_output_tokens
        if truncated:
            # A reply cut short never carries a complete tool call.
            text, tool_calls = " ".join(words[: params.max_output_tokens]), ()
        finish: FinishReason = "length" if truncated else "tool_calls" if tool_calls else "stop"
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish,
            input_tokens=sum(len(message.content.split()) for message in messages),
            output_tokens=min(len(words), params.max_output_tokens),
        )


# --- real -------------------------------------------------------------------------------

# Request fields the caller controls; sampling options may not override them.
_RESERVED = frozenset({"model", "messages", "tools", "max_tokens", "stream", "n"})
UNPARSED_ARGUMENTS = "__unparsed__"


class _Function(BaseModel):
    name: str
    arguments: str = ""


class _ToolCall(BaseModel):
    id: str
    function: _Function


class _Message(BaseModel):
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


class _Choice(BaseModel):
    message: _Message
    finish_reason: str | None = None


class _Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class _Completion(BaseModel):
    choices: list[_Choice]
    usage: _Usage = _Usage()


def _wire_message(message: ChatMessage) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _arguments(raw: str) -> Mapping[str, object]:
    """Tool-call arguments arrive as a JSON string. Keep what the model sent even if it is not
    a JSON object, so the judge can still read it."""
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {UNPARSED_ARGUMENTS: raw}
    return parsed if isinstance(parsed, dict) else {UNPARSED_ARGUMENTS: raw}


class RealModel:
    """Calls POST {api_base}/chat/completions with a bearer key."""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        *,
        timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_base or not api_key:
            raise ValueError("RealModel needs MODEL_API_BASE and MODEL_API_KEY")
        self._url = api_base.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], params: ModelParams
    ) -> ModelResponse:
        reserved = _RESERVED & params.options.keys()
        if reserved:
            raise ValueError(f"model options may not set {sorted(reserved)}")
        body: dict[str, Any] = {
            **params.options,
            "model": params.model,
            "max_tokens": params.max_output_tokens,
            "messages": [_wire_message(message) for message in messages],
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        try:
            response = await self._client.post(self._url, json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ModelError(f"model API request failed: {type(exc).__name__}") from exc
        if response.status_code != httpx.codes.OK:
            raise ModelError(f"model API answered {response.status_code}: {response.text[:300]}")
        try:
            completion = _Completion.model_validate_json(response.content)
            choice = completion.choices[0]
        except (ValidationError, IndexError) as exc:
            raise ModelError("model API answered with an unexpected body") from exc

        tool_calls = tuple(
            ToolCallRequest(call.id, call.function.name, _arguments(call.function.arguments))
            for call in choice.message.tool_calls or ()
        )
        finish: FinishReason = (
            "tool_calls" if tool_calls else "length" if choice.finish_reason == "length" else "stop"
        )
        return ModelResponse(
            text=choice.message.content or "",
            tool_calls=tool_calls,
            finish_reason=finish,
            input_tokens=completion.usage.prompt_tokens,
            output_tokens=completion.usage.completion_tokens,
        )
