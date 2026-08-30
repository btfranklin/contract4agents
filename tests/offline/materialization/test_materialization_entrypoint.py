from __future__ import annotations

import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from pydantic import ValidationError

from contract4agents import materialize
from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.compiler import CompilerArtifacts, artifact_digests
from contract4agents.ir import (
    CanonicalIR,
    EnumIR,
    TypeFieldIR,
    TypeIR,
    parse_type_ref,
    semantic_id,
)
from contract4agents.materialization import (
    MaterializationError,
    OpenAIMaterializationProvider,
    RecordingMaterializationTraceSink,
)
from contract4agents.materialization._types import build_pydantic_types
from contract4agents.planning import PlannerCapabilities
from contract4agents.runtime import EnvironmentProvider
from tests.support.openai import FakeAgent, FakeHandoff, FakeOpenAISDK, FakeTool, write_project


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
    write_project(tmp_path)
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
    write_project(tmp_path)
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
    write_project(tmp_path)
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
    write_project(tmp_path)
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
    write_project(tmp_path)
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
    write_project(tmp_path)
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
    write_project(tmp_path)

    with pytest.raises(MaterializationError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK(drop_attached_tools=True)),
        )

    assert "MAT404" in {issue.code for issue in caught.value.issues}


def test_materialization_fails_if_final_tool_schema_drops_contract_constraints(tmp_path: Path) -> None:
    write_project(tmp_path)
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


def test_materialization_trace_sink_receives_stable_validated_configuration_events(tmp_path: Path) -> None:
    write_project(tmp_path, isolation=True)
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
