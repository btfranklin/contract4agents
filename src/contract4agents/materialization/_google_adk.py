"""Google Agent Development Kit materializer."""

from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, TypeAdapter

from contract4agents.adapters._google_adk import google_adk_planner_capabilities
from contract4agents.adapters._native_names import NativeNameRegistry
from contract4agents.compiler import CompilerArtifacts
from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._errors import (
    MaterializationError,
    MaterializationIssue,
)
from contract4agents.materialization._models import (
    GraphValidationEvidence,
    NativeAgentGraph,
    SchemaConformanceEvidence,
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
from contract4agents.target_bindings import BindingEntry, TargetBinding

OutputMode = Literal["native", "emulated"]
_ToolInvoker = Callable[[dict[str, object], object], Awaitable[object]]
_OutputValidationObserver = Callable[[str, bool], None]
_OUTPUT_VALIDATION_OBSERVER: ContextVar[_OutputValidationObserver | None] = (
    ContextVar(
        "contract4agents_google_adk_output_validation_observer",
        default=None,
    )
)


class GoogleADKOutputValidationError(ValueError):
    """An emulated ADK terminal output failed its contract schema."""


@dataclass(frozen=True)
class GoogleADKNativeAgentDescription:
    """Provider-neutral facts used to validate a native ADK agent."""

    semantic_name: str
    native_name: str
    instructions: str
    model: str
    input_type: type[object] | None
    output_type: type[object]
    output_mode: OutputMode
    tools: tuple[object, ...]


@dataclass(frozen=True)
class GoogleADKNativeToolDescription:
    native_name: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]


