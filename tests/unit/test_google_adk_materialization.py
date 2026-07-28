from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, create_model

from contract4agents.adapters._native_names import native_name
from contract4agents.compiler import build_artifacts
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
from contract4agents.materialization import (
    MaterializationError,
    RecordingMaterializationTraceSink,
)
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._google_adk import (
    ADKSDK,
    GoogleADKMaterializationProvider,
    GoogleADKNativeAgentDescription,
    OutputMode,
)
from contract4agents.materialization._types import build_pydantic_types
from contract4agents.planning import MaterializationPlan, plan_materialization
from contract4agents.target_bindings import (
    BindingEntry,
    TargetBinding,
    TargetBindings,
    TargetProfile,
)


@dataclass
class FakeTool:
    name: str
    implementation: object | None = None
    requires_approval: bool = False
    child: object | None = None


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

    def __init__(self, *, drop_tools: bool = False) -> None:
        self.drop_tools = drop_tools
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
        del description, input_type, output_adapter
        return FakeTool(native_name, implementation, requires_approval)

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
        del child_name, description, binding, input_type, output_adapter
        return FakeTool(native_name, requires_approval=requires_approval)

    def create_delegate_tool(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object:
        del description, input_type, output_adapter
        return FakeTool(native_name, child=child)

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
            input_type,
            output_adapter,
            isolation_id,
            requested_dimensions,
            declared_capabilities,
            environment,
        )
        return FakeTool(native_name, child=child)

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


def test_google_adk_provider_builds_and_validates_typed_graph(
    tmp_path: Path,
) -> None:
    ir = _provider_ir()
    target, plan = _target_and_plan(tmp_path, ir)
    output_types = build_pydantic_types(ir)
    lookup_id = semantic_id("tool", "records.lookup")

    def lookup(query: str) -> dict[str, str]:
        return {"summary": query}

    implementations = FrozenMap(((lookup_id, lookup),))
    context = ContextRuntime(ir, plan, implementations, output_types)
    trace = RecordingMaterializationTraceSink()
    sdk = FakeGoogleADKSDK()

    graph = GoogleADKMaterializationProvider(sdk).build_graph(
        ir=ir,
        artifacts=build_artifacts(ir),
        target=target,
        plan=plan,
        implementations=implementations,
        output_types=output_types,
        context_runtime=context,
        environment=None,
        materialization_trace_sink=trace,
    )

    parent_id = semantic_id("agent", "Parent")
    child_id = semantic_id("agent", "Child")
    parent = graph.agents[parent_id]
    child = graph.agents[child_id]
    assert isinstance(parent, FakeAgent)
    assert isinstance(child, FakeAgent)
    assert parent.native_name == native_name("agent", parent_id, "Parent")
    assert parent.output_mode == "emulated"
    assert child.output_mode == "native"
    assert len(parent.tools) == 2
    assert isinstance(parent.tools[0], FakeTool)
    assert parent.tools[0].requires_approval
    assert isinstance(parent.tools[1], FakeTool)
    assert parent.tools[1].child is child
    assert graph.validation.plan_digest == plan.plan_digest
    assert "materialization.agent.configured" in {
        event.event_type for event in trace.events
    }


def test_google_adk_provider_detects_dropped_native_tools(tmp_path: Path) -> None:
    ir = _provider_ir()
    target, plan = _target_and_plan(tmp_path, ir)
    output_types = build_pydantic_types(ir)
    lookup_id = semantic_id("tool", "records.lookup")
    implementations = FrozenMap(((lookup_id, lambda query: {"summary": query}),))

    with pytest.raises(Exception) as caught:
        GoogleADKMaterializationProvider(
            FakeGoogleADKSDK(drop_tools=True)
        ).build_graph(
            ir=ir,
            artifacts=build_artifacts(ir),
            target=target,
            plan=plan,
            implementations=implementations,
            output_types=output_types,
            context_runtime=ContextRuntime(
                ir,
                plan,
                implementations,
                output_types,
            ),
            environment=None,
            materialization_trace_sink=RecordingMaterializationTraceSink(),
        )

    assert "MAT426" in str(caught.value)


