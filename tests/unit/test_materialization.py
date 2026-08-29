from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError, create_model

from contract4agents import materialize
from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.compiler import CompilerArtifacts, artifact_digests
from contract4agents.ir import (
    CanonicalIR,
    EnumIR,
    FrozenMap,
    SemanticId,
    TypeFieldIR,
    TypeIR,
    freeze_json,
    parse_type_ref,
    semantic_id,
)
from contract4agents.materialization import (
    AgentsSDK,
    ContextResolutionError,
    GraphValidationEvidence,
    MaterializationError,
    NativeAgentDescription,
    NativeToolDescription,
    OpenAIMaterializationProvider,
    RecordingMaterializationTraceSink,
    SchemaConformanceEvidence,
)
from contract4agents.materialization._types import build_pydantic_types
from contract4agents.planning import PlannerCapabilities, PlanningError
from contract4agents.runtime import EnvironmentProvider, EnvironmentRunRequest, InProcessEnvironment
from contract4agents.target_bindings import BindingEntry
from contract4agents.tracing import NoOpNormalizedTraceSink, RecordingNormalizedTraceSink


@dataclass
class FakeTool:
    name: str
    input_schema: Mapping[str, object] = field(default_factory=dict)
    implementation: object | None = None
    requires_approval: bool = False
    environment: EnvironmentProvider | None = None
    isolation_id: SemanticId | None = None
    dimensions: FrozenMap[str, str] = field(default_factory=FrozenMap)
    declared_capabilities: tuple[str, ...] = ()
    input_type: type[object] | None = None


@dataclass
class FakeHandoff:
    name: str
    child: object
    history: str


@dataclass
class FakeAgent:
    name: str
    instructions: str
    model: str
    model_options: Mapping[str, object]
    output_type: type[object]
    tools: list[object]
    handoffs: list[object] = field(default_factory=list)


