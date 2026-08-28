from __future__ import annotations

import importlib
import json
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from contract4agents import compile_project, materialize
from contract4agents.adapters._native_names import native_name
from contract4agents.ir import FrozenMap, SemanticId, freeze_json, semantic_id
from contract4agents.materialization import (
    MaterializationError,
    RecordingMaterializationTraceSink,
)
from contract4agents.materialization._strands import (
    NativeStrandsAgentDescription,
    NativeStrandsToolDescription,
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


@dataclass
class FakeStrandsModel:
    identity: str
    options: Mapping[str, object]


@dataclass
class FakeStrandsTool:
    native_name: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    implementation: object | None = None
    child: object | None = None
    environment: object | None = None


@dataclass
class FakeStrandsAgent:
    native_name: str
    contract_name: str
    instructions: str
    model_identity: str
    model: object
    output_type: type[object]
    tools: list[object]
    approval_allowed_tools: tuple[str, ...] | None


@dataclass
class FakeStrandsSDK:
    version: str = "fake-strands-1"
    drop_attached_tools: bool = False
    model_factory_calls: list[
        tuple[str, Mapping[str, object], object | None]
    ] = field(default_factory=list)

    def create_model(
        self,
        *,
        model: str,
        model_options: Mapping[str, object],
        factory: object | None,
    ) -> object:
        self.model_factory_calls.append((model, model_options, factory))
        if factory is None:
            return FakeStrandsModel(model, dict(model_options))
        assert callable(factory)
        return cast(Any, factory)(model=model, options=model_options)

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
        return FakeStrandsAgent(
            native_name,
            contract_name,
            instructions,
            model_identity,
            model,
            output_type,
            list(tools),
            approval_allowed_tools,
        )

    def create_function_tool(
        self,
        *,
        native_name: str,
        description: str,
        implementation: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object:
        return FakeStrandsTool(
            native_name,
            description,
            _input_schema(input_type),
            output_adapter.json_schema(),
            implementation=implementation,
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
        return FakeStrandsTool(
            native_name,
            description,
            _input_schema(input_type),
            output_adapter.json_schema(),
            child=child,
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
        del isolation_id, requested_dimensions, declared_capabilities
        return FakeStrandsTool(
            native_name,
            description,
            _input_schema(input_type),
            output_adapter.json_schema(),
            child=child,
            environment=environment,
        )

    def attach(self, agent: object, *, tools: tuple[object, ...]) -> None:
        assert isinstance(agent, FakeStrandsAgent)
        agent.tools = [] if self.drop_attached_tools else list(tools)

    def describe_agent(self, agent: object) -> NativeStrandsAgentDescription:
        assert isinstance(agent, FakeStrandsAgent)
        return NativeStrandsAgentDescription(
            agent.native_name,
            agent.instructions,
            agent.model_identity,
            agent.output_type,
            tuple(
                item.native_name
                for item in agent.tools
                if isinstance(item, FakeStrandsTool)
            ),
            agent.approval_allowed_tools,
        )

    def describe_tool(self, tool: object) -> NativeStrandsToolDescription:
        assert isinstance(tool, FakeStrandsTool)
        return NativeStrandsToolDescription(
            tool.native_name,
            tool.description,
            tool.input_schema,
            tool.output_schema,
        )

    def validate_result(self, agent: object, result: object) -> object:
        description = self.describe_agent(agent)
        value = getattr(result, "structured_output", None)
        if value is None:
            raise ValueError("missing structured output")
        return TypeAdapter(description.output_type).validate_python(value)


def test_strands_provider_builds_validated_graph_with_exact_controls(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)
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
    approval = result.plan.controls[
        semantic_id("control", "Child", "approval", "records.lookup")
    ]
    assert approval.outcome == "exact"
    edge_id = semantic_id("edge", "ask_child")
    assert result.plan.composition[edge_id].outcome == "emulated"
    assert any(
        "model-supplied delegate values" in obligation.description
        for obligation in result.plan.host_obligations
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
    child_tools = cast(list[FakeStrandsTool], child.tools)
    assert [tool.native_name for tool in child_tools] == [
        native_name(
            "tool",
            semantic_id("tool", "records.lookup"),
            "records.lookup",
        )
    ]
    assert child_tools[0].implementation is result.graph.implementations[
        semantic_id("tool", "records.lookup")
    ]
    assert result.graph.implementations[
        semantic_id("datasource", "records.current")
    ]
    assert result.graph.implementations[
        semantic_id("external", "request_context")
    ]

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
    _write_project(tmp_path, model_factory=True)
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
    assert all(
        isinstance(agent.model, dict)
        for agent in cast(list[FakeStrandsAgent], list(result.agents.values()))
    )
    assert all(
        set(options) == {"temperature"}
        for _, options, _factory in sdk.model_factory_calls
    )


def test_strands_provider_rejects_a_native_graph_that_drops_tools(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)

    with pytest.raises(MaterializationError) as caught:
        materialize(
            tmp_path,
            "strands",
            "test",
            provider=StrandsMaterializationProvider(
                FakeStrandsSDK(drop_attached_tools=True)
            ),
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
        {
            "additional_request_fields": {
                "reasoning_config": {"type": "enabled", "budget_tokens": 1024}
            }
        }
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
    request = model.format_request(
        [{"role": "user", "content": [{"text": "test"}]}]
    )

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
    _write_project(tmp_path, async_lookup=async_lookup)
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

    native_tool = result.graph.grant_objects[
        semantic_id("grant", "Child", "records.lookup")
    ]
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

    _write_project(tmp_path, scripted_models=True)
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
            raise AssertionError(
                "Strands should use its structured-output tool in this path"
            )
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
    approval_tool = result.graph.grant_objects[
        semantic_id("grant", "Child", "records.lookup")
    ]

    with session:
        with session.bind_attempt(attempt, agent=parent_id):
            interrupted = await cast(Any, result.graph.agent("Parent")).invoke_async(
                "Resolve the incident."
            )
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
    _write_project(tmp_path, isolation=True)

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
    isolation = result.plan.isolation[
        semantic_id("isolation", "CleanContext")
    ]
    assert all(
        dimension.outcome == "emulated"
        for dimension in isolation.dimensions.values()
    )
    assert (
        result.graph.environment_evidence[0].provider
        == "contract4agents.runtime:InProcessEnvironment"
    )


def _input_schema(input_type: type[object] | None) -> Mapping[str, object]:
    if input_type is None:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    return cast(dict[str, object], cast(Any, input_type).model_json_schema())


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


def _write_project(
    root: Path,
    *,
    model_factory: bool = False,
    scripted_models: bool = False,
    async_lookup: bool = False,
    isolation: bool = False,
) -> None:
    sys.modules.pop("app_impl", None)
    isolation_source = ""
    edge_isolation = ""
    environment_binding = ""
    if isolation:
        isolation_source = """\
isolation CleanContext:
    context = explicit_only
    capabilities = declared_only
    state = fresh
    return = final_output_only

"""
        edge_isolation = "    isolation = CleanContext\n"
        environment_binding = """\
[targets.strands.environments.in_process]
provider = "contract4agents.runtime:InProcessEnvironment"

"""
    (root / "system.contract").write_text(
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
    cache = run

external_context request_context -> Request:
    description = "Invocation metadata."
    sensitivity = internal
    render = markdown

{isolation_source}agent Child(request: Request) -> Result:
    use records.lookup:
        availability = enabled
        authorization = approval_required
        execution = host
    context current: Result from datasource records.current:
        map query = input.request.value
    context metadata: Request from external request_context
    goal = "Use the declared lookup tool."

agent Parent(request: Request) -> Result:
    goal = "Delegate to the child."

composition ask_child from Parent to Child:
    mode = delegate
    description = "Ask the child for a result."
    history = none
    map request = input.request
{edge_isolation}
"""
    )
    lookup_definition = (
        """\
async def lookup(query):
    LOOKUPS.append(query)
    return {"value": query}
"""
        if async_lookup
        else """\
def lookup(query):
    LOOKUPS.append(query)
    return {"value": query}
"""
    )
    (root / "app_impl.py").write_text(
        f"""\
FACTORY_CALLS = []
LOOKUPS = []

{lookup_definition}

def current(query):
    return {{"value": query}}

def context():
    return {{"value": "context"}}

def make_model(*, model, options):
    FACTORY_CALLS.append((model, dict(options)))
    return {{"model": model, "options": dict(options)}}
"""
    )
    profile_options = ""
    if model_factory:
        profile_options = """\
[targets.strands.profiles.test.options]
model_factory = "app_impl:make_model"
temperature = 0.2
"""
    if scripted_models:
        profile_options = """\
[targets.strands.profiles.test.options]
model_factory = "app_impl:make_model"

[targets.strands.profiles.test.agents.Child.options]
script = "child"

[targets.strands.profiles.test.agents.Parent.options]
script = "parent"
"""
    (root / "contract4agents.targets.toml").write_text(
        f"""\
schema_version = "1"

[targets.strands]
adapter = "strands"

[targets.strands.tools."records.lookup"]
python = "app_impl:lookup"

[targets.strands.datasources."records.current"]
python = "app_impl:current"

[targets.strands.external_context.request_context]
python = "app_impl:context"

[targets.strands.profiles.test]
default_model = "test-model"

{environment_binding}{profile_options}"""
    )


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