@pytest.mark.asyncio
async def test_concrete_adk_tool_validates_approval_sync_async_and_outputs() -> None:
    input_type = create_model("LookupInput", query=(str, ...))
    output_type = create_model("LookupOutput", summary=(str, ...))
    output_adapter = TypeAdapter(output_type)
    calls: list[tuple[str, int]] = []
    main_thread = threading.get_ident()

    def sync_lookup(query: str) -> dict[str, str]:
        calls.append((query, threading.get_ident()))
        return {"summary": query.upper()}

    tool = ADKSDK().create_function_tool(
        native_name="c4a_tool_lookup_deadbeef",
        description="Look up a record.",
        implementation=sync_lookup,
        input_type=input_type,
        output_adapter=output_adapter,
        requires_approval=True,
    )
    waiting = _ToolContext()
    rejected = _ToolContext(confirmed=False)
    approved = _ToolContext(confirmed=True)

    assert await tool.run_async(
        args={"query": "wait"},
        tool_context=waiting,
    ) == {"error": "Tool execution is awaiting approval."}
    assert waiting.requested
    assert await tool.run_async(
        args={"query": "reject"},
        tool_context=rejected,
    ) == {"error": "Tool execution was rejected."}
    assert calls == []
    assert await tool.run_async(
        args={"query": "approved"},
        tool_context=approved,
    ) == {"summary": "APPROVED"}
    assert calls == [("approved", calls[0][1])]
    assert calls[0][1] != main_thread

    async def async_lookup(query: str) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"summary": query}

    async_tool = ADKSDK().create_function_tool(
        native_name="c4a_tool_async_deadbeef",
        description="Look up asynchronously.",
        implementation=async_lookup,
        input_type=input_type,
        output_adapter=output_adapter,
        requires_approval=False,
    )
    assert await async_tool.run_async(
        args={"query": "async"},
        tool_context=_ToolContext(),
    ) == {"summary": "async"}


def test_concrete_adk_agent_uses_factory_once_without_reapplying_options() -> None:
    from google.adk.models import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class FakeModel(BaseLlm):
        async def generate_content_async(
            self,
            llm_request: Any,
            stream: bool = False,
        ) -> AsyncGenerator[Any, None]:
            del llm_request, stream
            if False:
                yield None

    calls: list[tuple[str, dict[str, object]]] = []

    def factory(*, model: str, options: Mapping[str, object]) -> object:
        calls.append((model, dict(options)))
        return FakeModel(model=model)

    output_type = create_model("FactoryOutput", summary=(str, ...))
    agent = ADKSDK().create_agent(
        semantic_name="FactoryAgent",
        native_name="c4a_agent_factory_deadbeef",
        description="Factory-backed agent.",
        instructions="Produce an answer.",
        model="custom-model",
        model_options={"model_factory": "app:factory", "temperature": 0.2},
        model_factory=factory,
        input_type=None,
        output_type=output_type,
        output_mode="emulated",
        tools=(),
    )

    assert calls == [("custom-model", {"temperature": 0.2})]
    assert isinstance(agent.model, FakeModel)
    assert agent.output_schema is None
    assert agent.generate_content_config.temperature is None
    assert "Return only one JSON value" in agent.instruction
    assert '"summary"' in agent.instruction
    callback = agent.canonical_after_model_callbacks[0]
    valid = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text='{"summary":"ok"}')],
        )
    )
    invalid = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text='{"wrong":"shape"}')],
        )
    )
    assert callback(None, valid) is None
    with pytest.raises(ValueError, match="failed contract schema validation"):
        callback(None, invalid)


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (lambda **_kwargs: object(), "MAT324"),
        (
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("factory failed")),
            "MAT323",
        ),
    ],
)
def test_concrete_adk_agent_wraps_factory_failures(
    factory: object,
    code: str,
) -> None:
    output_type = create_model("FactoryFailureOutput", summary=(str, ...))

    with pytest.raises(MaterializationError) as caught:
        ADKSDK().create_agent(
            semantic_name="FactoryAgent",
            native_name="c4a_agent_factory_deadbeef",
            description="Factory-backed agent.",
            instructions="Produce an answer.",
            model="custom-model",
            model_options={"model_factory": "app:factory"},
            model_factory=factory,
            input_type=None,
            output_type=output_type,
            output_mode="emulated",
            tools=(),
        )

    assert [issue.code for issue in caught.value.issues] == [code]