class FakeOpenAISDK:
    version = "fake-openai-1"

    def __init__(self, *, drop_attached_tools: bool = False, drift_tool_schema: bool = False) -> None:
        self.drop_attached_tools = drop_attached_tools
        self.drift_tool_schema = drift_tool_schema

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
        return FakeAgent(name, instructions, model, model_options, output_type, list(tools))

    def create_function_tool(
        self,
        *,
        name: str,
        description: str,
        implementation: object,
        input_type: type[object] | None,
        output_adapter: object,
        requires_approval: bool,
    ) -> object:
        del description, output_adapter
        schema = dict(self.input_schema(input_type))
        if self.drift_tool_schema:
            properties = cast(dict[str, object], schema.get("properties", {}))
            if properties:
                first = next(iter(properties))
                properties[first] = {"type": "string"}
        return FakeTool(
            openai_tool_name(name),
            schema,
            implementation,
            requires_approval,
        )

    def create_hosted_tool(self, *, name: str, binding: BindingEntry) -> object:
        return FakeTool(name, implementation=binding)

    def create_delegate_tool(
        self,
        *,
        name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
    ) -> object:
        del description, child
        return FakeTool(
            openai_tool_name(name),
            self.input_schema(input_type),
            input_type=input_type,
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
        del description, child
        return FakeTool(
            openai_tool_name(name),
            self.input_schema(input_type),
            environment=environment,
            isolation_id=isolation_id,
            dimensions=requested_dimensions,
            declared_capabilities=declared_capabilities,
            input_type=input_type,
        )

    def create_handoff(
        self,
        *,
        name: str,
        description: str,
        child: object,
        history: str,
    ) -> object:
        del description
        return FakeHandoff(name, child, history)

    def attach(self, agent: object, *, tools: tuple[object, ...], handoffs: tuple[object, ...]) -> None:
        assert isinstance(agent, FakeAgent)
        agent.tools = [] if self.drop_attached_tools else list(tools)
        agent.handoffs = list(handoffs)

    def describe(self, agent: object) -> NativeAgentDescription:
        assert isinstance(agent, FakeAgent)
        return NativeAgentDescription(
            agent.name,
            agent.instructions,
            agent.model,
            agent.output_type,
            self.output_schema(agent.output_type),
            tuple(agent.tools),
            tuple(agent.handoffs),
        )

    def describe_tool(self, tool: object) -> NativeToolDescription:
        assert isinstance(tool, FakeTool)
        return NativeToolDescription(tool.name, tool.input_schema)

    def input_schema(self, input_type: type[object] | None) -> Mapping[str, object]:
        if input_type is None:
            return {"type": "object", "properties": {}, "additionalProperties": False}
        return cast(dict[str, object], cast(Any, input_type).model_json_schema())

    def output_schema(self, output_type: type[object]) -> Mapping[str, object]:
        return cast(dict[str, object], cast(Any, output_type).model_json_schema())


class CustomMaterializationProvider:
    adapter = "custom"

    def __init__(self, sdk: FakeOpenAISDK) -> None:
        self._provider = OpenAIMaterializationProvider(sdk)

    def planner_capabilities(
        self,
        environment: EnvironmentProvider | None,
    ) -> PlannerCapabilities:
        base = self._provider.planner_capabilities(environment)
        return PlannerCapabilities.create(
            adapter=self.adapter,
            version=base.version,
            approval=base.approval,
            composition=base.composition,
            controls=base.controls,
            isolation=base.isolation,
            expected_event_types=base.expected_event_types,
            mapping_resolver=base.mapping_resolver,
        )

    def build_graph(self, **kwargs: Any) -> Any:
        return self._provider.build_graph(**kwargs)


def test_runtime_pydantic_types_enforce_literal_enum_membership() -> None:
    status = EnumIR(semantic_id("type", "Status"), "Status", ("accepted", "failed"))
    result = TypeIR(
        semantic_id("type", "Result"),
        "Result",
        (TypeFieldIR("status", parse_type_ref("Status")),),
    )

    types = build_pydantic_types(CanonicalIR.create(types=(result, status)))

    assert types["Result"](status="accepted").status == "accepted"
    with pytest.raises(ValidationError):
        types["Result"](status="unknown")


def test_public_materialize_builds_and_validates_complete_native_graph(tmp_path: Path) -> None:
    _write_project(tmp_path)
    sdk = FakeOpenAISDK()

    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(sdk),
    )

    assert result.plan.adapter.name == "openai"
    assert result.plan.adapter.version == "fake-openai-1"
    assert "instructions/Parent.md" in result.plan.artifact_digests
    assert result.graph.validation.plan_digest == result.plan.plan_digest
    assert result.structural_output_types == result.graph.output_types
    assert len(result.agents) == 3
    assert result.agents["Parent"] is result.graph.agent("Parent")
    parent = result.graph.agent("Parent")
    child = result.graph.agent("Child")
    reviewer = result.graph.agent("Reviewer")
    assert isinstance(parent, FakeAgent)
    assert isinstance(child, FakeAgent)
    assert isinstance(reviewer, FakeAgent)
    assert parent.model == "test-model"
    assert "Delegate to `Child`" in parent.instructions
    assert [item.name for item in parent.tools if isinstance(item, FakeTool)] == [
        openai_tool_name("ask_child"),
    ]
    assert cast(FakeTool, parent.tools[0]).input_type is result.graph.input_types[
        semantic_id("agent", "Child")
    ]
    assert [item.name for item in parent.handoffs if isinstance(item, FakeHandoff)] == ["send_review"]
    assert cast(FakeHandoff, parent.handoffs[0]).child is reviewer

    grant_id = semantic_id("grant", "Child", "records.lookup")
    native_grant = result.graph.grant_objects[grant_id]
    assert isinstance(native_grant, FakeTool)
    assert native_grant.requires_approval
    assert native_grant.implementation is result.graph.implementations[semantic_id("tool", "records.lookup")]
    assert cast(Any, result.graph.implementations[semantic_id("datasource", "records.current")]).__name__ == "current"
    assert cast(Any, result.graph.implementations[semantic_id("external", "request_context")]).__name__ == "context"

    result_model = cast(Any, result.graph.output_types["Result"])
    assert result_model(value="ok").value == "ok"
    with pytest.raises(ValidationError):
        result_model(value="ok", undeclared=True)


