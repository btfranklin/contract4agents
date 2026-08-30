from __future__ import annotations

import asyncio
import json
import sys
import traceback
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
    TypeFieldIR,
    TypeIR,
    freeze_json,
    parse_type_ref,
    semantic_id,
)
from contract4agents.materialization import (
    AgentsSDK,
    ContextResolutionError,
    ContextRuntime,
    GraphValidationEvidence,
    MaterializationError,
    OpenAIMaterializationProvider,
    RecordingMaterializationTraceSink,
    SchemaConformanceEvidence,
)
from contract4agents.materialization._types import build_pydantic_types
from contract4agents.planning import PlannerCapabilities, PlanningError
from contract4agents.runtime import EnvironmentProvider, EnvironmentRunRequest, InProcessEnvironment
from contract4agents.target_bindings import BindingEntry
from contract4agents.tracing import NoOpNormalizedTraceSink, RecordingNormalizedTraceSink
from tests.unit.support.openai import FakeAgent, FakeHandoff, FakeOpenAISDK, FakeTool, _write_project


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
    assert cast(FakeTool, parent.tools[0]).input_type is result.graph.input_types[semantic_id("agent", "Child")]
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
    assert result.serialize_agent_input("Parent", {"request": {"value": "hello"}}) == ('{"request":{"value":"hello"}}')

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


@pytest.mark.parametrize(
    ("invalid_request", "expected_detail"),
    (
        (
            {
                "value": "valid",
                "count": "scalar-secret",
                "details": {"count": 1},
                "items": [1],
            },
            "request.count: value does not satisfy the declared type (int_type)",
        ),
        (
            {
                "value": "valid",
                "count": 1,
                "details": {"count": "nested-secret"},
                "items": [1],
            },
            "request.details.count: value does not satisfy the declared type (int_type)",
        ),
        (
            {
                "value": "valid",
                "count": 1,
                "details": {"count": 1},
                "items": [1],
                "extra": "extra-secret",
            },
            "request.extra: value does not satisfy the declared type (extra_forbidden)",
        ),
        (
            {
                "value": "valid",
                "count": 1,
                "details": {"count": 1},
                "items": ["collection-secret"],
            },
            "request.items[0]: value does not satisfy the declared type (int_type)",
        ),
    ),
)
def test_materialization_does_not_expose_invalid_root_input_values(
    tmp_path: Path,
    invalid_request: dict[str, object],
    expected_detail: str,
) -> None:
    _write_project(tmp_path)
    contract_path = tmp_path / "system.contract"
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8").replace(
            "type Request:\n    value: string",
            """\
type Details:
    count: integer

type Request:
    value: string
    count: integer
    details: Details
    items: list[integer]""",
        ),
        encoding="utf-8",
    )
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    with pytest.raises(MaterializationError) as caught:
        result.validate_agent_input("Parent", {"request": invalid_request})

    error = caught.value
    issue = error.issues[0]
    assert issue.code == "MAT206"
    assert expected_detail in issue.message
    rendered = (
        issue.message,
        str(error),
        repr(error),
        "".join(traceback.format_exception(error)),
    )
    for secret in ("scalar-secret", "nested-secret", "extra-secret", "collection-secret"):
        assert all(secret not in text for text in rendered)
    assert error.__cause__ is None
    assert error.__context__ is None


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