class GoogleADKSDK(Protocol):
    """Small injectable surface used by the Google ADK materializer."""

    version: str

    def create_agent(
        self,
        *,
        semantic_name: str,
        native_name: str,
        description: str,
        instructions: str,
        model: str,
        model_options: Mapping[str, object],
        model_factory: object | None,
        input_type: type[object] | None,
        output_type: type[object],
        output_mode: OutputMode,
        tools: tuple[object, ...],
    ) -> object: ...

    def create_function_tool(
        self,
        *,
        native_name: str,
        description: str,
        implementation: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
        requires_approval: bool,
    ) -> object: ...

    def create_google_search_tool(
        self,
        *,
        native_name: str,
        child_name: str,
        description: str,
        binding: BindingEntry,
        input_type: type[object],
        output_adapter: TypeAdapter[Any],
        requires_approval: bool,
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

    def describe(self, agent: object) -> GoogleADKNativeAgentDescription: ...

    def describe_tool(self, tool: object) -> GoogleADKNativeToolDescription: ...


class ADKSDK:
    """Lazy concrete facade over an installed Google ADK."""

    def __init__(self) -> None:
        try:
            self.version = importlib.metadata.version("google-adk")
        except importlib.metadata.PackageNotFoundError:
            self.version = "unavailable"
        self._descriptions: dict[int, GoogleADKNativeAgentDescription] = {}

    def create_agent(
        self,
        *,
        semantic_name: str,
        native_name: str,
        description: str,
        instructions: str,
        model: str,
        model_options: Mapping[str, object],
        model_factory: object | None,
        input_type: type[object] | None,
        output_type: type[object],
        output_mode: OutputMode,
        tools: tuple[object, ...],
    ) -> object:
        try:
            from google.adk.agents import LlmAgent
            from google.adk.models import BaseLlm
            from google.genai import types
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT321",
                        (
                            "google-adk is not installed; install "
                            "`contract4agents[google-adk]`"
                        ),
                    ),
                )
            ) from exc

        options = _thaw_mapping(model_options)
        options.pop("environment", None)
        options.pop("model_factory", None)
        native_model: object
        if model_factory is not None:
            if not callable(model_factory):
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT322",
                            f"Model factory for `{semantic_name}` is not callable",
                        ),
                    )
                )
            try:
                native_model = model_factory(model=model, options=dict(options))
            except Exception as exc:  # noqa: BLE001 - trusted host extension boundary.
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT323",
                            (
                                f"Google ADK model factory for `{semantic_name}` "
                                f"failed: {type(exc).__name__}: {exc}"
                            ),
                        ),
                    )
                ) from exc
            if not isinstance(native_model, BaseLlm):
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT324",
                            (
                                f"Google ADK model factory for `{semantic_name}` "
                                "must return `google.adk.models.BaseLlm`"
                            ),
                        ),
                    )
                )
        else:
            if not model.startswith("gemini-"):
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT325",
                            (
                                f"Native Google ADK agent `{semantic_name}` requires a "
                                "`gemini-*` model or `model_factory`"
                            ),
                        ),
                    )
                )
            native_model = model

        generate_config: object | None = None
        if model_factory is None:
            try:
                generate_config = cast(Any, types.GenerateContentConfig)(
                    **options
                )
            except (TypeError, ValueError) as exc:
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT326",
                            (
                                f"Invalid Google ADK model options for "
                                f"`{semantic_name}`: {exc}"
                            ),
                        ),
                    )
                ) from exc
        native_instructions = instructions
        if output_mode == "emulated":
            native_instructions = _with_structured_output_instruction(
                instructions,
                output_type,
            )
        terminal_validator = _terminal_output_validator(
            semantic_name,
            output_type,
        )
        try:
            agent = cast(Any, LlmAgent)(
                name=native_name,
                description=description,
                instruction=native_instructions,
                model=native_model,
                generate_content_config=generate_config,
                input_schema=input_type,
                output_schema=output_type if output_mode == "native" else None,
                tools=list(tools),
                after_model_callback=terminal_validator,
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
            )
        except (TypeError, ValueError) as exc:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT327",
                        f"Could not construct Google ADK agent `{semantic_name}`: {exc}",
                    ),
                )
            ) from exc
        self._descriptions[id(agent)] = GoogleADKNativeAgentDescription(
            semantic_name=semantic_name,
            native_name=native_name,
            instructions=instructions,
            model=model,
            input_type=input_type,
            output_type=output_type,
            output_mode=output_mode,
            tools=tuple(tools),
        )
        return agent

    def create_function_tool(
        self,
        *,
        native_name: str,
        description: str,
        implementation: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
        requires_approval: bool,
    ) -> object:
        if not callable(implementation):
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT328",
                        f"Implementation for `{native_name}` is not callable",
                    ),
                )
            )
        input_adapter = TypeAdapter(input_type) if input_type is not None else None

        async def invoke(args: dict[str, object], tool_context: object) -> object:
            del tool_context
            arguments = _validated_arguments(args, input_adapter)
            if inspect.iscoroutinefunction(implementation):
                result = implementation(**arguments)
            else:
                result = await asyncio.to_thread(implementation, **arguments)
            if inspect.isawaitable(result):
                result = await result
            return _validated_output(result, output_adapter)

        return _contract_tool(
            name=native_name,
            description=description,
            input_type=input_type,
            output_adapter=output_adapter,
            requires_approval=requires_approval,
            invoke=invoke,
        )

    def create_google_search_tool(
        self,
        *,
        native_name: str,
        child_name: str,
        description: str,
        binding: BindingEntry,
        input_type: type[object],
        output_adapter: TypeAdapter[Any],
        requires_approval: bool,
    ) -> object:
        try:
            from google.adk.agents import LlmAgent
            from google.adk.tools.agent_tool import AgentTool
            from google.adk.tools.google_search_tool import google_search
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT321",
                        (
                            "google-adk is not installed; install "
                            "`contract4agents[google-adk]`"
                        ),
                    ),
                )
            ) from exc
        model = binding.values.get("model")
        if (
            set(binding.values) != {"provider", "tool", "model"}
            or binding.values.get("provider") != "google_adk"
            or binding.values.get("tool") != "google_search"
            or not isinstance(model, str)
            or not model.startswith("gemini-2")
        ):
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT329",
                        (
                            f"Google Search binding `{native_name}` must declare only "
                            "`provider = \"google_adk\"`, `tool = \"google_search\"`, "
                            "and an explicit `gemini-2*` model"
                        ),
                    ),
                )
            )
        search_agent = cast(Any, LlmAgent)(
            name=child_name,
            description="Contract-bound Google Search agent",
            instruction=_load_prompt("search.md"),
            model=model,
            tools=[google_search],
            include_contents="none",
            mode="single_turn",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
        search_tool = cast(Any, AgentTool)(
            agent=search_agent,
            skip_summarization=True,
            include_plugins=True,
            propagate_grounding_metadata=True,
        )
        input_adapter = TypeAdapter(input_type)

        async def invoke(args: dict[str, object], tool_context: object) -> object:
            arguments = _validated_arguments(args, input_adapter)
            raw = await search_tool.run_async(
                args={"request": arguments["query"]},
                tool_context=tool_context,
            )
            return _validated_output(raw, output_adapter)

        return _contract_tool(
            name=native_name,
            description=description,
            input_type=input_type,
            output_adapter=output_adapter,
            requires_approval=requires_approval,
            invoke=invoke,
        )

    def create_delegate_tool(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object:
        isolated_child = _single_turn_child(child)
        input_adapter = TypeAdapter(input_type) if input_type is not None else None

        async def invoke(args: dict[str, object], tool_context: object) -> object:
            arguments = _validated_arguments(args, input_adapter)
            run_node = cast(Any, tool_context).run_node
            raw = await run_node(
                isolated_child,
                node_input=arguments,
                use_sub_branch=True,
            )
            return _validated_output(raw, output_adapter)

        return _contract_tool(
            name=native_name,
            description=description,
            input_type=input_type,
            output_adapter=output_adapter,
            requires_approval=False,
            invoke=invoke,
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
        isolated_child = _single_turn_child(child)
        input_adapter = TypeAdapter(input_type) if input_type is not None else None

        async def invoke(args: dict[str, object], tool_context: object) -> object:
            arguments = _validated_arguments(args, input_adapter)
            get_context = getattr(tool_context, "get_invocation_context", None)
            parent_context = get_context() if callable(get_context) else None
            request = EnvironmentRunRequest(
                isolation_id=isolation_id,
                input_payload=arguments,
                requested_dimensions=requested_dimensions,
                declared_capabilities=declared_capabilities,
                parent_context=parent_context,
                parent_state=getattr(tool_context, "state", None),
            )

            async def invoke_child(
                child_input: object,
                run_context: object | None,
                state: object | None,
                capabilities: tuple[str, ...] | None,
            ) -> object:
                del run_context, state, capabilities
                run_node = cast(Any, tool_context).run_node
                raw = await run_node(
                    isolated_child,
                    node_input=child_input,
                    use_sub_branch=True,
                )
                return _validated_output(raw, output_adapter)

            return await environment.run(request, invoke_child)

        return _contract_tool(
            name=native_name,
            description=description,
            input_type=input_type,
            output_adapter=output_adapter,
            requires_approval=False,
            invoke=invoke,
        )

    def attach(self, agent: object, *, tools: tuple[object, ...]) -> None:
        native_agent = cast(Any, agent)
        native_agent.tools = list(tools)
        description = self._descriptions[id(agent)]
        self._descriptions[id(agent)] = GoogleADKNativeAgentDescription(
            semantic_name=description.semantic_name,
            native_name=description.native_name,
            instructions=description.instructions,
            model=description.model,
            input_type=description.input_type,
            output_type=description.output_type,
            output_mode=description.output_mode,
            tools=tools,
        )

    def describe(self, agent: object) -> GoogleADKNativeAgentDescription:
        try:
            return self._descriptions[id(agent)]
        except KeyError as exc:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT330",
                        "Google ADK agent was not created by this SDK facade",
                    ),
                )
            ) from exc

    def describe_tool(self, tool: object) -> GoogleADKNativeToolDescription:
        native_tool = cast(Any, tool)
        declaration_factory = getattr(native_tool, "_get_declaration", None)
        if not callable(declaration_factory):
            raise MaterializationError(
                (MaterializationIssue("MAT331", "Google ADK tool does not expose its declaration"),)
            )
        declaration = declaration_factory()
        input_schema = getattr(declaration, "parameters_json_schema", None)
        output_schema = getattr(declaration, "response_json_schema", None)
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise MaterializationError(
                (MaterializationIssue("MAT331", "Google ADK tool declaration has no JSON schemas"),)
            )
        return GoogleADKNativeToolDescription(
            native_name=str(declaration.name),
            input_schema=cast(dict[str, object], input_schema),
            output_schema=cast(dict[str, object], output_schema),
        )