def test_materialization_rejects_a_loaded_host_module_without_replacing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_project(tmp_path)
    host_module = ModuleType("app_impl")
    host_module.__file__ = str(tmp_path.parent / "host" / "app_impl.py")
    monkeypatch.setitem(sys.modules, "app_impl", host_module)

    with pytest.raises(MaterializationError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        )

    assert {issue.code for issue in caught.value.issues} == {"TGT105"}
    assert all("unique, package-qualified application module name" in issue.message for issue in caught.value.issues)
    assert sys.modules["app_impl"] is host_module


def test_materialization_returns_the_compiler_artifacts_used_by_the_graph_and_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_project(tmp_path)
    import contract4agents.materialization._entrypoint as entrypoint

    original_compile = entrypoint.compile_project
    compiled: list[CompilerArtifacts] = []

    def compile_spy(root: Path | str) -> CompilerArtifacts:
        artifacts = original_compile(root)
        compiled.append(artifacts)
        return artifacts

    monkeypatch.setattr(entrypoint, "compile_project", compile_spy)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    assert compiled == [result.artifacts]
    assert result.artifacts is compiled[0]
    assert result.graph.context.ir is result.artifacts.ir
    assert result.plan.contract_digest == result.artifacts.contract_digest
    assert result.plan.artifact_digests == artifact_digests(result.artifacts)
    assert result.graph.validation.contract_digest == result.artifacts.contract_digest
    assert result.graph.validation.plan_digest == result.plan.plan_digest


def test_materialization_validates_and_serializes_root_agent_inputs(tmp_path: Path) -> None:
    _write_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    parent_id = semantic_id("agent", "Parent")
    assert result.plan.agents[parent_id].parameters == result.artifacts.ir.agents[parent_id].parameters
    assert result.agent_input_types["Parent"] is result.graph.input_types[parent_id]

    validated = cast(Any, result.validate_agent_input("Parent", {"request": {"value": "hello"}}))
    assert validated.request.value == "hello"
    assert result.serialize_agent_input("Parent", {"request": {"value": "hello"}}) == (
        '{"request":{"value":"hello"}}'
    )

    invalid_inputs: tuple[object, ...] = (
        {},
        {"request": {"value": 1}},
        {"request": {"value": "hello", "extra": True}},
        {"request": {"value": "hello"}, "extra": True},
        "raw input",
    )
    for invalid in invalid_inputs:
        with pytest.raises(MaterializationError) as caught:
            result.validate_agent_input("Parent", cast(Any, invalid))
        assert [issue.code for issue in caught.value.issues] == ["MAT206"]

    with pytest.raises(MaterializationError) as caught:
        result.validate_agent_input("Missing", {})
    assert [issue.code for issue in caught.value.issues] == ["MAT205"]


def test_materialization_rejects_input_for_parameter_free_agent(tmp_path: Path) -> None:
    _write_parameter_free_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    assert result.agent_input_types["Worker"] is None
    assert result.validate_agent_input("Worker", {}) is None
    assert result.serialize_agent_input("Worker", {}) == "{}"
    with pytest.raises(MaterializationError) as caught:
        result.serialize_agent_input("Worker", {"unexpected": True})
    assert [issue.code for issue in caught.value.issues] == ["MAT206"]


def test_injected_provider_supports_an_unknown_matching_adapter(tmp_path: Path) -> None:
    _write_project(tmp_path)
    bindings = tmp_path / "contract4agents.targets.toml"
    bindings.write_text(
        bindings.read_text(encoding="utf-8")
        .replace("targets.openai", "targets.custom")
        .replace('adapter = "openai"', 'adapter = "custom"'),
        encoding="utf-8",
    )

    result = materialize(
        tmp_path,
        "custom",
        "test",
        provider=CustomMaterializationProvider(FakeOpenAISDK()),
    )

    assert result.plan.adapter.name == "custom"
    assert result.agents["Parent"] is result.graph.agent("Parent")


def test_materialization_fails_if_native_graph_does_not_match_plan(tmp_path: Path) -> None:
    _write_project(tmp_path)

    with pytest.raises(MaterializationError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK(drop_attached_tools=True)),
        )

    assert "MAT404" in {issue.code for issue in caught.value.issues}


