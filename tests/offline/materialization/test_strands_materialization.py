from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from contract4agents import compile_project, materialize
from contract4agents.adapters._native_names import native_name
from contract4agents.ir import FrozenMap, freeze_json, semantic_id
from contract4agents.materialization import (
    MaterializationError,
    RecordingMaterializationTraceSink,
)
from contract4agents.materialization._strands import (
    StrandsAgentsSDK,
    StrandsMaterializationProvider,
)
from contract4agents.runtime import InProcessEnvironment
from contract4agents.tracing import (
    StrandsNormalizedTraceRouter,
    TraceAttempt,
    assess_trace_evidence,
    validate_trace_closure,
    validate_trace_conformance,
)
from tests.support.strands import (
    FakeStrandsAgent,
    FakeStrandsSDK,
    FakeStrandsTool,
    write_project,
)


def test_strands_provider_builds_validated_graph_with_exact_controls(
    tmp_path: Path,
) -> None:
    write_project(tmp_path)
    sdk = FakeStrandsSDK()
    sink = RecordingMaterializationTraceSink()

    result = materialize(
        tmp_path,
        "strands",
        "test",
        provider=StrandsMaterializationProvider(sdk),
        materialization_trace_sink=sink,
    )

    assert result.plan.adapter.name == "strands"
    assert result.plan.adapter.version == "fake-strands-1"
    assert result.plan.bindings[semantic_id("tool", "records.lookup")].outcome == "exact"
    approval = result.plan.controls[semantic_id("control", "Child", "approval", "records.lookup")]
    assert approval.outcome == "exact"
    edge_id = semantic_id("edge", "ask_child")
    assert result.plan.composition[edge_id].outcome == "emulated"
    assert any(
        "model-supplied delegate values" in obligation.description for obligation in result.plan.host_obligations
    )

    parent = result.graph.agent("Parent")
    child = result.graph.agent("Child")
    assert isinstance(parent, FakeStrandsAgent)
    assert isinstance(child, FakeStrandsAgent)
    assert parent.native_name == native_name(
        "agent",
        semantic_id("agent", "Parent"),
        "Parent",
    )
    assert len(parent.native_name) <= 64
    assert parent.approval_allowed_tools is None
    assert child.approval_allowed_tools == ("Result",)
    assert [tool.native_name for tool in cast(list[FakeStrandsTool], parent.tools)] == [
        native_name("delegate", edge_id, "ask_child")
    ]
    assert cast(FakeStrandsTool, parent.tools[0]).input_type is result.graph.input_types[semantic_id("agent", "Child")]
    child_tools = cast(list[FakeStrandsTool], child.tools)
    assert [tool.native_name for tool in child_tools] == [
        native_name(
            "tool",
            semantic_id("tool", "records.lookup"),
            "records.lookup",
        )
    ]
    assert child_tools[0].implementation is result.graph.implementations[semantic_id("tool", "records.lookup")]
    assert result.graph.implementations[semantic_id("datasource", "records.current")]
    assert result.graph.implementations[semantic_id("external", "request_context")]

    event_types = {event.event_type for event in sink.events}
    assert event_types >= {
        "materialization.agent.configured",
        "materialization.tool.bound",
        "materialization.approval.configured",
        "materialization.delegate.configured",
        "materialization.output_validation.configured",
        "materialization.datasource.bound",
        "materialization.external.bound",
    }


def test_strands_provider_consumes_model_factories_once_per_agent(
    tmp_path: Path,
) -> None:
    write_project(tmp_path, model_factory=True)
    sdk = FakeStrandsSDK()

    result = materialize(
        tmp_path,
        "strands",
        "test",
        provider=StrandsMaterializationProvider(sdk),
    )

    module = importlib.import_module("app_impl")
    assert module.FACTORY_CALLS == [
        ("test-model", {"temperature": 0.2}),
        ("test-model", {"temperature": 0.2}),
    ]
    assert len(sdk.model_factory_calls) == 2
    assert all(factory is not None for _, _, factory in sdk.model_factory_calls)
    assert all(isinstance(agent.model, dict) for agent in cast(list[FakeStrandsAgent], list(result.agents.values())))
    assert all(set(options) == {"temperature"} for _, options, _factory in sdk.model_factory_calls)


