"""OpenAI Agents SDK materializer."""

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from contract4agents.adapters._openai import openai_planner_capabilities
from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.compiler import CompilerArtifacts
from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId, format_type_ref, freeze_json
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue
from contract4agents.materialization._models import (
    GraphValidationEvidence,
    NativeAgentGraph,
)
from contract4agents.materialization._tracing import MaterializationTraceEvent, TraceSink
from contract4agents.materialization._types import build_parameter_model, output_type_for
from contract4agents.planning import MaterializationPlan, PlannerCapabilities
from contract4agents.runtime import (
    EnvironmentProvider,
    EnvironmentRunRequest,
)
from contract4agents.target_bindings import BindingEntry, TargetBinding


@dataclass(frozen=True)
class NativeAgentDescription:
    name: str
    instructions: str
    model: str
    output_type: type[object]
    tools: tuple[object, ...]
    handoffs: tuple[object, ...]


class OpenAISDK(Protocol):
    """Small injectable surface used by the concrete OpenAI materializer."""

    version: str

    def create_agent(
        self,
        *,
        name: str,
        instructions: str,
        model: str,
        model_options: Mapping[str, object],
        output_type: type[object],
        tools: tuple[object, ...],
    ) -> object: ...

    def create_function_tool(
        self,
        *,
        name: str,
        description: str,
        implementation: object,
        requires_approval: bool,
    ) -> object: ...

    def create_hosted_tool(self, *, name: str, binding: BindingEntry) -> object: ...

    def create_delegate_tool(
        self,
        *,
        name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
    ) -> object: ...

    def create_isolated_delegate_tool(
        self,
        *,
        name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        isolation_id: SemanticId,
        requested_dimensions: FrozenMap[str, str],
        declared_capabilities: tuple[str, ...],
        environment: EnvironmentProvider,
    ) -> object: ...

    def create_handoff(
        self,
        *,
        name: str,
        description: str,
        child: object,
        history: str,
    ) -> object: ...

    def attach(self, agent: object, *, tools: tuple[object, ...], handoffs: tuple[object, ...]) -> None: ...

    def describe(self, agent: object) -> NativeAgentDescription: ...