class GoogleADKMaterializationProvider:
    """Construct a complete Google ADK-native graph from a reviewed plan."""

    adapter = "google_adk"

    def __init__(self, sdk: GoogleADKSDK | None = None) -> None:
        self.sdk = sdk or ADKSDK()

    def planner_capabilities(
        self,
        environment: EnvironmentProvider | None,
    ) -> PlannerCapabilities:
        base = google_adk_planner_capabilities()
        isolation = (
            environment.planning_support()
            if environment is not None
            else base.isolation
        )
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
        output_types: FrozenMap[str, type[object]],
        context_runtime: ContextRuntime,
        environment: EnvironmentProvider | None,
        materialization_trace_sink: MaterializationTraceSink,
    ) -> NativeAgentGraph:
        _reject_unsupported_runtime_shapes(ir)
        names = NativeNameRegistry()
        agents: dict[SemanticId, object] = {}
        grants: dict[SemanticId, object] = {}
        base_tools: dict[SemanticId, list[object]] = {
            identifier: [] for identifier in ir.agents
        }

        for agent_id, agent in ir.agents.items():
            for grant_id in agent.grant_ids:
                grant = ir.grants[grant_id]
                if (
                    grant.availability == "denied"
                    or grant.capability_id.kind != "tool"
                ):
                    continue
                capability = ir.capabilities[grant.capability_id]
                binding = target.tools[capability.name]
                tool_name = names.assign("tool", capability.id, capability.name)
                input_type = build_parameter_model(
                    f"{agent.name}_{capability.name.replace('.', '_')}Input",
                    capability.parameters,
                    output_types,
                )
                output_adapter = type_adapter_for(
                    capability.output_type,
                    output_types,
                )
                if plan.bindings[capability.id].execution == "provider_hosted":
                    if input_type is None:
                        raise MaterializationError(
                            (
                                MaterializationIssue(
                                    "MAT331",
                                    "Google Search requires a typed `query` input",
                                    capability.id,
                                ),
                            )
                        )
                    native_tool = self.sdk.create_google_search_tool(
                        native_name=tool_name,
                        child_name=names.assign(
                            "search",
                            capability.id,
                            f"{capability.name}_search",
                        ),
                        description=capability.description,
                        binding=binding,
                        input_type=input_type,
                        output_adapter=output_adapter,
                        requires_approval=(
                            grant.authorization == "approval_required"
                        ),
                    )
                else:
                    implementation = implementations.get(capability.id)
                    native_tool = self.sdk.create_function_tool(
                        native_name=tool_name,
                        description=capability.description,
                        implementation=implementation,
                        input_type=input_type,
                        output_adapter=output_adapter,
                        requires_approval=(
                            grant.authorization == "approval_required"
                        ),
                    )
                grants[grant_id] = native_tool
                base_tools[agent_id].append(native_tool)

            agent_plan = plan.agents[agent_id]
            output_mode = _output_mode(ir, plan, agent_id)
            agents[agent_id] = self.sdk.create_agent(
                semantic_name=agent.name,
                native_name=names.assign("agent", agent.id, agent.name),
                description=agent.description,
                instructions=artifacts.instructions[agent.name],
                model=agent_plan.model,
                model_options=agent_plan.model_options,
                model_factory=(
                    implementations.get(agent_id)
                    if "model_factory" in agent_plan.model_options
                    else None
                ),
                input_type=build_parameter_model(
                    f"{agent.name}Input",
                    agent.parameters,
                    output_types,
                ),
                output_type=output_type_for(agent.output_type, output_types),
                output_mode=output_mode,
                tools=tuple(base_tools[agent_id]),
            )

        edge_objects: dict[SemanticId, object] = {}
        edge_tools: dict[SemanticId, list[object]] = {
            identifier: [] for identifier in ir.agents
        }
        for edge_id, edge in ir.composition.items():
            if edge.mode != "delegate":
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT332",
                            "Google ADK materialization does not support handoffs",
                            edge.id,
                        ),
                    )
                )
            child_ir = ir.agents[edge.target_agent_id]
            input_type = build_parameter_model(
                f"{child_ir.name}Input",
                child_ir.parameters,
                output_types,
            )
            output_adapter = type_adapter_for(
                child_ir.output_type,
                output_types,
            )
            edge_name = names.assign("delegate", edge.id, edge.name)
            if edge.isolation_id is None:
                native_edge = self.sdk.create_delegate_tool(
                    native_name=edge_name,
                    description=edge.description,
                    child=agents[edge.target_agent_id],
                    input_type=input_type,
                    output_adapter=output_adapter,
                )
            else:
                if environment is None:
                    raise MaterializationError(
                        (
                            MaterializationIssue(
                                "MAT333",
                                "Isolated ADK delegate has no environment provider",
                                edge.id,
                            ),
                        )
                    )
                isolation_plan = plan.isolation[edge.isolation_id]
                dimensions = FrozenMap(
                    (name, value.requested)
                    for name, value in isolation_plan.dimensions.items()
                )
                declared = tuple(
                    str(ir.grants[grant_id].capability_id)
                    for grant_id in child_ir.grant_ids
                    if ir.grants[grant_id].availability == "enabled"
                )
                native_edge = self.sdk.create_isolated_delegate_tool(
                    native_name=edge_name,
                    description=edge.description,
                    child=agents[edge.target_agent_id],
                    input_type=input_type,
                    output_adapter=output_adapter,
                    isolation_id=edge.isolation_id,
                    requested_dimensions=dimensions,
                    declared_capabilities=declared,
                    environment=environment,
                )
            edge_objects[edge_id] = native_edge
            edge_tools[edge.source_agent_id].append(native_edge)

        for agent_id, native_agent in agents.items():
            self.sdk.attach(
                native_agent,
                tools=tuple(base_tools[agent_id] + edge_tools[agent_id]),
            )

        schema_conformance = _validate_graph(
            self.sdk,
            ir,
            artifacts,
            plan,
            agents,
            grants,
            edge_objects,
            output_types,
            names,
        )
        _emit_materialization_events(materialization_trace_sink, ir, plan)
        evidence = (
            tuple(
                environment.enforcement_evidence(item)
                for item in plan.isolation.values()
            )
            if environment is not None
            else ()
        )
        return NativeAgentGraph(
            agents=FrozenMap(
                (identifier, agents[identifier]) for identifier in ir.agents
            ),
            output_types=output_types,
            implementations=implementations,
            grant_objects=FrozenMap(
                (identifier, grants[identifier])
                for identifier in sorted(grants, key=str)
            ),
            composition_objects=FrozenMap(
                (identifier, edge_objects[identifier])
                for identifier in ir.composition
            ),
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
            ),
        )


