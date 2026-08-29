"""Strands Agents SDK materializer."""

from __future__ import annotations

import asyncio
import copy
import importlib.metadata
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import TypeAdapter

from contract4agents.adapters._native_names import NativeNameRegistry
from contract4agents.adapters._strands import strands_planner_capabilities
from contract4agents.compiler import CompilerArtifacts
from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId
from contract4agents.materialization._configuration import (
    MISSING,
)
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._errors import (
    MaterializationError,
    MaterializationIssue,
)
from contract4agents.materialization._models import (
    GraphValidationEvidence,
    NativeAgentGraph,
)
from contract4agents.materialization._options import thaw_mapping
from contract4agents.materialization._strands_validation import (
    _input_schema,
    _output_schema,
    validate_strands_graph,
)
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
from contract4agents.runtime import EnvironmentProvider, EnvironmentRunRequest
from contract4agents.target_bindings import TargetBinding


@dataclass(frozen=True)
class NativeStrandsAgentDescription:
    """Provider-neutral facts read back from one native Strands agent."""

    native_name: str
    instructions: str
    model_identity: str
    output_type: type[object]
    tool_names: tuple[str, ...]
    approval_allowed_tools: tuple[str, ...] | None
    model_options: Mapping[str, object] | None = None
    model_observation_source: str = "native_readback"
    model_observed: bool = True
    output_observation_source: str = "generated_wrapper"
    approval_observation_source: str = "generated_wrapper"


@dataclass(frozen=True)
class NativeStrandsToolDescription:
    """Provider-neutral facts read back from one native Strands tool."""

    native_name: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    observation_source: str = "native_schema"