def test_materialization_fails_if_final_tool_schema_drops_contract_constraints(tmp_path: Path) -> None:
    _write_project(tmp_path)
    contract_path = tmp_path / "system.contract"
    contract_path.write_text(
        contract_path.read_text().replace(
            "tool records.lookup(query: string)",
            "tool records.lookup(query: string(min_length=1,max_length=4000))",
        )
    )

    with pytest.raises(MaterializationError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK(drift_tool_schema=True)),
        )

    assert "MAT408" in {issue.code for issue in caught.value.issues}


def test_concrete_openai_materializer_builds_real_sdk_objects_without_live_calls(tmp_path: Path) -> None:
    from agents import Agent, FunctionTool, Handoff

    _write_project(tmp_path)

    result = materialize(tmp_path, "openai", "test")

    parent = result.agents["Parent"]
    child = result.agents["Child"]
    assert isinstance(parent, Agent)
    assert isinstance(child, Agent)
    assert all(isinstance(item, FunctionTool) for item in parent.tools)
    assert all(isinstance(item, Handoff) for item in parent.handoffs)
    assert cast(FunctionTool, child.tools[0]).needs_approval is True
    assert result.plan.adapter.version != "unavailable"


def test_concrete_openai_materializer_supports_nested_profile_and_agent_options(
    tmp_path: Path,
) -> None:
    from agents import ModelRetrySettings
    from openai.types.shared import Reasoning

    _write_project(tmp_path)
    bindings = tmp_path / "contract4agents.targets.toml"
    bindings.write_text(
        bindings.read_text(encoding="utf-8")
        + """\

[targets.openai.profiles.test.options.retry]
max_retries = 0

[targets.openai.profiles.test.agents.Child.options.reasoning]
effort = "high"
""",
        encoding="utf-8",
    )

    result = materialize(tmp_path, "openai", "test")

    parent_settings = cast(Any, result.agents["Parent"]).model_settings
    child_settings = cast(Any, result.agents["Child"]).model_settings
    assert isinstance(parent_settings.retry, ModelRetrySettings)
    assert parent_settings.retry.max_retries == 0
    assert isinstance(child_settings.retry, ModelRetrySettings)
    assert isinstance(child_settings.reasoning, Reasoning)
    assert child_settings.reasoning.effort == "high"
    assert result.graph.validation.complete


def test_concrete_openai_sdk_recursively_thaws_pass_through_model_options() -> None:
    frozen = freeze_json(
        {
            "extra_body": {"custom": {"flags": ["one", "two"]}},
            "tool_choice": {"server_label": "server", "name": "tool"},
        }
    )
    assert isinstance(frozen, FrozenMap)

    agent = AgentsSDK().create_agent(
        name="NestedOptions",
        instructions="Return a result.",
        model="test-model",
        model_options=frozen,
        output_type=create_model("NestedOptionsOutput", value=(str, ...)),
        tools=(),
    )

    settings = cast(Any, agent).model_settings
    assert settings.extra_body == {"custom": {"flags": ["one", "two"]}}
    assert isinstance(settings.extra_body, dict)
    assert isinstance(settings.extra_body["custom"], dict)
    assert isinstance(settings.extra_body["custom"]["flags"], list)
    assert settings.tool_choice.server_label == "server"
    assert settings.tool_choice.name == "tool"


def test_openai_model_option_validation_errors_are_structured() -> None:
    frozen = freeze_json({"retry": {"backoff": {"initial_delay": -0.1}}})
    assert isinstance(frozen, FrozenMap)

    with pytest.raises(MaterializationError) as caught:
        AgentsSDK().create_agent(
            name="InvalidOptions",
            instructions="Return a result.",
            model="test-model",
            model_options=frozen,
            output_type=create_model("InvalidOptionsOutput", value=(str, ...)),
            tools=(),
        )

    assert [issue.code for issue in caught.value.issues] == ["MAT302"]
    assert "Invalid OpenAI model options" in caught.value.issues[0].message