def _contract_tool(
    *,
    name: str,
    description: str,
    input_type: type[object] | None,
    output_adapter: TypeAdapter[Any],
    requires_approval: bool,
    invoke: _ToolInvoker,
) -> object:
    try:
        from google.adk.tools.base_tool import BaseTool
        from google.genai import types
    except Exception as exc:  # noqa: BLE001 - optional provider boundary.
        raise MaterializationError(
            (
                MaterializationIssue(
                    "MAT321",
                    (
                        "google-adk is not installed; install "
                        "`contract4agents[google-adk]`"
                    ),
                ),
            )
        ) from exc

    input_schema = _input_schema(input_type)

    class ContractTool(BaseTool):
        def __init__(self) -> None:
            super().__init__(name=name, description=description)

        def _get_declaration(self) -> types.FunctionDeclaration:
            return types.FunctionDeclaration(
                name=name,
                description=description,
                parameters_json_schema=input_schema,
                response_json_schema=output_adapter.json_schema(),
            )

        async def run_async(
            self,
            *,
            args: dict[str, Any],
            tool_context: Any,
        ) -> Any:
            if requires_approval:
                confirmation = getattr(tool_context, "tool_confirmation", None)
                if confirmation is None:
                    tool_context.request_confirmation(
                        hint=f"Approve or reject the contract tool `{name}`.",
                        payload={"arguments": args, "tool": name},
                    )
                    actions = getattr(tool_context, "actions", None)
                    if actions is not None:
                        actions.skip_summarization = True
                    return {"error": "Tool execution is awaiting approval."}
                if not bool(getattr(confirmation, "confirmed", False)):
                    return {"error": "Tool execution was rejected."}
            return await invoke(dict(args), tool_context)

    return ContractTool()