def test_concrete_adk_search_tool_builds_without_network() -> None:
    input_type = create_model("SearchInput", query=(str, ...))
    result_type = create_model(
        "SearchResult",
        title=(str, ...),
        url=(str, ...),
        snippet=(str, ...),
    )
    output_type = create_model("SearchOutput", results=(list[result_type], ...))

    tool = ADKSDK().create_google_search_tool(
        native_name="c4a_tool_web_search_deadbeef",
        child_name="c4a_search_web_search_deadbeef",
        description="Search the web.",
        binding=BindingEntry(
            {
                "provider": "google_adk",
                "tool": "google_search",
                "model": "gemini-2.5-flash",
            }
        ),
        input_type=input_type,
        output_adapter=TypeAdapter(output_type),
        requires_approval=False,
    )

    declaration = tool._get_declaration()
    assert tool.name == "c4a_tool_web_search_deadbeef"
    assert declaration.parameters_json_schema["required"] == ["query"]


@pytest.mark.asyncio
async def test_google_search_wrapper_preserves_grounding_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import google.adk.tools.agent_tool as agent_tool_module

    class FakeAgentTool:
        result = (
            '{"results":[{"title":"Source","url":"https://example.test",'
            '"snippet":"Supported fact."}]}'
        )

        def __init__(
            self,
            *,
            agent: object,
            skip_summarization: bool,
            include_plugins: bool,
            propagate_grounding_metadata: bool,
        ) -> None:
            del agent, skip_summarization, include_plugins
            assert propagate_grounding_metadata

        async def run_async(
            self,
            *,
            args: dict[str, object],
            tool_context: object,
        ) -> str:
            assert args == {"request": "contract semantics"}
            cast_context = tool_context
            cast_context.state["temp:_adk_grounding_metadata"] = {
                "renderedContent": "<div>Search suggestion</div>"
            }
            return self.result

    monkeypatch.setattr(agent_tool_module, "AgentTool", FakeAgentTool)
    input_type = create_model("GroundedSearchInput", query=(str, ...))
    result_type = create_model(
        "GroundedSearchResult",
        title=(str, ...),
        url=(str, ...),
        snippet=(str, ...),
    )
    output_type = create_model(
        "GroundedSearchOutput",
        results=(list[result_type], ...),
    )
    tool = ADKSDK().create_google_search_tool(
        native_name="c4a_tool_grounded_search_deadbeef",
        child_name="c4a_search_grounded_search_deadbeef",
        description="Search the web.",
        binding=BindingEntry(
            {
                "provider": "google_adk",
                "tool": "google_search",
                "model": "gemini-2.5-flash",
            }
        ),
        input_type=input_type,
        output_adapter=TypeAdapter(output_type),
        requires_approval=False,
    )
    context = _ToolContext()

    assert await tool.run_async(
        args={"query": "contract semantics"},
        tool_context=context,
    ) == {
        "results": [
            {
                "title": "Source",
                "url": "https://example.test",
                "snippet": "Supported fact.",
            }
        ]
    }
    assert "renderedContent" in context.state["temp:_adk_grounding_metadata"]

    FakeAgentTool.result = (
        '{"results":[{"title":"Source","url":"https://example.test"}]}'
    )
    with pytest.raises(ValueError):
        await tool.run_async(
            args={"query": "contract semantics"},
            tool_context=context,
        )


@dataclass
class _Confirmation:
    confirmed: bool


@dataclass
class _Actions:
    skip_summarization: bool = False


class _ToolContext:
    def __init__(self, *, confirmed: bool | None = None) -> None:
        self.tool_confirmation = (
            _Confirmation(confirmed) if confirmed is not None else None
        )
        self.actions = _Actions()
        self.requested = False
        self.state: dict[str, object] = {}

    def request_confirmation(
        self,
        *,
        hint: str | None = None,
        payload: object | None = None,
    ) -> None:
        del hint, payload
        self.requested = True


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
