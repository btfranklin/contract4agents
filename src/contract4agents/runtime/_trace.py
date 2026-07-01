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
TRACE_TARGET_FIELDS = TRACE_ENVELOPE_INDEX_FIELDS + ("produces",)


@dataclass(frozen=True)
class TraceEvent:
    type: str
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)


class TraceScopeError(ValueError):
    """Raised when trace evaluation needs an explicit run scope."""


class TraceRecorder:
    def __init__(self, path: Path | None = None, *, run_id: str | None = None, append: bool = False) -> None:
        self.path = path
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self.append = append
        self._path_initialized = False
        self._event_index = 0
        self.events: list[TraceEvent] = []

    def record(self, event_type: str, **data: Any) -> TraceEvent:
        envelope = self._envelope(event_type, data)
        line = json.dumps(envelope, sort_keys=True) + "\n"
        event = TraceEvent(
            str(envelope["event_type"]),
            float(envelope["timestamp"]),
            event_data_from_envelope(envelope),
        )
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if self.append or self._path_initialized else "w"
            with self.path.open(mode) as handle:
                handle.write(line)
            self._path_initialized = True
        self.events.append(event)
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
            if event.type == event_type
            and (
                target is None
                or target.strip().strip('"')
                in {str(event.data[field]) for field in TRACE_TARGET_FIELDS if field in event.data}
            )
        )


def event_data_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    data = dict(envelope.get("data", {}))
    for field_name in ("schema_version", "run_id", "event_id", "event_type", *TRACE_ENVELOPE_INDEX_FIELDS):
        if field_name in envelope:
            data[field_name] = envelope[field_name]
    data["data"] = dict(envelope.get("data", {}))
    data["provider"] = dict(envelope.get("provider", {}))
    return data


def scope_trace(trace: TraceRecorder, *, run_id: str | None = None, agent: str | None = None) -> TraceRecorder:
    """Return a trace containing one run, optionally scoped to one agent.

    Single-run traces can be evaluated without an explicit run_id. Multi-run
    traces must provide a run_id so events from separate runs cannot satisfy the
    same trace assertion or monitor rule.
    """
    event_run_ids = {_event_run_id(trace, event) for event in trace.events}
    if run_id is None:
        if len(event_run_ids) > 1:
            raise TraceScopeError("Trace contains multiple run_id values; pass run_id explicitly")
        selected_run_id = next(iter(event_run_ids), trace.run_id)
    else:
        selected_run_id = run_id

    scoped = TraceRecorder(run_id=selected_run_id)
    scoped.events = [
        event
        for event in trace.events
        if _event_run_id(trace, event) == selected_run_id and _event_matches_agent(event, agent)
    ]
    return scoped


def _event_run_id(trace: TraceRecorder, event: TraceEvent) -> str:
    return str(event.data.get("run_id", trace.run_id))


def _event_matches_agent(event: TraceEvent, agent: str | None) -> bool:
    if agent is None:
        return True
    event_agent = event.data.get("agent")
    return event_agent is None or event_agent == agent


__all__ = [
    "KNOWN_TRACE_EVENT_TYPES",
    "TRACE_ENVELOPE_INDEX_FIELDS",
    "TRACE_SCHEMA_VERSION",
    "TraceEvent",
    "TraceRecorder",
    "TraceScopeError",
    "event_data_from_envelope",
    "scope_trace",
]