def _input_schema(input_type: type[object] | None) -> Mapping[str, object]:
    if input_type is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    return cast(dict[str, object], cast(Any, input_type).model_json_schema())


def _single_turn_child(child: object) -> object:
    model_copy = getattr(child, "model_copy", None)
    if not callable(model_copy):
        return child
    return model_copy(
        update={
            "include_contents": "none",
            "mode": "single_turn",
            "disallow_transfer_to_parent": True,
            "disallow_transfer_to_peers": True,
        },
        deep=True,
    )


def _validated_arguments(
    args: Mapping[str, object],
    adapter: TypeAdapter[Any] | None,
) -> dict[str, object]:
    if adapter is None:
        if args:
            raise ValueError("This contract tool does not accept arguments")
        return {}
    validated = adapter.validate_python(args)
    if isinstance(validated, BaseModel):
        return cast(dict[str, object], validated.model_dump())
    if isinstance(validated, Mapping):
        return dict(validated)
    raise TypeError("Contract tool inputs must validate to an object")


def _validated_output(value: object, adapter: TypeAdapter[Any]) -> object:
    if isinstance(value, str):
        validated = adapter.validate_json(value)
    else:
        validated = adapter.validate_python(value)
    return adapter.dump_python(validated, mode="json")


def _with_structured_output_instruction(
    instructions: str,
    output_type: type[object],
) -> str:
    schema = cast(type[BaseModel], output_type).model_json_schema()
    rendered = _load_prompt("structured_output.md").replace(
        "{{OUTPUT_SCHEMA}}",
        json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )
    return f"{instructions.rstrip()}\n\n{rendered.strip()}\n"