def test_openai_tool_uses_contract_schema_instead_of_callable_annotations(tmp_path: Path) -> None:
    from agents import FunctionTool

    _write_project(tmp_path)
    contract_path = tmp_path / "system.contract"
    contract_path.write_text(
        contract_path.read_text().replace(
            "tool records.lookup(query: string)",
            "tool records.lookup(query: string(min_length=1,max_length=4000))",
        )
    )

    result = materialize(tmp_path, "openai", "test")
    tool = cast(FunctionTool, cast(Any, result.graph.agent("Child")).tools[0])
    query_schema = cast(dict[str, object], tool.params_json_schema["properties"])["query"]

    assert query_schema == {"maxLength": 4000, "minLength": 1, "title": "Query", "type": "string"}
    assert tool.params_json_schema["additionalProperties"] is False
    assert result.graph.validation.complete
    round_trip = type(result.graph.validation).from_dict(result.graph.validation.to_dict())
    assert round_trip == result.graph.validation
    evidence_path = tmp_path / "materialization-conformance.json"
    evidence_path.write_text(result.graph.validation.to_json())
    assert GraphValidationEvidence.load(evidence_path) == result.graph.validation
    with pytest.raises(ValueError, match="Invalid graph validation evidence JSON"):
        GraphValidationEvidence.from_json("{")
    inconsistent = json.loads(result.graph.validation.to_json())
    inconsistent["complete"] = not inconsistent["complete"]
    with pytest.raises(ValueError, match="completeness is inconsistent"):
        GraphValidationEvidence.from_dict(inconsistent)
    inconsistent = json.loads(result.graph.validation.to_json())
    inconsistent["schema_conformance"][0]["declared_digest"] = "sha256:incorrect"
    with pytest.raises(ValueError, match="Declared schema digest is inconsistent"):
        GraphValidationEvidence.from_dict(inconsistent)
    inconsistent = json.loads(result.graph.validation.to_json())
    inconsistent["schema_conformance"][0]["materialized_digest"] = "sha256:incorrect"
    with pytest.raises(ValueError, match="Materialized schema digest is inconsistent"):
        GraphValidationEvidence.from_dict(inconsistent)
    with pytest.raises(TypeError, match="must be an object"):
        GraphValidationEvidence.from_dict([])
    with pytest.raises(ValueError, match="boundary cannot be empty"):
        SchemaConformanceEvidence(semantic_id("agent", "Worker"), "", {}, {})
    with pytest.raises(TypeError, match="requires object schemas"):
        SchemaConformanceEvidence(semantic_id("agent", "Worker"), "agent_output", [], {})
    validated = asyncio.run(tool.on_invoke_tool(SimpleNamespace(), '{"query":"record-1"}'))
    assert validated.value == "record-1"
    with pytest.raises(ValidationError):
        asyncio.run(tool.on_invoke_tool(SimpleNamespace(), '{"query":""}'))


def test_openai_hosted_tool_option_errors_are_structured() -> None:
    with pytest.raises(MaterializationError) as caught:
        AgentsSDK().create_hosted_tool(
            name="web.search",
            binding=BindingEntry(
                {
                    "provider": "openai",
                    "tool": "web_search",
                    "not_a_web_search_option": True,
                }
            ),
        )

    assert [issue.code for issue in caught.value.issues] == ["MAT304"]
    assert "Invalid OpenAI web-search options" in caught.value.issues[0].message


def test_materialization_trace_sink_receives_stable_validated_configuration_events(tmp_path: Path) -> None:
    _write_project(tmp_path, isolation=True)
    sink = RecordingMaterializationTraceSink()

    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        materialization_trace_sink=sink,
    )

    event_types = {event.event_type for event in sink.events}
    assert event_types >= {
        "materialization.agent.configured",
        "materialization.grant.configured",
        "materialization.tool.bound",
        "materialization.approval.configured",
        "materialization.delegate.configured",
        "materialization.handoff.configured",
        "materialization.output_validation.configured",
        "materialization.context.configured",
        "materialization.resolver.bound",
        "materialization.datasource.bound",
        "materialization.external.bound",
        "materialization.isolation.configured",
    }
    assert {event.contract_digest for event in sink.events} == {result.plan.contract_digest}
    assert {event.plan_digest for event in sink.events} == {result.plan.plan_digest}
    approval = next(
        event for event in sink.events if event.event_type == "materialization.approval.configured"
    )
    assert approval.semantic_id == semantic_id("grant", "Child", "records.lookup")
    assert approval.agent_id == semantic_id("agent", "Child")
    assert approval.related_id == semantic_id("tool", "records.lookup")


