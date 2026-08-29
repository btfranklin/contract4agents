"""Google ADK materialization test support."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    CapabilityIR,
    CompositionEdgeIR,
    ControlIR,
    FrozenMap,
    GrantIR,
    ParameterIR,
    SemanticId,
    TypeFieldIR,
    TypeIR,
    parse_type_ref,
    semantic_id,
)
from contract4agents.materialization._google_adk import (
    GoogleADKMaterializationProvider,
    GoogleADKNativeAgentDescription,
    GoogleADKNativeToolDescription,
    OutputMode,
)
from contract4agents.planning import MaterializationPlan, plan_materialization
from contract4agents.target_bindings import BindingEntry, TargetBinding, TargetBindings, TargetProfile


@dataclass
class FakeTool:
    name: str
    input_schema: Mapping[str, object] = field(default_factory=dict)
    output_schema: Mapping[str, object] = field(default_factory=dict)
    implementation: object | None = None
    requires_approval: bool = False
    child: object | None = None
    input_type: type[object] | None = None


@dataclass
class FakeAgent:
    semantic_name: str
    native_name: str
    instructions: str
    model: str
    input_type: type[object] | None
    output_type: type[object]
    output_mode: OutputMode
    tools: list[object] = field(default_factory=list)


class FakeGoogleADKSDK:
    version = "fake-google-adk-2.5"

    def __init__(self, *, drop_tools: bool = False, drop_list_bounds: bool = False) -> None:
        self.drop_tools = drop_tools
        self.drop_list_bounds = drop_list_bounds
        self.model_factories: dict[str, object | None] = {}

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
        del description, model_options
        self.model_factories[semantic_name] = model_factory
        return FakeAgent(
            semantic_name,
            native_name,
            instructions,
            model,
            input_type,
            output_type,
            output_mode,
            list(tools),
        )

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
        del description
        input_schema = _input_schema(input_type)
        output_schema = output_adapter.json_schema()
        if self.drop_list_bounds:
            input_schema = _without_list_bounds(input_schema)
            output_schema = _without_list_bounds(output_schema)
        return FakeTool(
            native_name,
            input_schema,
            output_schema,
            implementation,
            requires_approval,
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
        del child_name, description, binding
        return FakeTool(
            native_name,
            _input_schema(input_type),
            output_adapter.json_schema(),
            requires_approval=requires_approval,
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
        del description
        input_schema = _input_schema(input_type)
        output_schema = output_adapter.json_schema()
        if self.drop_list_bounds:
            input_schema = _without_list_bounds(input_schema)
            output_schema = _without_list_bounds(output_schema)
        return FakeTool(
            native_name,
            input_schema,
            output_schema,
            child=child,
            input_type=input_type,
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
        environment: object,
    ) -> object:
        del (
            description,
            isolation_id,
            requested_dimensions,
            declared_capabilities,
            environment,
        )
        input_schema = _input_schema(input_type)
        output_schema = output_adapter.json_schema()
        if self.drop_list_bounds:
            input_schema = _without_list_bounds(input_schema)
            output_schema = _without_list_bounds(output_schema)
        return FakeTool(
            native_name,
            input_schema,
            output_schema,
            child=child,
            input_type=input_type,
        )

    def attach(self, agent: object, *, tools: tuple[object, ...]) -> None:
        assert isinstance(agent, FakeAgent)
        agent.tools = [] if self.drop_tools else list(tools)

    def describe(self, agent: object) -> GoogleADKNativeAgentDescription:
        assert isinstance(agent, FakeAgent)
        return GoogleADKNativeAgentDescription(
            semantic_name=agent.semantic_name,
            native_name=agent.native_name,
            instructions=agent.instructions,
            model=agent.model,
            input_type=agent.input_type,
            output_type=agent.output_type,
            output_mode=agent.output_mode,
            tools=tuple(agent.tools),
        )

    def describe_tool(self, tool: object) -> GoogleADKNativeToolDescription:
        assert isinstance(tool, FakeTool)
        return GoogleADKNativeToolDescription(
            tool.name,
            tool.input_schema,
            tool.output_schema,
        )


def _input_schema(input_type: type[object] | None) -> Mapping[str, object]:
    if input_type is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    return input_type.model_json_schema()  # type: ignore[attr-defined,no-any-return]


def _without_list_bounds(schema: Mapping[str, object]) -> dict[str, object]:
    result = dict(schema)
    properties = result.get("properties")
    if isinstance(properties, Mapping):
        result["properties"] = {
            name: (
                {key: value for key, value in property_schema.items() if key not in {"minItems", "maxItems"}}
                if isinstance(property_schema, Mapping) and property_schema.get("type") == "array"
                else property_schema
            )
            for name, property_schema in properties.items()
        }
    return result


def _provider_ir() -> CanonicalIR:
    answer = TypeIR(
        semantic_id("type", "Answer"),
        "Answer",
        (TypeFieldIR("summary", parse_type_ref("string")),),
    )
    lookup = TypeIR(
        semantic_id("type", "Lookup"),
        "Lookup",
        (TypeFieldIR("query", parse_type_ref("string")),),
    )
    tool = CapabilityIR(
        semantic_id("tool", "records.lookup"),
        "records.lookup",
        "tool",
        (ParameterIR("query", parse_type_ref("string")),),
        parse_type_ref("Answer"),
        "Look up records.",
        side_effect=False,
    )
    parent_id = semantic_id("agent", "Parent")
    child_id = semantic_id("agent", "Child")
    grant = GrantIR(
        semantic_id("grant", "Parent", "records.lookup"),
        parent_id,
        tool.id,
        "enabled",
        "approval_required",
    )
    parent = AgentIR(
        parent_id,
        "Parent",
        (),
        parse_type_ref("Answer"),
        "Coordinate the answer.",
        grant_ids=(grant.id,),
    )
    child = AgentIR(
        child_id,
        "Child",
        (ParameterIR("query", parse_type_ref("string")),),
        parse_type_ref("Answer"),
        "Research one answer.",
    )
    edge = CompositionEdgeIR(
        semantic_id("edge", "ask_child"),
        "ask_child",
        parent_id,
        child_id,
        "delegate",
        "Ask the child.",
        "none",
        FrozenMap((("query", "inputs.query"),)),
    )
    controls = (
        _output_control(parent_id),
        _output_control(child_id),
        ControlIR(
            semantic_id(
                "control",
                "Parent",
                "approval",
                "records.lookup",
            ),
            "approval_records_lookup",
            parent_id,
            "high",
            True,
            ("adapter", "host"),
            "runtime",
            derived_from=grant.id,
            expected_evidence=(
                "approval.requested",
                "approval.completed",
                "tool.started",
            ),
        ),
    )
    return CanonicalIR.create(
        types=(lookup, answer),
        capabilities=(tool,),
        agents=(parent, child),
        grants=(grant,),
        composition=(edge,),
        controls=controls,
    )


def _output_control(agent_id: SemanticId) -> ControlIR:
    return ControlIR(
        semantic_id("control", agent_id.parts[0], "output_conformance"),
        "output_conformance",
        agent_id,
        "high",
        True,
        ("adapter", "host"),
        "adapter",
        derived_from=agent_id,
        expected_evidence=("output.accepted", "output.schema_failed"),
    )


def _target_and_plan(
    root: Path,
    ir: CanonicalIR,
) -> tuple[TargetBinding, MaterializationPlan]:
    target = TargetBinding(
        adapter="google_adk",
        tools={
            "records.lookup": BindingEntry({"python": "app:lookup"}),
        },
        profiles={"test": TargetProfile(default_model="gemini-2.5-flash")},
    )
    bindings = TargetBindings(
        path=root / "contract4agents.targets.toml",
        targets={"google_adk": target},
    )
    provider = GoogleADKMaterializationProvider(FakeGoogleADKSDK())
    return (
        target,
        plan_materialization(
            ir,
            bindings,
            target="google_adk",
            profile="test",
            capabilities=provider.planner_capabilities(None),
        ),
    )


__all__ = ["FakeAgent", "FakeGoogleADKSDK", "FakeTool", "_provider_ir", "_target_and_plan"]
