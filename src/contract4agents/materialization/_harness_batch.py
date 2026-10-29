"""Compact state for adjacent Harness calls and their ordered results."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from contract4agents._strict_json import json_array, json_object, json_string, require_exact_keys
from contract4agents.ir import FrozenJsonValue, FrozenMap
from contract4agents.materialization._harness_configuration import frozen_object, thaw
from contract4agents.materialization._harness_models import HarnessApproval, HarnessPendingCall

_TOOL_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _identifier(label: str, value: object) -> str:
    identifier = json_string(label, value)
    if _TOOL_IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(f"Invalid Harness {label}")
    return identifier


def _approval(value: object) -> HarnessApproval:
    raw = json_object("Harness approval", value)
    require_exact_keys("Harness approval", raw, {"call_digest", "approved", "evidence_ref"})
    approved = raw["approved"]
    if type(approved) is not bool:
        raise ValueError("Harness approval requires an explicit decision")
    return HarnessApproval(
        call_digest=json_string("call_digest", raw["call_digest"]),
        approved=approved,
        evidence_ref=json_string("evidence_ref", raw["evidence_ref"]),
    )


@dataclass(frozen=True)
class HarnessBatchCall:
    """One complete adjacent call without duplicated invocation context."""

    tool_name: str
    tool_use_id: str
    arguments: FrozenMap[str, FrozenJsonValue]
    event_id: str

    def __post_init__(self) -> None:
        _identifier("tool_name", self.tool_name)
        _identifier("tool_use_id", self.tool_use_id)
        json_string("event_id", self.event_id)
        object.__setattr__(self, "arguments", frozen_object(self.arguments))

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "tool_use_id": self.tool_use_id,
            "arguments": thaw(self.arguments),
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> HarnessBatchCall:
        raw = json_object("Harness batch call", value)
        require_exact_keys("Harness batch call", raw, {"tool_name", "tool_use_id", "arguments", "event_id"})
        return cls(
            tool_name=_identifier("tool_name", raw["tool_name"]),
            tool_use_id=_identifier("tool_use_id", raw["tool_use_id"]),
            arguments=frozen_object(raw["arguments"]),
            event_id=json_string("event_id", raw["event_id"]),
        )


@dataclass(frozen=True)
class HarnessBatchResult:
    """One validated response and its optional application approval."""

    response: FrozenMap[str, FrozenJsonValue]
    approval: HarnessApproval | None

    def __post_init__(self) -> None:
        response = frozen_object(self.response)
        raw = json_object("Harness batch response", thaw(response))
        require_exact_keys("Harness batch response", raw, {"toolUseId", "content", "status"})
        _identifier("toolUseId", raw["toolUseId"])
        if raw["status"] not in ("success", "error"):
            raise ValueError("Harness batch response requires success or error status")
        content = json_array("Harness response content", raw["content"])
        if len(content) != 1:
            raise ValueError("Harness batch response requires one text content block")
        text = json_object("Harness response text", content[0])
        require_exact_keys("Harness response text", text, {"text"})
        if not isinstance(text["text"], str):
            raise TypeError("Harness batch response text must be a string")
        object.__setattr__(self, "response", response)
        if self.approval is not None:
            if not isinstance(self.approval, HarnessApproval):
                raise TypeError("Harness batch approval must be an approval record")
            _approval({
                "call_digest": self.approval.call_digest,
                "approved": self.approval.approved,
                "evidence_ref": self.approval.evidence_ref,
            })

    def to_dict(self) -> dict[str, object]:
        approval: dict[str, object] | None = None
        if self.approval is not None:
            approval = {
                "call_digest": self.approval.call_digest,
                "approved": self.approval.approved,
                "evidence_ref": self.approval.evidence_ref,
            }
        return {"response": thaw(self.response), "approval": approval}

    @classmethod
    def from_dict(cls, value: object) -> HarnessBatchResult:
        raw = json_object("Harness batch result", value)
        require_exact_keys("Harness batch result", raw, {"response", "approval"})
        return cls(
            response=frozen_object(raw["response"]),
            approval=_approval(raw["approval"]) if raw["approval"] is not None else None,
        )


@dataclass(frozen=True)
class HarnessPendingBatch:
    """Adjacent calls with the completed result prefix needed for continuation."""

    calls: tuple[HarnessBatchCall, ...]
    results: tuple[HarnessBatchResult, ...] = ()

    def __post_init__(self) -> None:
        calls, results = tuple(self.calls), tuple(self.results)
        if len(calls) < 2 or len(results) >= len(calls):
            raise ValueError("Harness pending batch requires adjacent calls and an unfinished result prefix")
        if any(not isinstance(call, HarnessBatchCall) for call in calls):
            raise TypeError("Harness pending batch requires call records")
        if any(not isinstance(result, HarnessBatchResult) for result in results):
            raise TypeError("Harness pending batch requires result records")
        if len({call.tool_use_id for call in calls}) != len(calls):
            raise ValueError("Harness pending batch has duplicate tool use IDs")
        if len({call.event_id for call in calls}) != len(calls):
            raise ValueError("Harness pending batch has duplicate event IDs")
        if any(result.response["toolUseId"] != calls[index].tool_use_id for index, result in enumerate(results)):
            raise ValueError("Harness pending batch result does not match its call")
        object.__setattr__(self, "calls", calls)
        object.__setattr__(self, "results", results)

    @property
    def current_call(self) -> HarnessBatchCall:
        return self.calls[len(self.results)]

    def pending(self, base: HarnessPendingCall) -> HarnessPendingCall:
        current = self.current_call
        return replace(
            base,
            tool_name=current.tool_name,
            tool_use_id=current.tool_use_id,
            arguments=current.arguments,
            event_id=current.event_id,
            batch=frozen_object(self.to_dict()),
        )

    def prior_pending(self, base: HarnessPendingCall, index: int) -> HarnessPendingCall:
        if type(index) is not int or not 0 <= index <= len(self.results):
            raise ValueError("Harness prior call index must reference the stored result prefix")
        return HarnessPendingBatch(self.calls, self.results[:index]).pending(base)

    def to_dict(self) -> dict[str, object]:
        return {
            "calls": [call.to_dict() for call in self.calls],
            "results": [result.to_dict() for result in self.results],
        }

    @classmethod
    def from_dict(cls, value: object) -> HarnessPendingBatch:
        raw = json_object("Harness pending batch", thaw(frozen_object(value)))
        require_exact_keys("Harness pending batch", raw, {"calls", "results"})
        return cls(
            calls=tuple(HarnessBatchCall.from_dict(call) for call in json_array("Harness batch calls", raw["calls"])),
            results=tuple(
                HarnessBatchResult.from_dict(result) for result in json_array("Harness batch results", raw["results"])
            ),
        )