@pytest.mark.parametrize("implementation_kind", ("sync", "async"))
@pytest.mark.asyncio
async def test_openai_materialized_tool_accepts_application_pydantic_output_and_continues_run(
    tmp_path: Path,
    implementation_kind: str,
) -> None:
    from collections.abc import AsyncIterator

    from agents import FunctionTool, ModelResponse, RunConfig, Runner, Usage
    from agents.models.interface import Model
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    _write_project(tmp_path)
    contract_path = tmp_path / "system.contract"
    contract_path.write_text(
        contract_path.read_text().replace("authorization = approval_required", "authorization = preapproved")
    )
    async_prefix = "async " if implementation_kind == "async" else ""
    (tmp_path / "app_impl.py").write_text(
        f"""\
from pydantic import BaseModel, ConfigDict

class SourceReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str

{async_prefix}def lookup(query: str) -> SourceReadResult:
    return SourceReadResult(value=query)

def current(query: str):
    return {{"value": query}}

def context():
    return {{"value": "context"}}
"""
    )

    system = materialize(tmp_path, "openai", "test")
    child = cast(Any, system.agents["Child"])
    tool = cast(FunctionTool, child.tools[0])

    class ToolThenFinalModel(Model):
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
            model_input = kwargs.get("input", args[1] if len(args) > 1 else None)
            self.inputs.append(model_input)
            if len(self.inputs) == 1:
                return ModelResponse(
                    output=[
                        ResponseFunctionToolCall(
                            arguments='{"query":"record-1"}',
                            call_id="call-1",
                            name=tool.name,
                            type="function_call",
                        )
                    ],
                    usage=Usage(),
                    response_id=None,
                )
            return ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="message-1",
                        content=[
                            ResponseOutputText(
                                annotations=[],
                                text='{"value":"complete"}',
                                type="output_text",
                            )
                        ],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                ],
                usage=Usage(),
                response_id=None,
            )

        async def stream_response(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
            if False:
                yield None

    scripted_model = ToolThenFinalModel()
    child.model = scripted_model
    run = await Runner.run(
        child,
        input="Read one source.",
        max_turns=3,
        run_config=RunConfig(tracing_disabled=True),
    )

    assert type(run.final_output) is system.structural_output_types["Result"]
    assert run.final_output.value == "complete"
    assert len(scripted_model.inputs) == 2
    second_turn = cast(list[dict[str, object]], scripted_model.inputs[1])
    tool_output = next(item for item in second_turn if item.get("type") == "function_call_output")
    assert tool_output["call_id"] == "call-1"
    assert "record-1" in cast(str, tool_output["output"])


@pytest.mark.parametrize(
    ("model_body", "constructor", "error_type"),
    (
        ("    pass", "SourceReadResult()", "missing"),
        (
            "    value: str\n    unexpected: str",
            'SourceReadResult(value=query, unexpected="extra")',
            "extra_forbidden",
        ),
        ("    value: int", "SourceReadResult(value=1)", "string_type"),
    ),
)
@pytest.mark.asyncio
async def test_openai_materialized_tool_keeps_strict_validation_after_pydantic_normalization(
    tmp_path: Path,
    model_body: str,
    constructor: str,
    error_type: str,
) -> None:
    from agents import FunctionTool

    _write_project(tmp_path)
    (tmp_path / "app_impl.py").write_text(
        f"""\
from pydantic import BaseModel, ConfigDict

class SourceReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
{model_body}

def lookup(query: str) -> SourceReadResult:
    return {constructor}

def current(query: str):
    return {{"value": query}}

def context():
    return {{"value": "context"}}
"""
    )

    system = materialize(tmp_path, "openai", "test")
    tool = cast(FunctionTool, cast(Any, system.agents["Child"]).tools[0])

    with pytest.raises(ValidationError) as caught:
        await tool.on_invoke_tool(SimpleNamespace(), '{"query":"record-1"}')

    assert error_type in {cast(str, item["type"]) for item in caught.value.errors()}


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
    approval = next(event for event in sink.events if event.event_type == "materialization.approval.configured")
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

    result.context.complete_run("run-1")
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
    result.context.complete_thread("thread-1")
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
        await broken.context.resolve_agent("Child", {"request": {"value": "bad"}}, run_id="run-broken")
    with pytest.raises(ContextResolutionError, match="output validation failed"):
        await broken.context.resolve_agent("Child", {"request": {"value": "bad"}}, run_id="run-broken")
    failures = [event for event in broken_sink.events if event.event_type == "datasource.failed"]
    assert len(failures) == 2
    assert all(event.data == {"error_type": "ValidationError"} for event in failures)


@pytest.mark.asyncio
async def test_context_runtime_uses_single_flight_and_requires_inactive_completion(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def current(query: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"value": query}

    datasource_id = semantic_id("datasource", "records.current")
    implementations = FrozenMap(
        (
            identifier,
            current if identifier == datasource_id else implementation,
        )
        for identifier, implementation in result.context.implementations.items()
    )
    runtime = ContextRuntime(
        result.context.ir,
        result.context.plan,
        implementations,
        result.context.output_types,
    )
    first_task = asyncio.create_task(
        runtime.resolve_agent(
            "Child",
            {"request": {"value": "same"}},
            run_id="run-1",
            thread_id="thread-1",
        )
    )
    await started.wait()
    second_task = asyncio.create_task(
        runtime.resolve_agent(
            "Child",
            {"request": {"value": "same"}},
            run_id="run-1",
            thread_id="thread-1",
        )
    )
    await asyncio.sleep(0)

    assert calls == 1
    with pytest.raises(RuntimeError, match="resolution is active"):
        runtime.complete_run("run-1")

    release.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert first["current"].from_cache is False
    assert second["current"].from_cache is True
    assert calls == 1

    runtime.complete_run("run-1")
    third = await runtime.resolve_agent(
        "Child",
        {"request": {"value": "same"}},
        run_id="run-1",
        thread_id="thread-1",
    )
    assert third["current"].from_cache is False
    assert calls == 2


@pytest.mark.asyncio
async def test_context_runtime_retries_after_provider_cancellation(tmp_path: Path) -> None:
    _write_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )
    calls = 0

    async def current(query: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return {"value": query}

    datasource_id = semantic_id("datasource", "records.current")
    implementations = FrozenMap(
        (
            identifier,
            current if identifier == datasource_id else implementation,
        )
        for identifier, implementation in result.context.implementations.items()
    )
    runtime = ContextRuntime(
        result.context.ir,
        result.context.plan,
        implementations,
        result.context.output_types,
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.resolve_agent("Child", {"request": {"value": "retry"}}, run_id="run-1")
    resolved = await runtime.resolve_agent("Child", {"request": {"value": "retry"}}, run_id="run-1")

    assert cast(Any, resolved["current"].value).value == "retry"
    assert calls == 2


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
