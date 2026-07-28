"""Stable materialization trace sink independent of provider-native spans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from contract4agents.ir import (
    CanonicalIR,
    FrozenJsonValue,
    FrozenMap,
    SemanticId,
    format_type_ref,
    freeze_json,
)
from contract4agents.planning import MaterializationPlan


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


def _emit_materialization_events(
    sink: MaterializationTraceSink,
    ir: CanonicalIR,
    plan: MaterializationPlan,
) -> None:
    """Emit the provider-neutral configured-graph evidence sequence."""

    def emit(
        event_type: str,
        *,
        semantic_id: SemanticId | None = None,
        agent_id: SemanticId | None = None,
        related_id: SemanticId | None = None,
        data: Mapping[str, object] | None = None,
    ) -> None:
        frozen = freeze_json(data or {})
        if not isinstance(frozen, FrozenMap):
            raise TypeError("Materialization trace data must be an object")
        sink.emit(
            MaterializationTraceEvent(
                event_type=event_type,
                contract_digest=plan.contract_digest,
                plan_digest=plan.plan_digest,
                semantic_id=semantic_id,
                agent_id=agent_id,
                related_id=related_id,
                data=frozen,
            )
        )

    for agent_id, agent in ir.agents.items():
        emit("materialization.agent.configured", semantic_id=agent_id, agent_id=agent_id)
        emit(
            "materialization.output_validation.configured",
            semantic_id=agent_id,
            agent_id=agent_id,
            data={"output_type": format_type_ref(agent.output_type)},
        )
    for grant_id, grant in ir.grants.items():
        emit(
            "materialization.grant.configured",
            semantic_id=grant_id,
            agent_id=grant.agent_id,
            related_id=grant.capability_id,
            data={
                "availability": grant.availability,
                "authorization": grant.authorization,
                "execution": grant.execution,
            },
        )
        if grant.availability == "enabled":
            emit(
                "materialization.tool.bound",
                semantic_id=grant.capability_id,
                agent_id=grant.agent_id,
                related_id=grant_id,
            )
        if grant.authorization == "approval_required":
            emit(
                "materialization.approval.configured",
                semantic_id=grant_id,
                agent_id=grant.agent_id,
                related_id=grant.capability_id,
            )
    for edge_id, edge in ir.composition.items():
        emit(
            f"materialization.{edge.mode}.configured",
            semantic_id=edge_id,
            agent_id=edge.source_agent_id,
            related_id=edge.target_agent_id,
            data={"history": edge.history},
        )
    for context_id, context in ir.contexts.items():
        emit(
            "materialization.context.configured",
            semantic_id=context_id,
            agent_id=context.agent_id,
            related_id=context.origin_id,
            data={"origin": context.origin},
        )
    for binding_id, binding in plan.bindings.items():
        if binding.kind in {"datasource", "external"}:
            emit(
                "materialization.resolver.bound",
                semantic_id=binding_id,
                data={"kind": binding.kind, "execution": binding.execution},
            )
            emit(
                f"materialization.{binding.kind}.bound",
                semantic_id=binding_id,
                data={"execution": binding.execution},
            )
    for isolation_id, isolation in plan.isolation.items():
        emit(
            "materialization.isolation.configured",
            semantic_id=isolation_id,
            data={"environment": isolation.environment, "provider": isolation.provider},
        )


__all__ = [
    "NOOP_MATERIALIZATION_TRACE_SINK",
    "MaterializationTraceEvent",
    "NoOpMaterializationTraceSink",
    "RecordingMaterializationTraceSink",
    "MaterializationTraceSink",
]