class AgentsSDK:
    """Lazy concrete facade over the installed OpenAI Agents SDK."""

    def __init__(self) -> None:
        try:
            self.version = importlib.metadata.version("openai-agents")
        except importlib.metadata.PackageNotFoundError:
            self.version = "unavailable"

    def create_agent(
        self,
        *,
        name: str,
        instructions: str,
        model: str,
        model_options: Mapping[str, object],
        output_type: type[object],
        tools: tuple[object, ...],
    ) -> object:
        try:
            from agents import Agent, ModelSettings
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            raise MaterializationError(
                (MaterializationIssue("MAT301", "openai-agents is not installed"),)
            ) from exc
        options = dict(model_options)
        options.pop("environment", None)
        try:
            settings = cast(Any, ModelSettings)(**options) if options else None
        except TypeError as exc:
            raise MaterializationError(
                (MaterializationIssue("MAT302", f"Invalid OpenAI model options for `{name}`: {exc}"),)
            ) from exc
        kwargs: dict[str, object] = {
            "name": name,
            "instructions": instructions,
            "model": model,
            "output_type": output_type,
            "tools": list(tools),
        }
        if settings is not None:
            kwargs["model_settings"] = settings
        return cast(Any, Agent)(**kwargs)

    def create_function_tool(
        self,
        *,
        name: str,
        description: str,
        implementation: object,
        requires_approval: bool,
    ) -> object:
        from agents import function_tool

        if not callable(implementation):
            raise MaterializationError(
                (MaterializationIssue("MAT303", f"Implementation for `{name}` is not callable"),)
            )
        return function_tool(
            name_override=openai_tool_name(name),
            description_override=description or None,
            needs_approval=requires_approval,
        )(implementation)

    def create_hosted_tool(self, *, name: str, binding: BindingEntry) -> object:
        provider = binding.values.get("provider")
        tool = binding.values.get("tool") or binding.values.get("provider_tool")
        if provider != "openai" or tool != "web_search":
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT304",
                        f"OpenAI target binding `{name}` names unsupported hosted tool `{provider}:{tool}`",
                    ),
                )
            )
        from agents import WebSearchTool

        options = {
            key: value
            for key, value in binding.values.items()
            if key not in {"provider", "tool", "provider_tool"}
        }
        return cast(Any, WebSearchTool)(**options)

    def create_delegate_tool(
        self,
        *,
        name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
    ) -> object:
        native_child = cast(Any, child)
        return native_child.as_tool(
            tool_name=openai_tool_name(name),
            tool_description=description,
            parameters=input_type,
            include_input_schema=input_type is not None,
        )

    def create_isolated_delegate_tool(
        self,
        *,
        name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        isolation_id: SemanticId,
        requested_dimensions: FrozenMap[str, str],
        declared_capabilities: tuple[str, ...],
        environment: EnvironmentProvider,
    ) -> object:
        from agents import FunctionTool, Runner
        from pydantic import BaseModel, TypeAdapter

        if input_type is None:
            schema: dict[str, Any] = {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            }
            adapter: TypeAdapter[Any] | None = None
        else:
            if not issubclass(input_type, BaseModel):
                raise TypeError("Isolated delegate inputs must be Pydantic models")
            adapter = TypeAdapter(input_type)
            schema = adapter.json_schema()

        async def invoke_tool(context: object, input_json: str) -> object:
            payload = json.loads(input_json)
            if adapter is not None:
                parsed = adapter.validate_python(payload)
                payload = adapter.dump_python(parsed, mode="json")
            request = EnvironmentRunRequest(
                isolation_id=isolation_id,
                input_payload=payload,
                requested_dimensions=requested_dimensions,
                declared_capabilities=declared_capabilities,
                parent_context=getattr(context, "context", None),
            )

            async def invoke(
                child_input: object,
                run_context: object | None,
                _state: object | None,
                _capabilities: tuple[str, ...] | None,
            ) -> object:
                run_input = child_input if isinstance(child_input, str) else json.dumps(child_input)
                result = await cast(Any, Runner).run(
                    starting_agent=child,
                    input=run_input,
                    context=run_context,
                    session=None,
                    previous_response_id=None,
                    conversation_id=None,
                )
                return result.final_output

            return await environment.run(request, invoke)

        return FunctionTool(
            name=openai_tool_name(name),
            description=description,
            params_json_schema=schema,
            on_invoke_tool=invoke_tool,
            strict_json_schema=True,
        )

    def create_handoff(
        self,
        *,
        name: str,
        description: str,
        child: object,
        history: str,
    ) -> object:
        from agents import handoff

        input_filter = None
        if history == "none":
            def discard_history(data: object) -> object:
                return cast(Any, dataclasses.replace)(
                    data,
                    input_history=(),
                    pre_handoff_items=(),
                    new_items=(),
                    input_items=(),
                )

            input_filter = discard_history
        return cast(Any, handoff)(
            child,
            tool_name_override=openai_tool_name(name),
            tool_description_override=description,
            input_filter=input_filter,
            nest_handoff_history=history == "full",
        )

    def attach(self, agent: object, *, tools: tuple[object, ...], handoffs: tuple[object, ...]) -> None:
        native_agent = cast(Any, agent)
        native_agent.tools = list(tools)
        native_agent.handoffs = list(handoffs)

    def describe(self, agent: object) -> NativeAgentDescription:
        native_agent = cast(Any, agent)
        return NativeAgentDescription(
            name=str(native_agent.name),
            instructions=str(native_agent.instructions),
            model=str(native_agent.model),
            output_type=cast(type[object], native_agent.output_type),
            tools=tuple(native_agent.tools),
            handoffs=tuple(native_agent.handoffs),
        )