def test_strands_provider_rejects_a_native_graph_that_drops_tools(
    tmp_path: Path,
) -> None:
    write_project(tmp_path)

    with pytest.raises(MaterializationError) as caught:
        materialize(
            tmp_path,
            "strands",
            "test",
            provider=StrandsMaterializationProvider(FakeStrandsSDK(drop_attached_tools=True)),
        )

    assert "MAT455" in {issue.code for issue in caught.value.issues}


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (lambda **_kwargs: object(), "MAT314"),
        (
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("factory failed")),
            "MAT312",
        ),
    ],
)
def test_concrete_strands_model_factory_wraps_failures(
    factory: object,
    code: str,
) -> None:
    with pytest.raises(MaterializationError) as caught:
        StrandsAgentsSDK().create_model(
            model="custom-model",
            model_options={"temperature": 0.2},
            factory=factory,
        )

    assert [issue.code for issue in caught.value.issues] == [code]


def test_concrete_strands_model_options_are_thawed_before_bedrock_validation() -> None:
    from botocore.validate import ParamValidator

    frozen = freeze_json(
        {"additional_request_fields": {"reasoning_config": {"type": "enabled", "budget_tokens": 1024}}}
    )
    assert isinstance(frozen, FrozenMap)

    model = cast(
        Any,
        StrandsAgentsSDK().create_model(
            model="global.anthropic.claude-sonnet-4-6",
            model_options=frozen,
            factory=None,
        ),
    )
    request = model.format_request([{"role": "user", "content": [{"text": "test"}]}])

    assert isinstance(request["additionalModelRequestFields"], dict)
    assert isinstance(request["additionalModelRequestFields"]["reasoning_config"], dict)
    operation = model.client.meta.service_model.operation_model("ConverseStream")
    errors = ParamValidator().validate(request, operation.input_shape)
    assert not errors.has_errors(), errors.generate_report()


def test_concrete_strands_model_factory_receives_thawed_options() -> None:
    from strands.models.bedrock import BedrockModel

    captured: dict[str, object] = {}

    def factory(*, model: str, options: dict[str, object]) -> object:
        captured.update(options)
        return BedrockModel(model_id=model)

    frozen = freeze_json({"custom": {"entries": [{"enabled": True}]}})
    assert isinstance(frozen, FrozenMap)

    StrandsAgentsSDK().create_model(
        model="test-model",
        model_options=frozen,
        factory=factory,
    )

    assert captured == {"custom": {"entries": [{"enabled": True}]}}
    assert isinstance(captured["custom"], dict)
    assert isinstance(cast(dict[str, object], captured["custom"])["entries"], list)


@pytest.mark.parametrize("async_lookup", [False, True])
@pytest.mark.asyncio
async def test_real_strands_sdk_builds_and_runs_typed_tools_without_live_calls(
    tmp_path: Path,
    async_lookup: bool,
) -> None:
    strands = pytest.importorskip("strands")
    write_project(tmp_path, async_lookup=async_lookup)
    provider = StrandsMaterializationProvider()

    result = materialize(
        tmp_path,
        "strands",
        "test",
        provider=provider,
    )

    parent = result.graph.agent("Parent")
    child = result.graph.agent("Child")
    assert isinstance(parent, strands.Agent)
    assert isinstance(child, strands.Agent)
    child_description = provider.sdk.describe_agent(child)
    assert child_description.approval_allowed_tools == ("Result",)
    assert child_description.output_type is result.graph.output_types["Result"]
    accepted = provider.validate_result(
        child,
        SimpleNamespace(structured_output={"value": "accepted"}),
    )
    assert accepted.value == "accepted"
    with pytest.raises(MaterializationError, match="MAT324"):
        provider.validate_result(
            child,
            SimpleNamespace(structured_output=None),
        )
    with pytest.raises(MaterializationError, match="MAT324"):
        provider.validate_result(
            child,
            SimpleNamespace(structured_output={"wrong": "shape"}),
        )

    native_tool = result.graph.grant_objects[semantic_id("grant", "Child", "records.lookup")]
    tool_description = provider.sdk.describe_tool(native_tool)
    assert tool_description.native_name in child.tool_names
    events = [
        event
        async for event in cast(Any, native_tool).stream(
            {
                "name": tool_description.native_name,
                "toolUseId": "tool-use-1",
                "input": {"query": "needle"},
            },
            {},
        )
    ]
    assert events[-1]["tool_result"] == {
        "toolUseId": "tool-use-1",
        "status": "success",
        "content": [{"json": {"value": "needle"}}],
    }
    with pytest.raises(ValidationError, match="query"):
        async for _event in cast(Any, native_tool).stream(
            {
                "name": tool_description.native_name,
                "toolUseId": "tool-use-invalid",
                "input": {},
            },
            {},
        ):
            pass