@pytest.mark.asyncio
async def test_materialized_context_runtime_maps_validates_caches_renders_and_traces(tmp_path: Path) -> None:
    _write_project(tmp_path)
    runtime_sink = RecordingNormalizedTraceSink()
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        normalized_trace_sink=runtime_sink,
    )

    first = await result.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
        thread_id="thread-1",
    )
    second = await result.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
        thread_id="thread-1",
    )

    assert cast(Any, first["current"].value).value == "needle"
    assert first["current"].rendered == "- **value:** needle"
    assert first["current"].from_cache is False
    assert second["current"].from_cache is True
    assert cast(Any, first["metadata"].value).value == "context"
    assert second["metadata"].from_cache is True
    assert [event.event_type for event in runtime_sink.events] == [
        "datasource.resolved",
        "context.resolved",
        "datasource.resolved",
        "context.resolved",
    ]
    assert runtime_sink.events[0].semantic.context_id == semantic_id("context", "Child", "current")
    assert runtime_sink.events[0].semantic.capability_id == semantic_id("datasource", "records.current")
    assert runtime_sink.events[1].data["sensitivity"] == "internal"
    assert runtime_sink.events[0].context.plan_digest == result.plan.plan_digest
    assert all("value" not in event.data for event in runtime_sink.events)
    NoOpNormalizedTraceSink().emit(runtime_sink.events[0])

    result.context.clear_run("run-1")
    third = await result.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
    )
    assert third["current"].from_cache is False


@pytest.mark.asyncio
async def test_materialized_context_runtime_rejects_invalid_invocation_shape(tmp_path: Path) -> None:
    _write_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    with pytest.raises(ContextResolutionError, match="input validation failed"):
        await result.context.resolve_agent("Child", {"request": {}}, run_id="run-1")

    with pytest.raises(KeyError):
        await result.context.resolve_agent("Missing", {}, run_id="run-1")
    with pytest.raises(ValueError, match="run_id"):
        await result.context.resolve_agent("Child", {"request": {"value": "ok"}}, run_id="")


@pytest.mark.asyncio
async def test_context_runtime_enforces_thread_cache_and_records_provider_failures(tmp_path: Path) -> None:
    _write_project(tmp_path, datasource_cache="thread", async_current=True)
    sink = RecordingNormalizedTraceSink()
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        normalized_trace_sink=sink,
    )

    first = await result.context.resolve_agent(
        "Child", {"request": {"value": "ok"}}, run_id="run-1", thread_id="thread-1"
    )
    second = await result.context.resolve_agent(
        "Child", {"request": {"value": "ok"}}, run_id="run-2", thread_id="thread-1"
    )
    assert first["current"].from_cache is False
    assert second["current"].from_cache is True
    result.context.clear_thread("thread-1")
    third = await result.context.resolve_agent(
        "Child", {"request": {"value": "ok"}}, run_id="run-3", thread_id="thread-1"
    )
    assert third["current"].from_cache is False

    broken_root = tmp_path / "broken"
    broken_root.mkdir()
    _write_project(broken_root, invalid_current=True)
    broken_sink = RecordingNormalizedTraceSink()
    broken = materialize(
        broken_root,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        normalized_trace_sink=broken_sink,
    )
    with pytest.raises(ContextResolutionError, match="output validation failed"):
        await broken.context.resolve_agent(
            "Child", {"request": {"value": "bad"}}, run_id="run-broken"
        )
    assert broken_sink.events[-1].event_type == "datasource.failed"
    assert broken_sink.events[-1].data == {"error_type": "ValidationError"}


def test_supported_in_process_isolation_is_configured_and_evidenced(tmp_path: Path) -> None:
    _write_project(tmp_path, isolation=True)

    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    edge = result.graph.composition_objects[semantic_id("edge", "ask_child")]
    assert isinstance(edge, FakeTool)
    assert isinstance(edge.environment, InProcessEnvironment)
    assert edge.dimensions == FrozenMap(
        {
            "context": "explicit_only",
            "capabilities": "declared_only",
            "state": "fresh",
            "return": "final_output_only",
        }
    )
    assert edge.declared_capabilities == ("tool:records.lookup",)
    evidence = result.graph.environment_evidence[0]
    assert evidence.isolation_id == semantic_id("isolation", "CleanContext")
    assert evidence.provider == InProcessEnvironment.provider_id
    assert evidence.mechanisms["context"] == "in_process.fresh_context"


