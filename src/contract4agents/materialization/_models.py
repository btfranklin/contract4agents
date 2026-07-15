"""Public result and provider models for V2 materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from contract4agents.compiler import CompilerArtifacts
from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._tracing import TraceSink
from contract4agents.planning import MaterializationPlan, PlannerCapabilities
from contract4agents.runtime import EnvironmentEnforcementEvidence, EnvironmentProvider
from contract4agents.target_bindings import TargetBinding


@dataclass(frozen=True)
class GraphValidationEvidence:
    plan_digest: str
    agent_ids: tuple[SemanticId, ...]
    grant_ids: tuple[SemanticId, ...]
    composition_ids: tuple[SemanticId, ...]


@dataclass(frozen=True)
class NativeAgentGraph:
    """A framework-native graph and the resolved host implementations it uses."""

    agents: FrozenMap[SemanticId, object]
    output_types: FrozenMap[str, type[object]]
    implementations: FrozenMap[SemanticId, object]
    grant_objects: FrozenMap[SemanticId, object]
    composition_objects: FrozenMap[SemanticId, object]
    context: ContextRuntime
    environment_evidence: tuple[EnvironmentEnforcementEvidence, ...]
    validation: GraphValidationEvidence

    def agent(self, name: str) -> object:
        for identifier, native_agent in self.agents.items():
            if identifier.parts[0] == name:
                return native_agent
        raise KeyError(name)


@dataclass(frozen=True)
class MaterializationResult:
    graph: NativeAgentGraph
    plan: MaterializationPlan

    @property
    def agents(self) -> FrozenMap[str, object]:
        """Return native agents by the contract names users wrote."""

        return FrozenMap(
            (identifier.parts[0], native_agent)
            for identifier, native_agent in self.graph.agents.items()
        )

    @property
    def context(self) -> ContextRuntime:
        """Return the typed context resolver wired into the native graph."""

        return self.graph.context


@runtime_checkable
class MaterializationProvider(Protocol):
    """Injectable adapter-specific native graph constructor."""

    adapter: str

    def planner_capabilities(
        self,
        environment: EnvironmentProvider | None,
    ) -> PlannerCapabilities:
        """Return the exact mappings implemented by this provider configuration."""

    def build_graph(
        self,
        *,
        ir: CanonicalIR,
        artifacts: CompilerArtifacts,
        target: TargetBinding,
        plan: MaterializationPlan,
        implementations: FrozenMap[SemanticId, object],
        output_types: FrozenMap[str, type[object]],
        context_runtime: ContextRuntime,
        environment: EnvironmentProvider | None,
        trace_sink: TraceSink,
    ) -> NativeAgentGraph:
        """Construct and validate the complete native graph."""


__all__ = [
    "GraphValidationEvidence",
    "MaterializationProvider",
    "MaterializationResult",
    "NativeAgentGraph",
]