class OpenAIMaterializationProvider:
    adapter = "openai"

    def __init__(self, sdk: OpenAISDK | None = None) -> None:
        self.sdk = sdk or AgentsSDK()

    def planner_capabilities(self, environment: EnvironmentProvider | None) -> PlannerCapabilities:
        base = openai_planner_capabilities()
        isolation = environment.planning_support() if environment is not None else base.isolation
        return PlannerCapabilities.create(
            adapter=base.adapter,
            version=self.sdk.version,
            approval=base.approval,
            composition=base.composition,
            controls=base.controls,
            isolation=isolation,
            expected_telemetry=base.expected_telemetry,
        )

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
        isolated_grants = [grant for grant in ir.grants.values() if grant.isolation_id is not None]
        if isolated_grants:
            raise MaterializationError(
                tuple(
                    MaterializationIssue(
                        "MAT305",
                        "OpenAI tool grants cannot cross a declared isolation environment",
                        grant.id,
                    )
                    for grant in isolated_grants
                )
            )
        hosted_approval_grants = [
            grant
            for grant in ir.grants.values()
            if grant.availability == "enabled"
            and grant.authorization == "approval_required"
            and plan.bindings[grant.capability_id].execution == "provider_hosted"
        ]
        if hosted_approval_grants:
            raise MaterializationError(
                tuple(
                    MaterializationIssue(
                        "MAT306",
                        "OpenAI hosted tools do not expose Contract4Agents approval enforcement",
                        grant.id,
                    )
                    for grant in hosted_approval_grants
                )
            )
        unsupported_isolated_handoffs = [
            edge for edge in ir.composition.values()
            if edge.mode == "handoff" and edge.isolation_id is not None
        ]
        if unsupported_isolated_handoffs:
            raise MaterializationError(
                tuple(
                    MaterializationIssue(
                        "MAT307",
                        "OpenAI handoffs cannot cross a declared isolation environment",
                        edge.id,
                    )
                    for edge in unsupported_isolated_handoffs
                )
            )

        agents: dict[SemanticId, object] = {}
        grants: dict[SemanticId, object] = {}
        base_tools: dict[SemanticId, list[object]] = {identifier: [] for identifier in ir.agents}

        # Pass one: construct all agent shells and their directly granted tools.
        for agent_id, agent in ir.agents.items():
            for grant_id in agent.grant_ids:
                grant = ir.grants[grant_id]
                if grant.availability == "denied" or grant.capability_id.kind != "tool":
                    continue
                capability = ir.capabilities[grant.capability_id]
                binding = target.tools[capability.name]
                if plan.bindings[capability.id].execution == "provider_hosted":
                    native_tool = self.sdk.create_hosted_tool(name=capability.name, binding=binding)
                else:
                    native_tool = self.sdk.create_function_tool(
                        name=capability.name,
                        description=capability.description,
                        implementation=implementations[capability.id],
                        requires_approval=grant.authorization == "approval_required",
                    )
                grants[grant_id] = native_tool
                base_tools[agent_id].append(native_tool)

            agent_plan = plan.agents[agent_id]
            agents[agent_id] = self.sdk.create_agent(
                name=agent.name,
                instructions=artifacts.instructions[agent.name],
                model=agent_plan.model,
                model_options=agent_plan.model_options,
                output_type=output_type_for(agent.output_type, output_types),
                tools=tuple(base_tools[agent_id]),
            )

        # Pass two: now every child identity exists, wire delegate and handoff edges.
        edge_objects: dict[SemanticId, object] = {}
        edge_tools: dict[SemanticId, list[object]] = {identifier: [] for identifier in ir.agents}
        handoffs: dict[SemanticId, list[object]] = {identifier: [] for identifier in ir.agents}
        for edge_id, edge in ir.composition.items():
            child_ir = ir.agents[edge.target_agent_id]
            child = agents[edge.target_agent_id]
            input_type = build_parameter_model(
                f"{child_ir.name}Input",
                child_ir.parameters,
                output_types,
            )
            if edge.mode == "delegate":
                if edge.isolation_id is None:
                    native_edge = self.sdk.create_delegate_tool(
                        name=edge.name,
                        description=edge.description,
                        child=child,
                        input_type=input_type,
                    )
                else:
                    if environment is None:
                        raise MaterializationError(
                            (MaterializationIssue("MAT308", "Isolated delegate has no environment provider", edge.id),)
                        )
                    isolation_plan = plan.isolation[edge.isolation_id]
                    dimensions = FrozenMap(
                        (name, value.requested) for name, value in isolation_plan.dimensions.items()
                    )
                    declared = tuple(
                        str(ir.grants[grant_id].capability_id)
                        for grant_id in child_ir.grant_ids
                        if ir.grants[grant_id].availability == "enabled"
                    )
                    native_edge = self.sdk.create_isolated_delegate_tool(
                        name=edge.name,
                        description=edge.description,
                        child=child,
                        input_type=input_type,
                        isolation_id=edge.isolation_id,
                        requested_dimensions=dimensions,
                        declared_capabilities=declared,
                        environment=environment,
                    )
                edge_tools[edge.source_agent_id].append(native_edge)
            else:
                native_edge = self.sdk.create_handoff(
                    name=edge.name,
                    description=edge.description,
                    child=child,
                    history=edge.history,
                )
                handoffs[edge.source_agent_id].append(native_edge)
            edge_objects[edge_id] = native_edge

        for agent_id, native_agent in agents.items():
            self.sdk.attach(
                native_agent,
                tools=tuple(base_tools[agent_id] + edge_tools[agent_id]),
                handoffs=tuple(handoffs[agent_id]),
            )

        _validate_graph(
            self.sdk,
            ir,
            artifacts,
            plan,
            agents,
            grants,
            edge_objects,
            output_types,
        )
        _emit_materialization_events(trace_sink, ir, plan)
        evidence = (
            tuple(environment.enforcement_evidence(item) for item in plan.isolation.values())
            if environment is not None
            else ()
        )
        return NativeAgentGraph(
            agents=FrozenMap((identifier, agents[identifier]) for identifier in ir.agents),
            output_types=output_types,
            implementations=implementations,
            grant_objects=FrozenMap((identifier, grants[identifier]) for identifier in sorted(grants, key=str)),
            composition_objects=FrozenMap(
                (identifier, edge_objects[identifier]) for identifier in ir.composition
            ),
            context=context_runtime,
            environment_evidence=evidence,
            validation=GraphValidationEvidence(
                plan_digest=plan.plan_digest,
                agent_ids=tuple(plan.agents),
                grant_ids=tuple(plan.grants),
                composition_ids=tuple(plan.composition),
            ),
        )