def test_concrete_openai_materializer_constructs_isolated_delegate_without_running_it(
    tmp_path: Path,
) -> None:
    from agents import FunctionTool

    _write_project(tmp_path, isolation=True)

    result = materialize(tmp_path, "openai", "test")

    parent = cast(Any, result.agents["Parent"])
    isolated_tool = next(item for item in parent.tools if item.name.endswith("ask_child"))
    assert isinstance(isolated_tool, FunctionTool)
    assert isolated_tool.params_json_schema["additionalProperties"] is False
    assert result.graph.environment_evidence[0].provider == InProcessEnvironment.provider_id


@pytest.mark.asyncio
async def test_in_process_environment_passes_only_explicit_fresh_declared_state() -> None:
    environment = InProcessEnvironment()
    observed: tuple[object, object | None, object | None, tuple[str, ...] | None] | None = None

    async def invoke(
        payload: object,
        context: object | None,
        state: object | None,
        capabilities: tuple[str, ...] | None,
    ) -> object:
        nonlocal observed
        observed = (payload, context, state, capabilities)
        return {"final": True}

    result = await environment.run(
        EnvironmentRunRequest(
            semantic_id("isolation", "CleanContext"),
            {"request": "only this"},
            FrozenMap(
                {
                    "context": "explicit_only",
                    "capabilities": "declared_only",
                    "state": "fresh",
                    "return": "final_output_only",
                }
            ),
            ("tool:records.lookup",),
            parent_context={"secret": True},
            parent_state={"conversation": True},
        ),
        invoke,
    )

    assert result == {"final": True}
    assert observed is not None
    assert observed[0] == {"request": "only this"}
    assert observed[1] is None
    assert observed[2] is not None and observed[2] != {"conversation": True}
    assert observed[3] == ("tool:records.lookup",)


def test_strong_isolation_dimension_fails_closed_before_graph_construction(tmp_path: Path) -> None:
    _write_project(tmp_path, isolation=True, network="denied")

    with pytest.raises(PlanningError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        )

    assert any(
        issue.code == "PLN009"
        and issue.semantic_id == semantic_id("isolation", "CleanContext")
        and "network" in issue.message
        for issue in caught.value.issues
    )


def test_unsupported_operational_control_fails_before_graph_construction(
    tmp_path: Path,
) -> None:
    _write_project(
        tmp_path,
        operational_source="""\
operational_control latency for Parent:
    severity = medium
    window = 15m
    require = trace.duration < 10s

""",
    )

    with pytest.raises(PlanningError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        )

    issue = next(item for item in caught.value.issues if item.code == "PLN012")
    assert issue.semantic_id == semantic_id("operational", "Parent", "latency")


def test_strong_environment_provider_can_satisfy_filesystem_and_network_dimensions(
    tmp_path: Path,
) -> None:
    _write_project(
        tmp_path,
        isolation=True,
        network="denied",
        filesystem="none",
        strong_environment=True,
    )

    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    isolation = result.plan.isolation[semantic_id("isolation", "CleanContext")]
    assert isolation.dimensions["network"].outcome == "host_enforced"
    assert isolation.dimensions["filesystem"].mechanism == "test_sandbox.filesystem"
    assert result.graph.environment_evidence[0].provider == "app_impl:StrongEnvironment"


