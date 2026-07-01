"""Trace event recording for Contract4Agents runtime internals."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TRACE_SCHEMA_VERSION = "1"

KNOWN_TRACE_EVENT_TYPES = frozenset(
    {
        "agent.started",
        "agent.completed",
        "agent.handoff",
        "tool.requested",
        "tool.started",
        "tool.allowed",
        "tool.denied",
        "tool.completed",
        "tool.failed",
        "host_tool.requested",
        "host_tool.started",
        "host_tool.completed",
        "host_tool.failed",
        "hosted_tool.requested",
        "hosted_tool.started",
        "hosted_tool.completed",
        "hosted_tool.failed",
        "datasource.started",
        "datasource.resolved",
        "datasource.failed",
        "approval.requested",
        "approval.completed",
        "stage.completed",
        "output.accepted",
        "output.rejected",
        "output.schema_failed",
        "assertion.evaluated",
        "guardrail.rejected",
        "llm.started",
        "llm.completed",
    }
)

TRACE_ENVELOPE_INDEX_FIELDS = ("agent", "tool", "datasource", "stage", "guardrail", "assertion")


@dataclass(frozen=True)
class TraceEvent:
    type: str
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)


class TraceRecorder:
    def __init__(self, path: Path | None = None, *, run_id: str | None = None) -> None:
        self.path = path
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self._event_index = 0
        self.events: list[TraceEvent] = []

    def record(self, event_type: str, **data: Any) -> TraceEvent:
        envelope = self._envelope(event_type, data)
        event = TraceEvent(str(envelope["event_type"]), float(envelope["timestamp"]), _event_data(envelope))
        self.events.append(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                handle.write(json.dumps(envelope, sort_keys=True) + "\n")
        return event

    def _envelope(self, event_type: str, event_data: dict[str, Any]) -> dict[str, Any]:
        self._event_index += 1
        timestamp = event_data.pop("timestamp", time.time())
        event_id = event_data.pop("event_id", f"evt-{self._event_index:06d}")
        run_id = event_data.pop("run_id", self.run_id)
        provider = event_data.pop("provider", {})
        if provider is None:
            provider = {}
        if not isinstance(provider, dict):
            raise ValueError("Trace provider metadata must be an object")
        index_fields: dict[str, str] = {}
        for field_name in TRACE_ENVELOPE_INDEX_FIELDS:
            if field_name in event_data:
                index_fields[field_name] = str(event_data.pop(field_name))
        explicit_data = event_data.pop("data", None)
        if explicit_data is None:
            payload_data = event_data
        elif isinstance(explicit_data, dict):
            payload_data = {**explicit_data, **event_data}
        else:
            raise ValueError("Trace event data must be an object")
        envelope: dict[str, Any] = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": str(run_id),
            "event_id": str(event_id),
            "event_type": str(event_type),
            "timestamp": float(timestamp),
            "data": payload_data,
            "provider": provider,
        }
        envelope.update(index_fields)
        return envelope

    def count(self, event_type: str, target: str | None = None) -> int:
        return sum(
            1
            for event in self.events
            if event.type == event_type and (target is None or target in event.data.values())
        )


def _event_data(envelope: dict[str, Any]) -> dict[str, Any]:
    data = dict(envelope.get("data", {}))
    for field_name in (
        "schema_version",
        "run_id",
        "event_id",
        "event_type",
        "agent",
        "tool",
        "datasource",
        "stage",
        "guardrail",
        "assertion",
    ):
        if field_name in envelope:
            data[field_name] = envelope[field_name]
    data["data"] = dict(envelope.get("data", {}))
    data["provider"] = dict(envelope.get("provider", {}))
    return data


__all__ = [
    "KNOWN_TRACE_EVENT_TYPES",
    "TRACE_ENVELOPE_INDEX_FIELDS",
    "TRACE_SCHEMA_VERSION",
    "TraceEvent",
    "TraceRecorder",
]