def _terminal_output_validator(
    semantic_name: str,
    output_type: type[object],
) -> Callable[[object, object], None]:
    adapter = TypeAdapter(output_type)

    def validate_terminal_output(
        callback_context: object,
        llm_response: object,
    ) -> None:
        del callback_context
        response = cast(Any, llm_response)
        if response.partial is True or response.get_function_calls():
            return
        content = response.content
        parts = getattr(content, "parts", None) if content is not None else None
        text = "".join(
            str(part.text)
            for part in (parts or ())
            if getattr(part, "text", None) and not getattr(part, "thought", False)
        )
        try:
            adapter.validate_json(text)
        except Exception as exc:  # noqa: BLE001 - schema boundary must fail closed.
            observer = _OUTPUT_VALIDATION_OBSERVER.get()
            if observer is not None:
                observer(semantic_name, False)
            raise GoogleADKOutputValidationError(
                f"Google ADK terminal output for `{semantic_name}` failed "
                f"contract schema validation ({type(exc).__name__})"
            ) from exc
        observer = _OUTPUT_VALIDATION_OBSERVER.get()
        if observer is not None:
            observer(semantic_name, True)

    return validate_terminal_output


def _set_output_validation_observer(
    observer: _OutputValidationObserver,
) -> Token[_OutputValidationObserver | None]:
    """Bind optional trace evidence to provider-owned output validation."""

    return _OUTPUT_VALIDATION_OBSERVER.set(observer)