def _write_project(
    tmp_path: Path,
    *,
    isolation: bool = False,
    network: str | None = None,
    filesystem: str | None = None,
    strong_environment: bool = False,
    datasource_cache: str = "run",
    async_current: bool = False,
    invalid_current: bool = False,
    operational_source: str = "",
) -> None:
    sys.modules.pop("app_impl", None)
    isolation_source = ""
    edge_isolation = ""
    environments = ""
    profile_options = ""
    if isolation:
        network_line = f"    network = {network}\n" if network is not None else ""
        filesystem_line = f"    filesystem = {filesystem}\n" if filesystem is not None else ""
        isolation_source = f"""\
isolation CleanContext:
    context = explicit_only
    capabilities = declared_only
    state = fresh
{filesystem_line}{network_line}    return = final_output_only

"""
        edge_isolation = "    isolation = CleanContext\n"
        provider_locator = (
            "app_impl:StrongEnvironment"
            if strong_environment
            else "contract4agents.runtime:InProcessEnvironment"
        )
        environments = f"""\
[targets.openai.environments.in_process]
provider = "{provider_locator}"

"""
        profile_options = """\
[targets.openai.profiles.test.options]
environment = "in_process"
"""

    (tmp_path / "system.contract").write_text(
        f"""\
type Request:
    value: string

type Result:
    value: string

tool records.lookup(query: string) -> Result:
    description = "Look up one record."
    side_effect = false

datasource records.current(query: string) -> Result:
    description = "Resolve the current record."
    render = markdown
    cache = {datasource_cache}

external_context request_context -> Request:
    description = "Invocation metadata."
    sensitivity = internal
    render = markdown

{isolation_source}{operational_source}agent Child(request: Request) -> Result:
    use records.lookup:
        availability = enabled
        authorization = approval_required
        execution = host
    context current: Result from datasource records.current:
        map query = input.request.value
    context metadata: Request from external request_context
    goal = "Use the declared lookup tool."

agent Reviewer(request: Request) -> Result:
    goal = "Review the result."

agent Parent(request: Request) -> Result:
    goal = "Delegate and review."

composition ask_child from Parent to Child:
    mode = delegate
    description = "Ask the child for a result."
    history = none
    map request = input.request
{edge_isolation}
composition send_review from Parent to Reviewer:
    mode = handoff
    description = "Hand the conversation to the reviewer."
    history = full
    map request = input.request
"""
    )
    strong_environment_source = ""
    if strong_environment:
        strong_environment_source = """\
from contract4agents.ir import FrozenMap
from contract4agents.planning import MappingSupport
from contract4agents.runtime import EnvironmentEnforcementEvidence

class StrongEnvironment:
    provider_id = "app_impl:StrongEnvironment"

    def planning_support(self):
        return {
            "context:explicit_only": MappingSupport("emulated", "test_sandbox.context"),
            "capabilities:declared_only": MappingSupport("emulated", "test_sandbox.capabilities"),
            "state:fresh": MappingSupport("emulated", "test_sandbox.state"),
            "filesystem:none": MappingSupport("host_enforced", "test_sandbox.filesystem"),
            "network:denied": MappingSupport("host_enforced", "test_sandbox.network"),
            "return:final_output_only": MappingSupport("emulated", "test_sandbox.return"),
        }

    def enforcement_evidence(self, plan):
        return EnvironmentEnforcementEvidence(
            plan.id,
            plan.environment,
            self.provider_id,
            FrozenMap((name, value.requested) for name, value in plan.dimensions.items()),
            FrozenMap((name, value.mechanism or "") for name, value in plan.dimensions.items()),
        )

    async def run(self, request, invoke):
        return await invoke(
            request.input_payload,
            None,
            object(),
            request.declared_capabilities,
        )

"""
    current_prefix = "async " if async_current else ""
    current_result = '{"wrong": query}' if invalid_current else '{"value": query}'
    (tmp_path / "app_impl.py").write_text(
        f"""\
{strong_environment_source}
def lookup(query):
    return {{"value": query}}

{current_prefix}def current(query):
    return {current_result}

def context():
    return {{"value": "context"}}
"""
    )
    (tmp_path / "contract4agents.targets.toml").write_text(
        f"""\
schema_version = "1"

[targets.openai]
adapter = "openai"

[targets.openai.tools."records.lookup"]
python = "app_impl:lookup"

[targets.openai.datasources."records.current"]
python = "app_impl:current"

[targets.openai.external_context.request_context]
python = "app_impl:context"

{environments}[targets.openai.profiles.test]
default_model = "test-model"

{profile_options}"""
    )


def _write_parameter_free_project(tmp_path: Path) -> None:
    (tmp_path / "system.contract").write_text(
        """\
type Result:
    value: string

agent Worker() -> Result:
    goal = "Return one result."
"""
    )
    (tmp_path / "contract4agents.targets.toml").write_text(
        """\
schema_version = "1"

[targets.openai]
adapter = "openai"

[targets.openai.profiles.test]
default_model = "test-model"
"""
    )