class StrandsSDK(Protocol):
    """Small injectable SDK surface used by the Strands materializer."""

    version: str

    def create_model(
        self,
        *,
        model: str,
        model_options: Mapping[str, object],
        factory: object | None,
    ) -> object: ...

    def create_agent(
        self,
        *,
        native_name: str,
        contract_name: str,
        instructions: str,
        model_identity: str,
        model: object,
        output_type: type[object],
        tools: tuple[object, ...],
        approval_allowed_tools: tuple[str, ...] | None,
    ) -> object: ...

    def create_function_tool(
        self,
        *,
        native_name: str,
        description: str,
        implementation: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object: ...

    def create_delegate_tool(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object: ...

    def create_isolated_delegate_tool(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
        isolation_id: SemanticId,
        requested_dimensions: FrozenMap[str, str],
        declared_capabilities: tuple[str, ...],
        environment: EnvironmentProvider,
    ) -> object: ...

    def attach(self, agent: object, *, tools: tuple[object, ...]) -> None: ...

    def describe_agent(self, agent: object) -> NativeStrandsAgentDescription: ...

    def describe_tool(self, tool: object) -> NativeStrandsToolDescription: ...

    def validate_result(self, agent: object, result: object) -> object: ...


class StrandsAgentsSDK:
    """Lazy concrete facade over the optional Strands Agents SDK."""

    def __init__(self) -> None:
        try:
            self.version = importlib.metadata.version("strands-agents")
        except importlib.metadata.PackageNotFoundError:
            self.version = "unavailable"
        self._agent_metadata: dict[
            int,
            tuple[str, type[object], tuple[str, ...] | None, bool],
        ] = {}
        self._factory_model_ids: set[int] = set()
        self._tool_metadata: dict[int, NativeStrandsToolDescription] = {}

    def create_model(
        self,
        *,
        model: str,
        model_options: Mapping[str, object],
        factory: object | None,
    ) -> object:
        Model, BedrockModel = _strands_model_types()
        options = thaw_mapping(model_options)
        if factory is not None:
            if not callable(factory):
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT312",
                            "Resolved Strands model factory is not callable",
                        ),
                    )
                )
            try:
                native_model = factory(model=model, options=options)
            except Exception as exc:  # noqa: BLE001 - host factory boundary.
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT312",
                            f"Strands model factory failed for `{model}`: {exc}",
                        ),
                    )
                ) from exc
            if not isinstance(native_model, Model):
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT314",
                            "Strands model factory must return `strands.models.Model`",
                        ),
                    )
                )
            self._factory_model_ids.add(id(native_model))
            return native_model
        try:
            return BedrockModel(model_id=model, **options)
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT312",
                        f"Invalid Strands Bedrock model options for `{model}`: {exc}",
                    ),
                )
            ) from exc

    def create_agent(
        self,
        *,
        native_name: str,
        contract_name: str,
        instructions: str,
        model_identity: str,
        model: object,
        output_type: type[object],
        tools: tuple[object, ...],
        approval_allowed_tools: tuple[str, ...] | None,
    ) -> object:
        try:
            from strands import Agent
            from strands.vended_interventions.hitl import HumanInTheLoop
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            raise _missing_strands_error() from exc

        interventions: list[object] | None = None
        if approval_allowed_tools is not None:
            interventions = [HumanInTheLoop(allowed_tools=list(approval_allowed_tools))]
        try:
            native_agent = cast(Any, Agent)(
                model=model,
                tools=list(tools),
                system_prompt=instructions,
                structured_output_model=output_type,
                callback_handler=None,
                agent_id=native_name,
                name=native_name,
                description=contract_name,
                interventions=interventions,
                session_manager=None,
                retry_strategy=None,
            )
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT313",
                        f"Could not create Strands agent `{contract_name}`: {exc}",
                    ),
                )
            ) from exc
        self._agent_metadata[id(native_agent)] = (
            model_identity,
            output_type,
            approval_allowed_tools,
            id(model) in self._factory_model_ids,
        )
        return native_agent

    def create_function_tool(
        self,
        *,
        native_name: str,
        description: str,
        implementation: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object:
        try:
            from strands.tools import PythonAgentTool
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            raise _missing_strands_error() from exc
        if not callable(implementation):
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT315",
                        f"Implementation for Strands tool `{native_name}` is not callable",
                    ),
                )
            )
        input_schema = _input_schema(input_type)
        output_schema = _output_schema(output_adapter)
        input_adapter = TypeAdapter(input_type) if input_type is not None else None

        async def invoke(tool_use: Mapping[str, object], **_state: object) -> object:
            raw_input = tool_use.get("input", {})
            values = _validated_input(input_adapter, raw_input)
            if inspect.iscoroutinefunction(implementation):
                result = await cast(Any, implementation)(**values)
            else:
                result = await asyncio.to_thread(
                    cast(Any, implementation),
                    **values,
                )
                if inspect.isawaitable(result):
                    result = await result
            output = _validated_output(output_adapter, result)
            return _tool_result(str(tool_use.get("toolUseId", "unknown")), output)

        tool_spec = {
            "name": native_name,
            "description": description or native_name,
            "inputSchema": copy.deepcopy(dict(input_schema)),
            "outputSchema": copy.deepcopy(dict(output_schema)),
        }
        native_tool = cast(Any, PythonAgentTool)(
            native_name,
            tool_spec,
            invoke,
        )
        self._remember_tool(
            native_tool,
            native_name=native_name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        return native_tool

    def create_delegate_tool(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object:
        return self._create_typed_delegate(
            native_name=native_name,
            description=description,
            child=child,
            input_type=input_type,
            output_adapter=output_adapter,
            isolation=None,
        )

    def create_isolated_delegate_tool(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
        isolation_id: SemanticId,
        requested_dimensions: FrozenMap[str, str],
        declared_capabilities: tuple[str, ...],
        environment: EnvironmentProvider,
    ) -> object:
        return self._create_typed_delegate(
            native_name=native_name,
            description=description,
            child=child,
            input_type=input_type,
            output_adapter=output_adapter,
            isolation=(
                isolation_id,
                requested_dimensions,
                declared_capabilities,
                environment,
            ),
        )

    def _create_typed_delegate(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
        isolation: tuple[
            SemanticId,
            FrozenMap[str, str],
            tuple[str, ...],
            EnvironmentProvider,
        ]
        | None,
    ) -> object:
        try:
            from strands import ToolContext, tool
            from strands.interrupt import InterruptException
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            raise _missing_strands_error() from exc

        native_child = cast(Any, child)
        inner = native_child.as_tool(
            name=native_name,
            description=description or None,
            preserve_context=False,
        )
        input_schema = _input_schema(input_type)
        output_schema = _output_schema(output_adapter)
        input_adapter = TypeAdapter(input_type) if input_type is not None else None

        context_name = _tool_context_name(input_type)

        async def invoke(**values: object) -> object:
            tool_context = values.pop(context_name, None)
            payload = _validated_input(input_adapter, values)
            invocation_state = dict(cast(Any, tool_context).invocation_state) if tool_context is not None else {}
            tool_use_id = (
                str(cast(Any, tool_context).tool_use.get("toolUseId", "unknown"))
                if tool_context is not None
                else "unknown"
            )
            if isolation is not None:
                isolation_id, dimensions, capabilities, environment = isolation
                request = EnvironmentRunRequest(
                    isolation_id=isolation_id,
                    input_payload=payload,
                    requested_dimensions=dimensions,
                    declared_capabilities=capabilities,
                    parent_context=invocation_state.get("context"),
                )

                async def invoke_isolated(
                    child_input: object,
                    _run_context: object | None,
                    _state: object | None,
                    _capabilities: tuple[str, ...] | None,
                ) -> object:
                    if not isinstance(child_input, Mapping):
                        raise TypeError("Strands isolated delegate input must be an object")
                    return await _collect_delegate_output(
                        inner,
                        native_name,
                        tool_use_id,
                        dict(child_input),
                        invocation_state,
                        output_adapter,
                    )

                result = await environment.run(request, invoke_isolated)
                return _validated_output(output_adapter, result)

            inner_use = _delegate_tool_use(
                native_name,
                tool_use_id,
                payload,
            )
            async for event in cast(Any, inner).stream(
                inner_use,
                invocation_state,
            ):
                if not isinstance(event, Mapping):
                    continue
                if "tool_interrupt_event" in event:
                    interrupt_data = event["tool_interrupt_event"]
                    if not isinstance(interrupt_data, Mapping):
                        raise RuntimeError("Strands delegate returned an invalid interrupt")
                    interrupts = interrupt_data.get("interrupts")
                    if not isinstance(interrupts, list | tuple) or not interrupts:
                        raise RuntimeError("Strands delegate returned an empty interrupt")
                    raise InterruptException(interrupts[0])
                if "tool_result" not in event:
                    continue
                result = cast(Mapping[str, object], event["tool_result"])
                if result.get("status") == "error":
                    raise RuntimeError("Strands delegate returned an error")
                return _validated_output(
                    output_adapter,
                    _tool_result_value(result),
                )
            raise RuntimeError("Strands delegate did not produce a terminal result")

        _set_tool_signature(
            invoke,
            input_type,
            context_name=context_name,
            tool_context_type=ToolContext,
        )
        decorator = cast(Any, tool)(
            name=native_name,
            description=description or native_name,
            inputSchema=dict(input_schema),
            context=context_name,
        )
        native_tool = decorator(invoke)
        tool_spec = dict(cast(Any, native_tool).tool_spec)
        tool_spec["outputSchema"] = dict(output_schema)
        cast(Any, native_tool).tool_spec = tool_spec
        self._remember_tool(
            native_tool,
            native_name=native_name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        return native_tool

    def attach(self, agent: object, *, tools: tuple[object, ...]) -> None:
        native_agent = cast(Any, agent)
        existing = set(native_agent.tool_names)
        for tool in tools:
            description = self.describe_tool(tool)
            if description.native_name in existing:
                continue
            native_agent.tool_registry.register_dynamic_tool(tool)
            existing.add(description.native_name)

    def describe_agent(self, agent: object) -> NativeStrandsAgentDescription:
        native_agent = cast(Any, agent)
        try:
            model_identity, output_type, allowed, factory_boundary = self._agent_metadata[id(agent)]
        except KeyError as exc:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT318",
                        "Strands agent was not created by this SDK facade",
                    ),
                )
            ) from exc
        native_name = getattr(native_agent, "name", MISSING)
        native_prompt = getattr(native_agent, "system_prompt", MISSING)
        native_names = getattr(native_agent, "tool_names", MISSING)
        if native_name is MISSING or native_prompt is MISSING or native_names is MISSING:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT318",
                        "Strands agent is missing a required public property",
                    ),
                )
            )
        native_model = getattr(native_agent, "model", MISSING)
        if native_model is MISSING:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT318",
                        "Strands agent is missing its public model property",
                    ),
                )
            )
        model_config = getattr(native_model, "config", MISSING)
        observed_model = model_config.get("model_id") if isinstance(model_config, Mapping) else MISSING
        model_source = "adapter_boundary" if factory_boundary else "native_readback"
        if isinstance(observed_model, str):
            model_source = "native_readback"
        if isinstance(observed_model, str):
            model_identity = observed_model
        return NativeStrandsAgentDescription(
            native_name=str(native_name),
            instructions=str(native_prompt or ""),
            model_identity=model_identity,
            output_type=output_type,
            tool_names=tuple(cast(Any, native_names)),
            approval_allowed_tools=allowed,
            model_options=(dict(model_config) if isinstance(model_config, Mapping) else None),
            model_observation_source=model_source,
            model_observed=isinstance(observed_model, str),
        )

    def describe_tool(self, tool: object) -> NativeStrandsToolDescription:
        native_tool = cast(Any, tool)
        public_spec = getattr(native_tool, "tool_spec", MISSING)
        if isinstance(public_spec, Mapping):
            input_schema = public_spec.get("inputSchema")
            output_schema = public_spec.get("outputSchema")
            name = public_spec.get("name", getattr(native_tool, "name", MISSING))
            if isinstance(name, str) and isinstance(input_schema, Mapping) and isinstance(output_schema, Mapping):
                return NativeStrandsToolDescription(
                    native_name=name,
                    description=str(public_spec.get("description", "")),
                    input_schema=dict(input_schema),
                    output_schema=dict(output_schema),
                    observation_source="native_schema",
                )
        try:
            return self._tool_metadata[id(tool)]
        except KeyError as exc:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT318",
                        "Strands tool was not created by this SDK facade",
                    ),
                )
            ) from exc

    def validate_result(self, agent: object, result: object) -> object:
        description = self.describe_agent(agent)
        structured_output = getattr(result, "structured_output", None)
        if structured_output is None:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT324",
                        (f"Strands agent `{description.native_name}` did not produce required structured output"),
                    ),
                )
            )
        try:
            return TypeAdapter(description.output_type).validate_python(structured_output)
        except Exception as exc:  # noqa: BLE001 - provider result boundary.
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT324",
                        (f"Strands agent `{description.native_name}` produced invalid structured output: {exc}"),
                    ),
                )
            ) from exc

    def _remember_tool(
        self,
        tool: object,
        *,
        native_name: str,
        description: str,
        input_schema: Mapping[str, object],
        output_schema: Mapping[str, object],
    ) -> None:
        self._tool_metadata[id(tool)] = NativeStrandsToolDescription(
            native_name=native_name,
            description=description,
            input_schema=copy.deepcopy(dict(input_schema)),
            output_schema=copy.deepcopy(dict(output_schema)),
            observation_source="generated_wrapper",
        )


