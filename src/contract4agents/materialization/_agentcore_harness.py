"""Materialize verified remote Harness handles without resource writes."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from contract4agents.adapters._agentcore_harness import agentcore_harness_planner_capabilities
from contract4agents.compiler import CompilerArtifacts
from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue
from contract4agents.materialization._harness_configuration import build_harness_configuration
from contract4agents.materialization._harness_deployment import HarnessDeployment, HarnessRuntimeClient
from contract4agents.materialization._harness_invocation import HarnessAgent
from contract4agents.materialization._harness_validation import validate_harness_graph
from contract4agents.materialization._models import (
    NativeAgentGraph,
)
from contract4agents.materialization._tracing import MaterializationTraceSink, _emit_materialization_events
from contract4agents.materialization._types import GeneratedType
from contract4agents.planning import MaterializationPlan, PlannedSystem, PlannerCapabilities
from contract4agents.runtime import EnvironmentProvider
from contract4agents.target_bindings import TargetBinding
from contract4agents.tracing import NormalizedTraceSink


class AgentCoreHarnessMaterializationProvider:
    """Bind local typed handles to explicit deployment verification records."""

    adapter = "agentcore_harness"

    def __init__(
        self,
        *,
        deployments: Mapping[str, HarnessDeployment] | None = None,
        client: HarnessRuntimeClient | None = None,
        trace_sink: NormalizedTraceSink | None = None,
    ) -> None:
        self.deployments = FrozenMap(deployments or {})
        self.client = client
        self.trace_sink = trace_sink

    def planner_capabilities(self, environment: EnvironmentProvider | None) -> PlannerCapabilities:
        del environment
        return agentcore_harness_planner_capabilities()

    def build_graph(
        self,
        *,
        ir: CanonicalIR,
        artifacts: CompilerArtifacts,
        target: TargetBinding,
        plan: MaterializationPlan,
        implementations: FrozenMap[SemanticId, object],
        input_types: FrozenMap[SemanticId, type[BaseModel] | None],
        output_types: FrozenMap[str, GeneratedType],
        context_runtime: ContextRuntime,
        environment: EnvironmentProvider | None,
        materialization_trace_sink: MaterializationTraceSink,
    ) -> NativeAgentGraph:
        del target, environment
        configuration = build_harness_configuration(
            PlannedSystem(artifacts, plan),
            input_types=input_types,
            output_types=output_types,
        )
        expected = set(configuration.agents)
        if set(self.deployments) != expected:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT600",
                        "Harness materialization requires explicit verified deployment records for every agent",
                    ),
                )
            )
        agents: dict[SemanticId, object] = {}
        for name, configured in configuration.agents.items():
            deployment = self.deployments[name]
            try:
                deployment.validate(configuration, name)
            except ValueError as exc:
                raise MaterializationError((MaterializationIssue("MAT601", str(exc), configured.agent_id),)) from exc
            agents[configured.agent_id] = HarnessAgent(
                configured, deployment, self.client, self.trace_sink or context_runtime.trace_sink
            )
        validation = validate_harness_graph(configuration, agents)
        if not validation.complete:
            raise MaterializationError((MaterializationIssue("MAT602", "Harness graph validation is incomplete"),))
        grants: dict[SemanticId, object] = {}
        edges: dict[SemanticId, object] = {}
        for configured in configuration.agents.values():
            for tool in configured.tools.values():
                if tool.grant_id is not None:
                    grants[tool.grant_id] = tool
                else:
                    edges[tool.semantic_id] = tool
        _emit_materialization_events(materialization_trace_sink, ir, plan)
        return NativeAgentGraph(
            agents=FrozenMap(agents),
            input_types=input_types,
            output_types=output_types,
            implementations=implementations,
            grant_objects=FrozenMap(grants),
            composition_objects=FrozenMap(edges),
            context=context_runtime,
            environment_evidence=(),
            validation=validation,
        )