@pytest.mark.parametrize(
    ("approved", "approval_response", "expected_lookups"),
    [
        (True, "yes", ["needle"]),
        (False, "no", []),
    ],
)
@pytest.mark.asyncio
async def test_real_strands_incident_slice_closes_after_delegate_approval_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approved: bool,
    approval_response: str,
    expected_lookups: list[str],
) -> None:
    pytest.importorskip("strands")
    from strands.models import Model

    write_project(tmp_path, scripted_models=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    implementation_module = importlib.import_module("app_impl")
    lookup_name = native_name(
        "tool",
        semantic_id("tool", "records.lookup"),
        "records.lookup",
    )
    delegate_name = native_name(
        "delegate",
        semantic_id("edge", "ask_child"),
        "ask_child",
    )

    class ScriptedModel(Model):
        def __init__(self, responses: Sequence[Mapping[str, object]]) -> None:
            self.responses = list(responses)
            self.index = 0

        def get_config(self) -> Mapping[str, object]:
            return {}

        def update_config(self, **model_config: object) -> None:
            del model_config

        async def structured_output(
            self,
            output_model: type[object],
            prompt: object,
            system_prompt: str | None = None,
            **kwargs: object,
        ) -> AsyncIterator[Mapping[str, object]]:
            del output_model, prompt, system_prompt, kwargs
            raise AssertionError("Strands should use its structured-output tool in this path")
            yield {}  # pragma: no cover

        async def stream(
            self,
            messages: object,
            tool_specs: object = None,
            system_prompt: str | None = None,
            **kwargs: object,
        ) -> AsyncIterator[Mapping[str, object]]:
            del messages, tool_specs, system_prompt, kwargs
            response = self.responses[self.index]
            self.index += 1
            yield {"messageStart": {"role": "assistant"}}
            for content in cast(Sequence[Mapping[str, object]], response["content"]):
                tool_use = cast(Mapping[str, object], content["toolUse"])
                yield {
                    "contentBlockStart": {
                        "start": {
                            "toolUse": {
                                "name": tool_use["name"],
                                "toolUseId": tool_use["toolUseId"],
                            }
                        }
                    }
                }
                yield {
                    "contentBlockDelta": {
                        "delta": {
                            "toolUse": {
                                "input": json.dumps(tool_use["input"]),
                            }
                        }
                    }
                }
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}

    scripted_models = {
        "child": ScriptedModel(
            (
                _tool_use_message(
                    lookup_name,
                    "child-lookup-1",
                    {"query": "needle"},
                ),
                _tool_use_message(
                    "Result",
                    "child-output-1",
                    {"value": "needle"},
                ),
            )
        ),
        "parent": ScriptedModel(
            (
                _tool_use_message(
                    delegate_name,
                    "parent-delegate-1",
                    {"request": {"value": "needle"}},
                ),
                _tool_use_message(
                    "Result",
                    "parent-output-1",
                    {"value": "needle"},
                ),
            )
        ),
    }
    factory_calls: list[tuple[str, Mapping[str, object]]] = []

    def make_model(*, model: str, options: Mapping[str, object]) -> object:
        factory_calls.append((model, dict(options)))
        return scripted_models[cast(str, options["script"])]

    monkeypatch.setattr(implementation_module, "make_model", make_model)
    artifacts = compile_project(tmp_path)
    provider = StrandsMaterializationProvider()
    result = materialize(tmp_path, "strands", "test", provider=provider)
    router = StrandsNormalizedTraceRouter()
    router.attach(result.graph)
    session = router.open_session(
        artifacts.ir,
        result.plan,
        run_id="strands-incident-poc",
    )
    attempt = TraceAttempt("incident:1", "incident-attempt-1", 1)
    parent_id = semantic_id("agent", "Parent")
    approval_tool = result.graph.grant_objects[semantic_id("grant", "Child", "records.lookup")]

    with session:
        with session.bind_attempt(attempt, agent=parent_id):
            interrupted = await cast(Any, result.graph.agent("Parent")).invoke_async("Resolve the incident.")
            assert interrupted.stop_reason == "interrupt"
            assert len(interrupted.interrupts) == 1
            assert implementation_module.LOOKUPS == []
            session.record_approval_requested(native_tool=approval_tool)
            session.record_approval(native_tool=approval_tool, approved=approved)
            completed = await cast(Any, result.graph.agent("Parent")).invoke_async(
                [
                    {
                        "interruptResponse": {
                            "interruptId": interrupted.interrupts[0].id,
                            "response": approval_response,
                        }
                    }
                ]
            )

    accepted = provider.validate_result(result.graph.agent("Parent"), completed)
    assert accepted.value == "needle"
    assert implementation_module.LOOKUPS == expected_lookups
    assert factory_calls == [
        ("test-model", {"script": "child"}),
        ("test-model", {"script": "parent"}),
    ]
    snapshot = session.closed_snapshot
    validate_trace_conformance(artifacts.ir, result.plan, snapshot.trace)
    validate_trace_closure(snapshot.trace, snapshot.closure)
    assessment = assess_trace_evidence(
        snapshot.trace,
        result.plan.expected_event_types,
        closure=snapshot.closure,
    )
    assert snapshot.closure.status == "complete"
    assert assessment.status == "complete"