class StrandsMaterializationProvider:
    """Construct and validate one native Strands agent graph."""

    adapter = "strands"

    def __init__(self, sdk: StrandsSDK | None = None) -> None:
        self.sdk = sdk or StrandsAgentsSDK()

    def planner_capabilities(
        self,
        environment: EnvironmentProvider | None,
    ) -> PlannerCapabilities:
        base = strands_planner_capabilities()
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

    def validate_result(self, agent: object, result: object) -> object:
        """Fail closed unless a host-run invocation returns contract output."""

        return self.sdk.validate_result(agent, result)

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
        _require_supported_graph(ir)
        names = NativeNameRegistry()
        agent_names = {
            identifier: _assign_name(names, "agent", identifier, agent.name) for identifier, agent in ir.agents.items()
        }
        capability_names = {
            identifier: _assign_name(
                names,
                "tool",
                identifier,
                capability.name,
            )
            for identifier, capability in ir.capabilities.items()
            if capability.kind == "tool"
        }
        edge_names = {
            identifier: _assign_name(names, "delegate", identifier, edge.name)
            for identifier, edge in ir.composition.items()
        }

        agents: dict[SemanticId, object] = {}
        grants: dict[SemanticId, object] = {}
        base_tools: dict[SemanticId, list[object]] = {identifier: [] for identifier in ir.agents}

        for agent_id, agent in ir.agents.items():
            for grant_id in agent.grant_ids:
                grant = ir.grants[grant_id]
                if grant.availability == "denied" or grant.capability_id.kind != "tool":
                    continue
                capability = ir.capabilities[grant.capability_id]
                input_type = build_parameter_model(
                    f"{agent.name}_{capability.name}_Input",
                    capability.parameters,
                    output_types,
                )
                native_tool = self.sdk.create_function_tool(
                    native_name=capability_names[capability.id],
                    description=capability.description,
                    implementation=implementations[capability.id],
                    input_type=input_type,
                    output_adapter=type_adapter_for(
                        capability.output_type,
                        output_types,
                    ),
                )
                grants[grant_id] = native_tool
                base_tools[agent_id].append(native_tool)

            agent_plan = plan.agents[agent_id]
            options = dict(agent_plan.model_options)
            factory_locator = options.pop("model_factory", None)
            options.pop("environment", None)
            factory = None
            if factory_locator is not None:
                factory = implementations.get(agent_id)
                if factory is None:
                    raise MaterializationError(
                        (
                            MaterializationIssue(
                                "MAT316",
                                (f"No resolved model factory is available for Strands agent `{agent.name}`"),
                                agent_id,
                            ),
                        )
                    )
            native_model = self.sdk.create_model(
                model=agent_plan.model,
                model_options=options,
                factory=factory,
            )
            agent_output_type = cast(
                type[object],
                output_type_for(agent.output_type, output_types),
            )
            approval_grants = [
                ir.grants[grant_id]
                for grant_id in agent.grant_ids
                if ir.grants[grant_id].availability == "enabled"
                and ir.grants[grant_id].capability_id.kind == "tool"
                and ir.grants[grant_id].authorization == "approval_required"
            ]
            approval_allowed_tools: tuple[str, ...] | None = None
            if approval_grants:
                preapproved = [
                    capability_names[grant.capability_id]
                    for grant_id in agent.grant_ids
                    if (grant := ir.grants[grant_id]).availability == "enabled"
                    and grant.capability_id.kind == "tool"
                    and grant.authorization != "approval_required"
                ]
                delegates = [
                    edge_names[edge.id] for edge in ir.composition.values() if edge.source_agent_id == agent_id
                ]
                approval_allowed_tools = tuple(sorted(set(preapproved + delegates + [agent_output_type.__name__])))
            agents[agent_id] = self.sdk.create_agent(
                native_name=agent_names[agent_id],
                contract_name=agent.name,
                instructions=artifacts.instructions[agent.name],
                model_identity=agent_plan.model,
                model=native_model,
                output_type=agent_output_type,
                tools=tuple(base_tools[agent_id]),
                approval_allowed_tools=approval_allowed_tools,
            )

        edges: dict[SemanticId, object] = {}
        edge_tools: dict[SemanticId, list[object]] = {identifier: [] for identifier in ir.agents}
        for edge_id, edge in ir.composition.items():
            child_ir = ir.agents[edge.target_agent_id]
            child = agents[edge.target_agent_id]
            input_type = input_types[edge.target_agent_id]
            output_adapter = type_adapter_for(
                child_ir.output_type,
                output_types,
            )
            if edge.isolation_id is None:
                native_edge = self.sdk.create_delegate_tool(
                    native_name=edge_names[edge_id],
                    description=edge.description,
                    child=child,
                    input_type=input_type,
                    output_adapter=output_adapter,
                )
            else:
                if environment is None:
                    raise MaterializationError(
                        (
                            MaterializationIssue(
                                "MAT317",
                                "Isolated Strands delegate has no environment provider",
                                edge_id,
                            ),
                        )
                    )
                isolation_plan = plan.isolation[edge.isolation_id]
                dimensions = FrozenMap((name, value.requested) for name, value in isolation_plan.dimensions.items())
                declared = tuple(
                    str(ir.grants[grant_id].capability_id)
                    for grant_id in child_ir.grant_ids
                    if ir.grants[grant_id].availability == "enabled"
                )
                native_edge = self.sdk.create_isolated_delegate_tool(
                    native_name=edge_names[edge_id],
                    description=edge.description,
                    child=child,
                    input_type=input_type,
                    output_adapter=output_adapter,
                    isolation_id=edge.isolation_id,
                    requested_dimensions=dimensions,
                    declared_capabilities=declared,
                    environment=environment,
                )
            edges[edge_id] = native_edge
            edge_tools[edge.source_agent_id].append(native_edge)

        for agent_id, native_agent in agents.items():
            self.sdk.attach(
                native_agent,
                tools=tuple(base_tools[agent_id] + edge_tools[agent_id]),
            )

        schema_conformance, configuration_conformance = validate_strands_graph(
            self.sdk,
            ir,
            artifacts,
            plan,
            agents,
            grants,
            edges,
            input_types,
            output_types,
            agent_names,
            capability_names,
            edge_names,
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
            composition_objects=FrozenMap((identifier, edges[identifier]) for identifier in ir.composition),
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


def _require_supported_graph(ir: CanonicalIR) -> None:
    issues: list[MaterializationIssue] = []
    for grant in ir.grants.values():
        if grant.isolation_id is not None and grant.availability == "enabled":
            issues.append(
                MaterializationIssue(
                    "MAT321",
                    "Strands tool grants cannot cross a declared isolation environment",
                    grant.id,
                )
            )
    for edge in ir.composition.values():
        if edge.mode != "delegate":
            issues.append(
                MaterializationIssue(
                    "MAT322",
                    "Strands materialization does not implement handoffs",
                    edge.id,
                )
            )
        elif edge.history != "none":
            issues.append(
                MaterializationIssue(
                    "MAT323",
                    "Strands delegates require `history = none`",
                    edge.id,
                )
            )
    if issues:
        raise MaterializationError(tuple(issues))


def _assign_name(
    registry: NativeNameRegistry,
    kind: str,
    identifier: SemanticId,
    display_name: str,
) -> str:
    try:
        return registry.assign(kind, identifier, display_name)
    except ValueError as exc:
        raise MaterializationError(
            (
                MaterializationIssue(
                    "MAT319",
                    str(exc),
                    identifier,
                ),
            )
        ) from exc


def _tool_context_name(input_type: type[object] | None) -> str:
    fields = set(cast(Any, input_type).model_fields) if input_type is not None else set()
    name = "__c4a_tool_context"
    while name in fields:
        name = f"_{name}"
    return name


def _set_tool_signature(
    implementation: object,
    input_type: type[object] | None,
    *,
    context_name: str,
    tool_context_type: object,
) -> None:
    parameters: list[inspect.Parameter] = []
    annotations: dict[str, object] = {}
    if input_type is not None:
        for name, field in cast(Any, input_type).model_fields.items():
            default = inspect.Parameter.empty if field.is_required() else field.default
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=field.annotation,
                )
            )
            annotations[name] = field.annotation
    parameters.append(
        inspect.Parameter(
            context_name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=tool_context_type,
        )
    )
    annotations[context_name] = tool_context_type
    annotations["return"] = object
    native = cast(Any, implementation)
    native.__signature__ = inspect.Signature(parameters)
    native.__annotations__ = annotations


