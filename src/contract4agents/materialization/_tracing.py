"""Stable materialization trace sink independent of provider-native spans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from contract4agents.ir import FrozenJsonValue, FrozenMap, SemanticId


@dataclass(frozen=True)
class MaterializationTraceEvent:
    event_type: str
    contract_digest: str
    plan_digest: str
    semantic_id: SemanticId | None = None
    agent_id: SemanticId | None = None
    related_id: SemanticId | None = None
    data: FrozenMap[str, FrozenJsonValue] = field(default_factory=FrozenMap)


@runtime_checkable
class MaterializationTraceSink(Protocol):
    def emit(self, event: MaterializationTraceEvent) -> None:
        """Accept one deterministic materialization event."""


class NoOpMaterializationTraceSink:
    def emit(self, event: MaterializationTraceEvent) -> None:
        del event


class RecordingMaterializationTraceSink:
    """Small in-memory sink for tests and host integration."""

    def __init__(self) -> None:
        self.events: list[MaterializationTraceEvent] = []

    def emit(self, event: MaterializationTraceEvent) -> None:
        self.events.append(event)


NOOP_MATERIALIZATION_TRACE_SINK = NoOpMaterializationTraceSink()


__all__ = [
    "NOOP_MATERIALIZATION_TRACE_SINK",
    "MaterializationTraceEvent",
    "NoOpMaterializationTraceSink",
    "RecordingMaterializationTraceSink",
    "MaterializationTraceSink",
]
