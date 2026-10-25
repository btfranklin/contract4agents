"""Read a complete Harness stream before accepting output or tool use."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from contract4agents.materialization._harness_configuration import content_digest

_STOP_REASONS = frozenset(
    {
        "end_turn",
        "tool_use",
        "tool_result",
        "max_tokens",
        "stop_sequence",
        "content_filtered",
        "malformed_model_output",
        "malformed_tool_use",
        "interrupted",
        "partial_turn",
        "model_context_window_exceeded",
        "max_iterations_exceeded",
        "max_output_tokens_exceeded",
        "timeout_exceeded",
        "hook_stopped",
    }
)
_TOOL_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _encode_stream_value(value: object) -> str:
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    raise TypeError(f"Unsupported stream value: {type(value).__name__}")


def decode_json(text: str) -> Any:
    """Reject duplicate keys and non-finite values before typed validation."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise ValueError("Non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


@dataclass
class _Block:
    kind: str
    name: str = ""
    tool_use_id: str = ""
    text: str = ""
    stopped: bool = False


@dataclass
class HarnessStream:
    """Final response state; fragments are never executable tool calls."""

    stop_reason: str | None = None
    failure: str | None = None
    text: str = ""
    tool_name: str | None = None
    tool_use_id: str | None = None
    arguments: object = None
    usage: list[Mapping[str, Any]] = field(default_factory=list)

    @classmethod
    def read(cls, events: Iterable[Mapping[str, Any]]) -> HarnessStream:
        result = cls()
        blocks: dict[int, _Block] = {}
        started = False
        stopped = False
        byte_count = 0
        usage_digests: set[str] = set()
        block: _Block | None
        for event in events:
            try:
                byte_count += len(json.dumps(event, default=_encode_stream_value).encode("utf-8"))
            except (TypeError, ValueError):
                result.failure = "invalid_stream"
                continue
            if byte_count > 8 * 1024 * 1024:
                raise ValueError("Harness stream exceeds the client size limit")
            if not isinstance(event, Mapping) or len(event) != 1:
                result.failure = "invalid_stream"
                continue
            name, payload = next(iter(event.items()))
            if not isinstance(payload, Mapping):
                result.failure = "invalid_stream"
                continue
            if name in {"internalServerException", "runtimeClientError", "validationException"}:
                result.failure = "provider_error"
            elif name == "messageStart":
                if set(payload) != {"role"} or (started and not stopped) or payload.get("role") != "assistant":
                    result.failure = "invalid_stream"
                started, stopped = True, False
                blocks = {}
                result.stop_reason = None
            elif name in {"contentBlockStart", "contentBlockDelta", "contentBlockStop"}:
                index = payload.get("contentBlockIndex")
                expected = (
                    {"contentBlockIndex", "start"}
                    if name == "contentBlockStart"
                    else {"contentBlockIndex", "delta"}
                    if name == "contentBlockDelta"
                    else {"contentBlockIndex"}
                )
                if set(payload) != expected or not started or stopped or type(index) is not int or index < 0:
                    result.failure = "invalid_stream"
                    continue
                if name == "contentBlockStart":
                    if index in blocks:
                        result.failure = "invalid_stream"
                        continue
                    start = payload["start"]
                    if not isinstance(start, Mapping) or len(start) != 1:
                        result.failure = "invalid_stream"
                        continue
                    tool = start.get("toolUse")
                    if tool is not None and "toolUse" in start:
                        if (
                            not isinstance(tool, Mapping)
                            or not isinstance(tool.get("name"), str)
                            or not isinstance(tool.get("toolUseId"), str)
                            or set(tool) - {"name", "toolUseId", "type"}
                            or _TOOL_IDENTIFIER.fullmatch(tool["name"]) is None
                            or _TOOL_IDENTIFIER.fullmatch(tool["toolUseId"]) is None
                            or tool.get("type") not in (None, "tool_use")
                        ):
                            result.failure = "invalid_stream"
                            continue
                        blocks[index] = _Block("tool", name=tool["name"], tool_use_id=tool["toolUseId"])
                    else:
                        result.failure = "unsupported_stream_event"
                elif name == "contentBlockDelta":
                    delta = payload["delta"]
                    if not isinstance(delta, Mapping) or len(delta) != 1:
                        result.failure = "invalid_stream"
                        continue
                    if "text" in delta:
                        block = blocks.setdefault(index, _Block("text"))
                        if block.stopped or block.kind != "text" or not isinstance(delta["text"], str):
                            result.failure = "invalid_stream"
                        else:
                            block.text += delta["text"]
                    elif "toolUse" in delta:
                        block = blocks.get(index)
                        tool_delta = delta["toolUse"]
                        fragment = (
                            tool_delta.get("input")
                            if isinstance(tool_delta, Mapping) and set(tool_delta) == {"input"}
                            else None
                        )
                        if block is None or block.stopped or block.kind != "tool" or not isinstance(fragment, str):
                            result.failure = "invalid_stream"
                        else:
                            block.text += fragment
                    elif "reasoningContent" in delta:
                        reasoning = delta["reasoningContent"]
                        if (
                            not isinstance(reasoning, Mapping)
                            or set(reasoning)
                            - {
                                "text",
                                "redactedContent",
                                "signature",
                            }
                            or ("text" in reasoning and not isinstance(reasoning["text"], str))
                            or ("redactedContent" in reasoning and not isinstance(reasoning["redactedContent"], bytes))
                            or ("signature" in reasoning and not isinstance(reasoning["signature"], str))
                        ):
                            result.failure = "invalid_stream"
                        else:
                            blocks.setdefault(index, _Block("reasoning"))
                    else:
                        result.failure = "unsupported_stream_event"
                else:
                    block = blocks.get(index)
                    if block is None or block.stopped:
                        result.failure = "invalid_stream"
                    else:
                        block.stopped = True
            elif name == "messageStop":
                if (
                    set(payload) != {"stopReason"}
                    or not isinstance(payload.get("stopReason"), str)
                    or payload.get("stopReason") not in _STOP_REASONS
                ):
                    result.failure = "invalid_stream"
                elif not started or stopped or any(not block.stopped for block in blocks.values()):
                    result.failure = "incomplete_stream"
                stopped = True
                result.stop_reason = payload.get("stopReason")
            elif name == "metadata":
                usage = payload.get("usage")
                metrics = payload.get("metrics")
                if (
                    set(payload) != {"usage", "metrics"}
                    or not isinstance(usage, Mapping)
                    or set(usage)
                    - {
                        "inputTokens",
                        "outputTokens",
                        "totalTokens",
                        "cacheReadInputTokens",
                        "cacheWriteInputTokens",
                    }
                    or not {"inputTokens", "outputTokens", "totalTokens"} <= set(usage)
                    or any(type(usage[key]) is not int or usage[key] < 0 for key in usage)
                    or not isinstance(metrics, Mapping)
                    or set(metrics) != {"latencyMs"}
                    or type(metrics.get("latencyMs")) not in {int, float}
                    or metrics["latencyMs"] < 0
                ):
                    result.failure = "invalid_stream"
                else:
                    digest = content_digest(usage)
                    if digest not in usage_digests:
                        result.usage.append(usage)
                        usage_digests.add(digest)
            elif name == "hookEvent":
                if (
                    set(payload) - {"hookEventId", "name", "type", "decision", "reason"}
                    or not {"hookEventId", "name", "type"} <= set(payload)
                    or any(not isinstance(payload[key], str) for key in ("hookEventId", "name", "type"))
                    or payload["type"]
                    not in {"before_tool_call", "after_tool_call", "before_invocation", "after_invocation"}
                    or ("decision" in payload and payload["decision"] not in {"allow", "deny"})
                    or ("reason" in payload and not isinstance(payload["reason"], str))
                ):
                    result.failure = "invalid_stream"
            else:
                result.failure = "unsupported_stream_event"
        if (not started or not stopped or result.stop_reason is None) and result.failure is None:
            result.failure = "incomplete_stream"
        result.text = "".join(block.text for block in blocks.values() if block.kind == "text")
        calls = [block for _, block in sorted(blocks.items()) if block.kind == "tool"]
        if result.stop_reason == "tool_use":
            if result.failure is not None:
                pass
            elif not calls:
                result.failure = "incomplete_stream"
            elif len({call.tool_use_id for call in calls}) != len(calls):
                result.failure = "invalid_stream"
            else:
                first = calls[0]
                try:
                    arguments = decode_json(first.text or "{}")
                except ValueError:
                    result.failure = "invalid_tool_input"
                else:
                    result.tool_name, result.tool_use_id = first.name, first.tool_use_id
                    result.arguments = arguments
        elif result.stop_reason == "end_turn" and calls:
            result.failure = "invalid_stream"
        return result