def _reset_output_validation_observer(
    token: Token[_OutputValidationObserver | None],
) -> None:
    _OUTPUT_VALIDATION_OBSERVER.reset(token)


def _load_prompt(name: str) -> str:
    resource = (
        files("contract4agents.adapters")
        .joinpath("prompts")
        .joinpath("google_adk")
        .joinpath(name)
    )
    return resource.read_text(encoding="utf-8")


def _thaw_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {name: _thaw(value) for name, value in values.items()}


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(name): _thaw(child) for name, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _output_mode(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    agent_id: SemanticId,
) -> OutputMode:
    control = next(
        (
            control
            for control in ir.controls.values()
            if control.agent_id == agent_id
            and control.assessment == "adapter"
            and control.derived_from == agent_id
        ),
        None,
    )
    if control is None:
        raise MaterializationError(
            (
                MaterializationIssue(
                    "MAT334",
                    "Agent output conformance control is missing",
                    agent_id,
                ),
            )
        )
    mapping = plan.controls[control.id]
    return "native" if mapping.outcome == "exact" else "emulated"


def _reject_unsupported_runtime_shapes(ir: CanonicalIR) -> None:
    issues = [
        MaterializationIssue(
            "MAT335",
            "Google ADK tool grants cannot cross a declared isolation environment",
            grant.id,
        )
        for grant in ir.grants.values()
        if grant.capability_id.kind == "tool" and grant.isolation_id is not None
    ]
    issues.extend(
        MaterializationIssue(
            "MAT336",
            "Google ADK materialization does not support handoffs",
            edge.id,
        )
        for edge in ir.composition.values()
        if edge.mode == "handoff"
    )
    if issues:
        raise MaterializationError(tuple(issues))