def _validate_graph(
    sdk: OpenAISDK,
    ir: CanonicalIR,
    artifacts: CompilerArtifacts,
    plan: MaterializationPlan,
    agents: Mapping[SemanticId, object],
    grant_objects: Mapping[SemanticId, object],
    edge_objects: Mapping[SemanticId, object],
    output_types: FrozenMap[str, type[object]],
) -> None:
    issues: list[MaterializationIssue] = []
    for agent_id, agent_plan in plan.agents.items():
        native = sdk.describe(agents[agent_id])
        if native.name != agent_plan.name:
            issues.append(MaterializationIssue("MAT401", "Native agent name differs from plan", agent_id))
        if native.instructions != artifacts.instructions[agent_plan.name]:
            issues.append(
                MaterializationIssue("MAT406", "Native agent instructions differ from compiler artifact", agent_id)
            )
        if native.model != agent_plan.model:
            issues.append(MaterializationIssue("MAT402", "Native agent model differs from plan", agent_id))
        expected_output = output_type_for(agent_plan.output_type, output_types)
        if native.output_type is not expected_output:
            issues.append(MaterializationIssue("MAT403", "Native output type differs from plan", agent_id))

        expected_tools = [
            grant_objects[grant_id]
            for grant_id in ir.agents[agent_id].grant_ids
            if grant_id in grant_objects
        ] + [
            edge_objects[edge.id]
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "delegate"
        ]
        expected_handoffs = [
            edge_objects[edge.id]
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "handoff"
        ]
        if len(native.tools) != len(expected_tools) or any(
            all(item is not candidate for candidate in native.tools) for item in expected_tools
        ):
            issues.append(MaterializationIssue("MAT404", "Native tools differ from planned grants/edges", agent_id))
        if len(native.handoffs) != len(expected_handoffs) or any(
            all(item is not candidate for candidate in native.handoffs)
            for item in expected_handoffs
        ):
            issues.append(MaterializationIssue("MAT405", "Native handoffs differ from planned edges", agent_id))
    if issues:
        raise MaterializationError(tuple(issues))


def _emit_materialization_events(
    sink: TraceSink,
    ir: CanonicalIR,
    plan: MaterializationPlan,
) -> None:
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
    "AgentsSDK",
    "NativeAgentDescription",
    "OpenAIMaterializationProvider",
    "OpenAISDK",
]
