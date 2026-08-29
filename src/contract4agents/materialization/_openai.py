"""OpenAI Agents SDK materializer."""

from __future__ import annotations

import dataclasses
import importlib.metadata
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import BaseModel, TypeAdapter

from contract4agents.adapters._openai import openai_planner_capabilities
from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.compiler import CompilerArtifacts
from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId
from contract4agents.materialization._configuration import (
    MISSING,
    SAFE_OPTION_PATHS,
    configuration_evidence,
    flatten_mapping,
    read_public_path,
)
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue
from contract4agents.materialization._models import (
    ConfigurationConformanceEvidence,
    GraphValidationEvidence,
    NativeAgentGraph,
    SchemaConformanceEvidence,
)
from contract4agents.materialization._options import thaw_mapping
from contract4agents.materialization._tracing import (
    MaterializationTraceSink,
    _emit_materialization_events,
)
from contract4agents.materialization._types import (
    build_parameter_model,
    output_type_for,
    type_adapter_for,
)
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
    output_schema: Mapping[str, object]
    tools: tuple[object, ...]
    handoffs: tuple[object, ...]
    model_settings: object | None = None
    output_mode: str = "native"


@dataclass(frozen=True)
class NativeToolDescription:
    name: str
    input_schema: Mapping[str, object]
    needs_approval: bool | None = None


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
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
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

    def describe_tool(self, tool: object) -> NativeToolDescription: ...

    def input_schema(self, input_type: type[object] | None) -> Mapping[str, object]: ...

    def output_schema(self, output_type: type[object]) -> Mapping[str, object]: ...


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
            raise MaterializationError((MaterializationIssue("MAT301", "openai-agents is not installed"),)) from exc
        options = thaw_mapping(model_options)
        options.pop("environment", None)
        try:
            settings = cast(Any, ModelSettings)(**options) if options else None
        except (TypeError, ValueError) as exc:
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
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
        requires_approval: bool,
    ) -> object:
        from agents import FunctionTool

        if not callable(implementation):
            raise MaterializationError(
                (MaterializationIssue("MAT303", f"Implementation for `{name}` is not callable"),)
            )
        input_adapter = TypeAdapter(input_type) if input_type is not None else None

        async def invoke_tool(_context: object, input_json: str) -> object:
            payload = json.loads(input_json)
            if input_adapter is None:
                if payload != {}:
                    raise ValueError(f"Tool `{name}` does not accept parameters")
                arguments: dict[str, object] = {}
            else:
                parsed = input_adapter.validate_python(payload)
                if not isinstance(parsed, BaseModel):
                    raise TypeError(f"Tool `{name}` input did not produce a Pydantic model")
                arguments = parsed.model_dump()
            if inspect.iscoroutinefunction(implementation):
                result = await cast(Any, implementation)(**arguments)
            else:
                import asyncio

                result = await asyncio.to_thread(cast(Any, implementation), **arguments)
            return output_adapter.validate_python(result)

        return FunctionTool(
            name=openai_tool_name(name),
            description=description,
            params_json_schema=dict(self.input_schema(input_type)),
            on_invoke_tool=invoke_tool,
            strict_json_schema=True,
            needs_approval=requires_approval,
        )

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
            key: value for key, value in binding.values.items() if key not in {"provider", "tool", "provider_tool"}
        }
        try:
            return cast(Any, WebSearchTool)(**options)
        except TypeError as exc:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT304",
                        f"Invalid OpenAI web-search options for `{name}`: {exc}",
                    ),
                )
            ) from exc

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
        from agents import AgentOutputSchema

        native_agent = cast(Any, agent)
        try:
            output_type = cast(type[object], native_agent.output_type)
            name = str(native_agent.name)
            instructions = str(native_agent.instructions)
            model = str(native_agent.model)
            tools = tuple(native_agent.tools)
            handoffs = tuple(native_agent.handoffs)
        except (AttributeError, TypeError) as exc:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT310",
                        "OpenAI agent is missing a required public property",
                    ),
                )
            ) from exc
        return NativeAgentDescription(
            name=name,
            instructions=instructions,
            model=model,
            output_type=output_type,
            output_schema=cast(dict[str, object], AgentOutputSchema(output_type).json_schema()),
            tools=tools,
            handoffs=handoffs,
            model_settings=getattr(native_agent, "model_settings", None),
            output_mode="native",
        )

    def describe_tool(self, tool: object) -> NativeToolDescription:
        native_tool = cast(Any, tool)
        schema = getattr(native_tool, "params_json_schema", None)
        if not isinstance(schema, dict):
            raise MaterializationError(
                (MaterializationIssue("MAT309", "OpenAI tool does not expose a parameter schema"),)
            )
        native_name = getattr(native_tool, "name", None)
        if not isinstance(native_name, str) or not native_name:
            raise MaterializationError((MaterializationIssue("MAT309", "OpenAI tool does not expose a public name"),))
        return NativeToolDescription(
            name=native_name,
            input_schema=cast(dict[str, object], schema),
            needs_approval=(
                cast(bool, native_tool.needs_approval)
                if isinstance(getattr(native_tool, "needs_approval", None), bool)
                else None
            ),
        )

    def input_schema(self, input_type: type[object] | None) -> Mapping[str, object]:
        from agents.strict_schema import ensure_strict_json_schema

        schema = (
            cast(dict[str, object], cast(Any, input_type).model_json_schema())
            if input_type is not None
            else {"type": "object", "properties": {}, "additionalProperties": False}
        )
        return cast(dict[str, object], ensure_strict_json_schema(schema))

    def output_schema(self, output_type: type[object]) -> Mapping[str, object]:
        from agents import AgentOutputSchema

        return cast(dict[str, object], AgentOutputSchema(output_type).json_schema())


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
            expected_event_types=base.expected_event_types,
            mapping_resolver=base.mapping_resolver,
        )

    def build_graph(
        self,
        *,
        ir: CanonicalIR,
        artifacts: CompilerArtifacts,
        target: TargetBinding,
        plan: MaterializationPlan,
        implementations: FrozenMap[SemanticId, object],
        input_types: FrozenMap[SemanticId, type[object] | None],
        output_types: FrozenMap[str, type[object]],
        context_runtime: ContextRuntime,
        environment: EnvironmentProvider | None,
        materialization_trace_sink: MaterializationTraceSink,
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
            edge for edge in ir.composition.values() if edge.mode == "handoff" and edge.isolation_id is not None
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
                    input_type = build_parameter_model(
                        f"{agent.name}_{capability.name}_Input",
                        capability.parameters,
                        output_types,
                    )
                    native_tool = self.sdk.create_function_tool(
                        name=capability.name,
                        description=capability.description,
                        implementation=implementations[capability.id],
                        input_type=input_type,
                        output_adapter=type_adapter_for(capability.output_type, output_types),
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
            input_type = input_types[edge.target_agent_id]
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
                    dimensions = FrozenMap((name, value.requested) for name, value in isolation_plan.dimensions.items())
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

        schema_conformance, configuration_conformance = _validate_graph(
            self.sdk,
            ir,
            artifacts,
            plan,
            agents,
            grants,
            edge_objects,
            input_types,
            output_types,
        )
        _emit_materialization_events(materialization_trace_sink, ir, plan)
        evidence = (
            tuple(environment.enforcement_evidence(item) for item in plan.isolation.values())
            if environment is not None
            else ()
        )
        return NativeAgentGraph(
            agents=FrozenMap((identifier, agents[identifier]) for identifier in ir.agents),
            input_types=input_types,
            output_types=output_types,
            implementations=implementations,
            grant_objects=FrozenMap((identifier, grants[identifier]) for identifier in sorted(grants, key=str)),
            composition_objects=FrozenMap((identifier, edge_objects[identifier]) for identifier in ir.composition),
            context=context_runtime,
            environment_evidence=evidence,
            validation=GraphValidationEvidence(
                adapter=self.adapter,
                adapter_version=self.sdk.version,
                contract_digest=artifacts.contract_digest,
                plan_digest=plan.plan_digest,
                agent_ids=tuple(plan.agents),
                grant_ids=tuple(plan.grants),
                composition_ids=tuple(plan.composition),
                schema_conformance=schema_conformance,
                configuration_conformance=configuration_conformance,
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
    input_types: FrozenMap[SemanticId, type[object] | None],
    output_types: FrozenMap[str, type[object]],
) -> tuple[tuple[SchemaConformanceEvidence, ...], tuple[ConfigurationConformanceEvidence, ...]]:
    issues: list[MaterializationIssue] = []
    schema_conformance: list[SchemaConformanceEvidence] = []
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
        output_evidence = SchemaConformanceEvidence(
            semantic_id=agent_id,
            boundary="agent_output",
            declared_schema=dict(sdk.output_schema(expected_output)),
            materialized_schema=dict(native.output_schema),
        )
        schema_conformance.append(output_evidence)
        if not output_evidence.matches:
            issues.append(MaterializationIssue("MAT407", "Native output schema differs from contract", agent_id))

        expected_tools = [
            grant_objects[grant_id] for grant_id in ir.agents[agent_id].grant_ids if grant_id in grant_objects
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
            all(item is not candidate for candidate in native.handoffs) for item in expected_handoffs
        ):
            issues.append(MaterializationIssue("MAT405", "Native handoffs differ from planned edges", agent_id))
    for grant_id, native_tool in grant_objects.items():
        grant = ir.grants[grant_id]
        capability = ir.capabilities[grant.capability_id]
        if plan.bindings[capability.id].execution == "provider_hosted":
            continue
        input_type = build_parameter_model(
            f"{ir.agents[grant.agent_id].name}_{capability.name}_Input",
            capability.parameters,
            output_types,
        )
        native_tool_description = sdk.describe_tool(native_tool)
        evidence = SchemaConformanceEvidence(
            semantic_id=grant_id,
            boundary="tool_input",
            declared_schema=dict(sdk.input_schema(input_type)),
            materialized_schema=dict(native_tool_description.input_schema),
        )
        schema_conformance.append(evidence)
        if native_tool_description.name != openai_tool_name(capability.name) or not evidence.matches:
            issues.append(MaterializationIssue("MAT408", "Native tool schema differs from contract", grant_id))
    for edge_id, native_tool in edge_objects.items():
        edge = ir.composition[edge_id]
        if edge.mode != "delegate":
            continue
        input_type = input_types[edge.target_agent_id]
        if input_type is None:
            continue
        native_tool_description = sdk.describe_tool(native_tool)
        evidence = SchemaConformanceEvidence(
            semantic_id=edge_id,
            boundary="delegate_input",
            declared_schema=dict(sdk.input_schema(input_type)),
            materialized_schema=dict(native_tool_description.input_schema),
        )
        schema_conformance.append(evidence)
        if native_tool_description.name != openai_tool_name(edge.name) or not evidence.matches:
            issues.append(MaterializationIssue("MAT409", "Native delegate schema differs from contract", edge_id))
    configuration_conformance, configuration_issues = _validate_configuration(
        ir,
        plan,
        agents,
        grant_objects,
        edge_objects,
        input_types,
        output_types,
        sdk,
    )
    issues.extend(configuration_issues)
    if issues:
        raise MaterializationError(tuple(issues))
    return tuple(schema_conformance), configuration_conformance


def _validate_configuration(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    agents: Mapping[SemanticId, object],
    grant_objects: Mapping[SemanticId, object],
    edge_objects: Mapping[SemanticId, object],
    input_types: FrozenMap[SemanticId, type[object] | None],
    output_types: FrozenMap[str, type[object]],
    sdk: OpenAISDK,
) -> tuple[tuple[ConfigurationConformanceEvidence, ...], list[MaterializationIssue]]:
    """Read OpenAI's public Agent and tool properties and compare the plan."""

    records: list[ConfigurationConformanceEvidence] = []
    issues: list[MaterializationIssue] = []

    def add(record: ConfigurationConformanceEvidence, identifier: SemanticId) -> None:
        records.append(record)
        status = cast(Any, record).status
        if cast(Any, record).required and status != "passed":
            code = "MAT410" if status == "violated" else "MAT411"
            issues.append(
                MaterializationIssue(
                    code,
                    (
                        "Native OpenAI configuration differs from the materialization plan"
                        if status == "violated"
                        else "Required OpenAI configuration property cannot be read back"
                    ),
                    identifier,
                )
            )

    for agent_id, agent_plan in plan.agents.items():
        native = sdk.describe(agents[agent_id])
        agent_ir = ir.agents[agent_id]
        expected_output = output_type_for(agent_plan.output_type, output_types)
        expected_tools = [
            _planned_tool_name(
                plan,
                ir.capabilities[ir.grants[grant_id].capability_id].id,
                ir.capabilities[ir.grants[grant_id].capability_id].name,
            )
            for grant_id in agent_ir.grant_ids
            if grant_id in grant_objects
        ] + [
            _native_name(edge_objects[edge.id])
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "delegate"
        ]
        expected_handoffs = [
            _native_name(edge_objects[edge.id])
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "handoff"
        ]
        actual_tools = tuple(_native_name(item) for item in native.tools)
        actual_handoffs = tuple(_native_name(item) for item in native.handoffs)
        add(
            configuration_evidence(
                agent_id,
                "agent.name",
                agent_ir.name,
                native.name,
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.identity",
                {"semantic_id": str(agent_id), "name": agent_ir.name},
                {"semantic_id": str(agent_id), "name": native.name},
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.model",
                agent_plan.model,
                native.model,
            ),
            agent_id,
        )
        planned_options = thaw_mapping(agent_plan.model_options)
        planned_options.pop("environment", None)
        observed_settings: object = native.model_settings
        if observed_settings is None:
            observed_settings = getattr(agents[agent_id], "model_options", MISSING)
        observed_options: dict[str, object] = {}
        for path, planned_value in flatten_mapping(planned_options):
            observed = read_public_path(observed_settings, path)
            if observed is not MISSING:
                _set_nested(observed_options, path, observed)
            add(
                configuration_evidence(
                    agent_id,
                    f"agent.model_settings.{path}",
                    planned_value,
                    observed,
                    safe=path in SAFE_OPTION_PATHS,
                ),
                agent_id,
            )
        add(
            configuration_evidence(
                agent_id,
                "agent.model_options",
                planned_options,
                observed_options,
                safe=False,
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.output_type",
                expected_output.__qualname__,
                native.output_type.__qualname__,
            ),
            agent_id,
        )
        add(
            configuration_evidence(agent_id, "agent.output_mode", "native", native.output_mode),
            agent_id,
        )
        add(
            configuration_evidence(agent_id, "agent.tools", sorted(expected_tools), sorted(actual_tools)),
            agent_id,
        )
        add(
            configuration_evidence(agent_id, "agent.handoffs", sorted(expected_handoffs), sorted(actual_handoffs)),
            agent_id,
        )

    for grant_id, grant in plan.grants.items():
        native_tool = grant_objects.get(grant_id)
        capability = ir.capabilities[grant.capability_id]
        actual_name = _native_name(native_tool) if native_tool is not None else MISSING
        expected_name = _planned_tool_name(plan, capability.id, capability.name)
        add(
            configuration_evidence(
                grant_id,
                "grant.identity",
                {"name": expected_name, "capability": str(grant.capability_id)},
                {"name": actual_name, "capability": str(grant.capability_id)}
                if actual_name is not MISSING
                else MISSING,
                required=grant.availability == "enabled" and grant.capability_id.kind == "tool",
            ),
            grant_id,
        )
        expected_approval = grant.authorization == "approval_required"
        actual_approval: object = MISSING
        if native_tool is not None:
            try:
                description = sdk.describe_tool(native_tool)
            except MaterializationError:
                description = None
            actual_approval = description.needs_approval if description is not None else MISSING
            if actual_approval is None:
                actual_approval = getattr(
                    native_tool, "needs_approval", getattr(native_tool, "requires_approval", MISSING)
                )
        add(
            configuration_evidence(
                grant_id,
                "grant.approval",
                expected_approval,
                actual_approval,
                required=(
                    grant.availability == "enabled"
                    and grant.capability_id.kind == "tool"
                    and grant.authorization == "approval_required"
                ),
            ),
            grant_id,
        )

    for edge_id, edge in ir.composition.items():
        native_edge = edge_objects.get(edge_id)
        expected_name = (
            openai_tool_name(edge.name) if edge.mode == "delegate" or hasattr(native_edge, "tool_name") else edge.name
        )
        actual_name = _native_name(native_edge) if native_edge is not None else MISSING
        add(
            configuration_evidence(edge_id, "edge.identity", expected_name, actual_name),
            edge_id,
        )
        expected_input = input_types[edge.target_agent_id]
        expected_schema = (
            sdk.input_schema(expected_input)
            if edge.mode == "delegate"
            else {"mode": "handoff", "history": edge.history}
        )
        actual_schema = (
            sdk.describe_tool(native_edge).input_schema
            if native_edge is not None and edge.mode == "delegate"
            else ({"mode": "handoff", "history": edge.history} if native_edge is not None else MISSING)
        )
        add(
            configuration_evidence(
                edge_id,
                "edge.schema",
                expected_schema,
                actual_schema,
                source="native_schema",
                safe=False,
            ),
            edge_id,
        )
    return tuple(records), issues


def _native_name(value: object) -> str:
    for attr in ("name", "tool_name", "tool_name_override", "agent_name"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return type(value).__name__


def _planned_tool_name(plan: MaterializationPlan, capability_id: SemanticId, capability_name: str) -> str:
    binding = plan.bindings.get(capability_id)
    if binding is not None and binding.execution == "provider_hosted":
        provider_name = binding.locator.get("tool") or binding.locator.get("provider_tool")
        if isinstance(provider_name, str):
            return provider_name
    return openai_tool_name(capability_name)


def _set_nested(target: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    current = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


__all__ = [
    "AgentsSDK",
    "NativeAgentDescription",
    "NativeToolDescription",
    "OpenAIMaterializationProvider",
    "OpenAISDK",
]