def _validate_graph(
    sdk: GoogleADKSDK,
    ir: CanonicalIR,
    artifacts: CompilerArtifacts,
    plan: MaterializationPlan,
    agents: Mapping[SemanticId, object],
    grant_objects: Mapping[SemanticId, object],
    edge_objects: Mapping[SemanticId, object],
    output_types: FrozenMap[str, type[object]],
    names: NativeNameRegistry,
) -> tuple[SchemaConformanceEvidence, ...]:
    issues: list[MaterializationIssue] = []
    schema_conformance: list[SchemaConformanceEvidence] = []
    for agent_id, agent_plan in plan.agents.items():
        native = sdk.describe(agents[agent_id])
        expected_name = names.assign("agent", agent_id, agent_plan.name)
        if native.semantic_name != agent_plan.name or native.native_name != expected_name:
            issues.append(
                MaterializationIssue(
                    "MAT421",
                    "Native Google ADK agent identity differs from plan",
                    agent_id,
                )
            )
        if native.instructions != artifacts.instructions[agent_plan.name]:
            issues.append(
                MaterializationIssue(
                    "MAT422",
                    "Native Google ADK instructions differ from compiler artifact",
                    agent_id,
                )
            )
        if native.model != agent_plan.model:
            issues.append(
                MaterializationIssue(
                    "MAT423",
                    "Native Google ADK model differs from plan",
                    agent_id,
                )
            )
        expected_output = output_type_for(agent_plan.output_type, output_types)
        if native.output_type is not expected_output:
            issues.append(
                MaterializationIssue(
                    "MAT424",
                    "Native Google ADK output type differs from plan",
                    agent_id,
                )
            )
        expected_mode = _output_mode(ir, plan, agent_id)
        if native.output_mode != expected_mode:
            issues.append(
                MaterializationIssue(
                    "MAT425",
                    "Native Google ADK output enforcement differs from plan",
                    agent_id,
                )
            )
        expected_output_schema = cast(dict[str, object], TypeAdapter(expected_output).json_schema())
        output_evidence = SchemaConformanceEvidence(
            semantic_id=agent_id,
            boundary=("agent_output" if expected_mode == "native" else "host_structural_output"),
            declared_schema=expected_output_schema,
            materialized_schema=cast(dict[str, object], TypeAdapter(native.output_type).json_schema()),
        )
        schema_conformance.append(output_evidence)
        if not output_evidence.matches:
            issues.append(
                MaterializationIssue(
                    "MAT427",
                    "Native Google ADK output schema differs from contract",
                    agent_id,
                )
            )
        expected_tools = [
            grant_objects[grant_id]
            for grant_id in ir.agents[agent_id].grant_ids
            if grant_id in grant_objects
        ] + [
            edge_objects[edge.id]
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "delegate"
        ]
        if len(native.tools) != len(expected_tools) or any(
            all(item is not candidate for candidate in native.tools)
            for item in expected_tools
        ):
            issues.append(
                MaterializationIssue(
                    "MAT426",
                    "Native Google ADK tools differ from planned grants/edges",
                    agent_id,
                )
            )
    for grant_id, native_tool in grant_objects.items():
        grant = ir.grants[grant_id]
        capability = ir.capabilities[grant.capability_id]
        expected_input = build_parameter_model(
            f"{ir.agents[grant.agent_id].name}_{capability.name.replace('.', '_')}Input",
            capability.parameters,
            output_types,
        )
        expected_output = type_adapter_for(capability.output_type, output_types)
        native_tool_description = sdk.describe_tool(native_tool)
        input_evidence = SchemaConformanceEvidence(
            semantic_id=grant_id,
            boundary="tool_input",
            declared_schema=dict(_input_schema(expected_input)),
            materialized_schema=dict(native_tool_description.input_schema),
        )
        output_evidence = SchemaConformanceEvidence(
            semantic_id=grant_id,
            boundary="tool_output",
            declared_schema=dict(expected_output.json_schema()),
            materialized_schema=dict(native_tool_description.output_schema),
        )
        schema_conformance.extend((input_evidence, output_evidence))
        if (
            native_tool_description.native_name != names.assign("tool", capability.id, capability.name)
            or not input_evidence.matches
            or not output_evidence.matches
        ):
            issues.append(
                MaterializationIssue(
                    "MAT428",
                    "Native Google ADK tool schema differs from contract",
                    grant_id,
                )
            )
    for edge_id, native_tool in edge_objects.items():
        edge = ir.composition[edge_id]
        child = ir.agents[edge.target_agent_id]
        expected_input = build_parameter_model(f"{child.name}Input", child.parameters, output_types)
        expected_output = type_adapter_for(child.output_type, output_types)
        native_tool_description = sdk.describe_tool(native_tool)
        input_evidence = SchemaConformanceEvidence(
            semantic_id=edge_id,
            boundary="delegate_input",
            declared_schema=dict(_input_schema(expected_input)),
            materialized_schema=dict(native_tool_description.input_schema),
        )
        output_evidence = SchemaConformanceEvidence(
            semantic_id=edge_id,
            boundary="delegate_output",
            declared_schema=dict(expected_output.json_schema()),
            materialized_schema=dict(native_tool_description.output_schema),
        )
        schema_conformance.extend((input_evidence, output_evidence))
        if (
            native_tool_description.native_name != names.assign("delegate", edge_id, edge.name)
            or not input_evidence.matches
            or not output_evidence.matches
        ):
            issues.append(
                MaterializationIssue("MAT429", "Native Google ADK delegate schema differs from contract", edge_id)
            )
    if issues:
        raise MaterializationError(tuple(issues))
    return tuple(schema_conformance)


__all__ = [
    "ADKSDK",
    "GoogleADKMaterializationProvider",
    "GoogleADKNativeAgentDescription",
    "GoogleADKNativeToolDescription",
    "GoogleADKOutputValidationError",
    "GoogleADKSDK",
    "OutputMode",
]
