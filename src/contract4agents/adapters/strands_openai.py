"""Public Strands Chat Completions extension for reasoning and host evidence."""
from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from enum import StrEnum
from typing import Protocol, Self

from pydantic import JsonValue, TypeAdapter
from strands.agent.agent_metadata import AgentMetadata
from strands.models.openai import OpenAIModel
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from contract4agents.adapters._strands_http import RecordingOpenAITransport, StrandsHTTPObserver

__all__ = [
    "ReasoningOpenAIModel", "StrandsModelObserver", "RecordingOpenAITransport",
    "StrandsHTTPObserver", "ReasoningHistoryMode",
]


_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])



class StrandsModelObserver(Protocol):
    """Host-owned evidence sink. Calls run before the consumer receives an event."""

    def request(self, payload: dict[str, JsonValue]) -> None: ...
    def event(self, payload: dict[str, JsonValue]) -> None: ...
    def failed(self, error: BaseException) -> None: ...
    def completed(self) -> None: ...


def _reasoning(contents: object) -> str | None:
    if not isinstance(contents, list):
        raise ValueError("Assistant content must be a list")
    parts: list[str] = []
    present = False
    for block in contents:
        if not isinstance(block, Mapping) or "reasoningContent" not in block:
            continue
        present = True
        value = block["reasoningContent"]
        if not isinstance(value, Mapping):
            raise ValueError("Assistant reasoning must be an object")
        if "reasoningText" in value:
            value = value["reasoningText"]
        if not isinstance(value, Mapping) or not isinstance(value.get("text"), str):
            raise ValueError("Chat Completions cannot preserve non-text reasoning")
        parts.append(value["text"])
    return "".join(parts) if present else None


class ReasoningHistoryMode(StrEnum):
    """Whether the provider requires a reasoning field on each assistant turn."""

    PRESERVE = "preserve"
    REQUIRED = "required"


class ReasoningOpenAIModel(OpenAIModel):
    """Preserve assistant reasoning through public Strands serialization methods.

    The host chooses compatible models and AWS authentication through the normal
    native constructor. The observer records provider request objects and native
    Strands stream events, not HTTP bytes or raw provider SSE frames.
    """

    observation_sink: StrandsModelObserver | None = None
    _reasoning_history_mode = ReasoningHistoryMode.PRESERVE

    @property
    def reasoning_history_mode(self) -> ReasoningHistoryMode:
        return self._reasoning_history_mode

    def with_reasoning_history(self, mode: ReasoningHistoryMode) -> Self:
        """Select a checked provider protocol policy without changing reasoning text."""
        if not isinstance(mode, ReasoningHistoryMode):
            raise TypeError("The reasoning history mode must be a ReasoningHistoryMode")
        self._reasoning_history_mode = mode
        return self

    def observe(self, sink: StrandsModelObserver) -> Self:
        """Attach the host evidence sink. Native constructor options stay unchanged."""
        self.observation_sink = sink
        return self

    @classmethod
    def format_request_messages(
        cls, messages: Messages, system_prompt: str | None = None, *,
        system_prompt_content: list[SystemContentBlock] | None = None, **kwargs: object,
    ) -> list[dict[str, object]]:
        # Let Strands serialize all ordinary content and tool calls.
        stripped: Messages = [
            {**message, "content": [block for block in message["content"] if "reasoningContent" not in block]}
            for message in messages
        ]
        formatted: list[dict[str, object]] = super().format_request_messages(
            stripped, system_prompt, system_prompt_content=system_prompt_content, **kwargs,
        )
        assistants = iter(row for row in formatted if row.get("role") == "assistant")
        for message in messages:
            if message["role"] != "assistant":
                continue
            reasoning = _reasoning(message["content"])
            if not any("reasoningContent" not in block for block in message["content"]):
                if reasoning is not None:
                    raise ValueError("Assistant reasoning has no ordinary message or tool call")
                continue
            current = next(assistants, None)
            if current is None:
                if reasoning is not None:
                    raise ValueError("Assistant reasoning has no matching message")
                continue
            if reasoning is not None:
                current["reasoning_content"] = reasoning
        return formatted

    def format_request(
        self, messages: Messages, tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None, tool_choice: ToolChoice | None = None, *,
        system_prompt_content: list[SystemContentBlock] | None = None,
        agent_metadata: AgentMetadata | None = None, **kwargs: object,
    ) -> dict[str, object]:
        request: dict[str, object] = super().format_request(
            messages, tool_specs, system_prompt, tool_choice,
            system_prompt_content=system_prompt_content, agent_metadata=agent_metadata, **kwargs,
        )
        if not tool_specs:
            # Chat Completions has no meaning for an empty tool list; omit it rather than advertise tools.
            if tool_choice is not None and "auto" not in tool_choice:
                raise ValueError("A required or named tool choice needs at least one tool")
            request.pop("tools", None)
            request.pop("tool_choice", None)
        if self._reasoning_history_mode == ReasoningHistoryMode.REQUIRED:
            messages_out = request.get("messages")
            if not isinstance(messages_out, list):
                raise ValueError("Chat Completions messages must be a list")
            for message in messages_out:
                if isinstance(message, dict) and message.get("role") == "assistant":
                    if "content" in message or "tool_calls" in message:
                        message.setdefault("reasoning_content", "")
        if self.observation_sink is not None:
            self.observation_sink.request(_JSON_OBJECT.validate_python(request, strict=True))
        return request

    async def stream(
        self, messages: Messages, tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None, *, tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        agent_metadata: AgentMetadata | None = None, **kwargs: object,
    ) -> AsyncGenerator[StreamEvent, None]:
        try:
            async for event in super().stream(
                messages, tool_specs, system_prompt, tool_choice=tool_choice,
                system_prompt_content=system_prompt_content, agent_metadata=agent_metadata, **kwargs,
            ):
                if self.observation_sink is not None:
                    self.observation_sink.event(_JSON_OBJECT.validate_python(event, strict=True))
                yield event
        except BaseException as error:
            if self.observation_sink is not None:
                self.observation_sink.failed(error)
            raise
        else:
            if self.observation_sink is not None:
                self.observation_sink.completed()