def _validated_input(
    adapter: TypeAdapter[Any] | None,
    value: object,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Strands tool input must be an object")
    if adapter is None:
        if value:
            raise ValueError("Strands tool does not accept input parameters")
        return {}
    parsed = adapter.validate_python(value)
    dumped = adapter.dump_python(parsed, mode="json")
    if not isinstance(dumped, dict):
        raise TypeError("Validated Strands tool input must be an object")
    return cast(dict[str, object], dumped)


def _validated_output(adapter: TypeAdapter[Any], value: object) -> object:
    parsed = adapter.validate_python(value)
    return adapter.dump_python(parsed, mode="json")


def _tool_result(tool_use_id: str, output: object) -> dict[str, object]:
    return {
        "toolUseId": tool_use_id,
        "status": "success",
        "content": [{"json": output}],
    }


def _delegate_tool_use(
    native_name: str,
    tool_use_id: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    return {
        "name": native_name,
        "toolUseId": tool_use_id,
        "input": {
            "input": json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        },
    }


def _tool_result_value(result: Mapping[str, object]) -> object:
    content = result.get("content")
    if not isinstance(content, list | tuple) or not content:
        raise ValueError("Strands delegate returned no content")
    first = content[0]
    if not isinstance(first, Mapping):
        raise ValueError("Strands delegate returned invalid content")
    if "json" in first:
        return first["json"]
    text = first.get("text")
    if not isinstance(text, str):
        raise ValueError("Strands delegate returned neither JSON nor text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Strands delegate returned non-JSON text") from exc


async def _collect_delegate_output(
    inner: object,
    native_name: str,
    tool_use_id: str,
    payload: Mapping[str, object],
    invocation_state: dict[str, Any],
    output_adapter: TypeAdapter[Any],
) -> object:
    try:
        from strands.interrupt import InterruptException
    except Exception as exc:  # noqa: BLE001 - optional provider boundary.
        raise _missing_strands_error() from exc
    inner_use = _delegate_tool_use(native_name, tool_use_id, payload)
    async for event in cast(Any, inner).stream(inner_use, invocation_state):
        if not isinstance(event, Mapping):
            continue
        if "tool_interrupt_event" in event:
            interrupt_data = event["tool_interrupt_event"]
            if not isinstance(interrupt_data, Mapping):
                raise RuntimeError("Strands delegate returned an invalid interrupt")
            interrupts = interrupt_data.get("interrupts")
            if not isinstance(interrupts, list | tuple) or not interrupts:
                raise RuntimeError("Strands delegate returned an empty interrupt")
            raise InterruptException(interrupts[0])
        if "tool_result" not in event:
            continue
        result = cast(Mapping[str, object], event["tool_result"])
        if result.get("status") == "error":
            raise RuntimeError("Isolated Strands delegate returned an error")
        return _validated_output(output_adapter, _tool_result_value(result))
    raise RuntimeError("Isolated Strands delegate did not produce a terminal result")


def _strands_model_types() -> tuple[type[Any], type[Any]]:
    try:
        from strands.models import BedrockModel, Model
    except Exception as exc:  # noqa: BLE001 - optional provider boundary.
        raise _missing_strands_error() from exc
    return Model, BedrockModel


def _missing_strands_error() -> MaterializationError:
    return MaterializationError(
        (
            MaterializationIssue(
                "MAT311",
                ("strands-agents is not installed; install `contract4agents[strands]`"),
            ),
        )
    )


__all__ = [
    "NativeStrandsAgentDescription",
    "NativeStrandsToolDescription",
    "StrandsAgentsSDK",
    "StrandsMaterializationProvider",
    "StrandsSDK",
]