def test_strands_builds_cyclic_delegate_declarations_in_two_passes(
    tmp_path: Path,
) -> None:
    _write_cyclic_project(tmp_path)

    result = materialize(
        tmp_path,
        "strands",
        "test",
        provider=StrandsMaterializationProvider(FakeStrandsSDK()),
    )

    first = cast(FakeStrandsAgent, result.graph.agent("First"))
    second = cast(FakeStrandsAgent, result.graph.agent("Second"))
    first_edge = cast(
        FakeStrandsTool,
        result.graph.composition_objects[semantic_id("edge", "ask_second")],
    )
    second_edge = cast(
        FakeStrandsTool,
        result.graph.composition_objects[semantic_id("edge", "ask_first")],
    )
    assert first_edge in first.tools
    assert second_edge in second.tools
    assert first_edge.child is second
    assert second_edge.child is first

    strands = pytest.importorskip("strands")
    real = materialize(
        tmp_path,
        "strands",
        "test",
        provider=StrandsMaterializationProvider(),
    )
    assert isinstance(real.graph.agent("First"), strands.Agent)
    assert isinstance(real.graph.agent("Second"), strands.Agent)
    assert len(real.graph.composition_objects) == 2


def test_strands_wraps_named_environment_delegate(
    tmp_path: Path,
) -> None:
    write_project(tmp_path, isolation=True)

    result = materialize(
        tmp_path,
        "strands",
        "test",
        provider=StrandsMaterializationProvider(FakeStrandsSDK()),
    )

    edge = cast(
        FakeStrandsTool,
        result.graph.composition_objects[semantic_id("edge", "ask_child")],
    )
    assert isinstance(edge.environment, InProcessEnvironment)
    isolation = result.plan.isolation[semantic_id("isolation", "CleanContext")]
    assert all(dimension.outcome == "emulated" for dimension in isolation.dimensions.values())
    assert result.graph.environment_evidence[0].provider == "contract4agents.runtime:InProcessEnvironment"


def _tool_use_message(
    name: str,
    tool_use_id: str,
    tool_input: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": tool_use_id,
                    "name": name,
                    "input": dict(tool_input),
                }
            }
        ],
    }


def _write_cyclic_project(root: Path) -> None:
    (root / "system.contract").write_text(
        """\
type Request:
    value: string

type Result:
    value: string

agent First(request: Request) -> Result:
    goal = "Ask the second agent."

agent Second(request: Request) -> Result:
    goal = "Ask the first agent."

composition ask_second from First to Second:
    mode = delegate
    description = "Ask the second agent."
    history = none
    map request = input.request

composition ask_first from Second to First:
    mode = delegate
    description = "Ask the first agent."
    history = none
    map request = input.request
"""
    )
    (root / "contract4agents.targets.toml").write_text(
        """\
schema_version = "1"

[targets.strands]
adapter = "strands"

[targets.strands.profiles.test]
default_model = "test-model"
"""
    )
