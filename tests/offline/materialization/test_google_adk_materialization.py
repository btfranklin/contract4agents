from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, create_model

from contract4agents.adapters._native_names import native_name
from contract4agents.compiler import build_artifacts
from contract4agents.ir import (
    FrozenMap,
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
)
from contract4agents.materialization._types import build_agent_input_types, build_pydantic_types
from contract4agents.target_bindings import (
    BindingEntry,
)
from tests.support.google_adk import (
    FakeAgent,
    FakeGoogleADKSDK,
    FakeTool,
    _provider_ir,
    _target_and_plan,
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
        input_types=build_agent_input_types(ir, output_types),
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
    assert parent.input_type is graph.input_types[parent_id]
    assert child.input_type is graph.input_types[child_id]
    assert parent.native_name == native_name("agent", parent_id, "Parent")
    assert parent.output_mode == "emulated"
    assert child.output_mode == "native"
    assert len(parent.tools) == 2
    assert isinstance(parent.tools[0], FakeTool)
    assert parent.tools[0].requires_approval
    assert isinstance(parent.tools[1], FakeTool)
    assert parent.tools[1].child is child
    assert parent.tools[1].input_type is graph.input_types[child_id]
    assert graph.validation.plan_digest == plan.plan_digest
    assert "materialization.agent.configured" in {event.event_type for event in trace.events}


def test_google_adk_provider_detects_dropped_native_tools(tmp_path: Path) -> None:
    ir = _provider_ir()
    target, plan = _target_and_plan(tmp_path, ir)
    output_types = build_pydantic_types(ir)
    lookup_id = semantic_id("tool", "records.lookup")
    implementations = FrozenMap(((lookup_id, lambda query: {"summary": query}),))

    with pytest.raises(Exception) as caught:
        GoogleADKMaterializationProvider(FakeGoogleADKSDK(drop_tools=True)).build_graph(
            ir=ir,
            artifacts=build_artifacts(ir),
            target=target,
            plan=plan,
            implementations=implementations,
            input_types=build_agent_input_types(ir, output_types),
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

    sdk = ADKSDK()
    tool = sdk.create_function_tool(
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
    description = sdk.describe_tool(tool)

    assert description.input_schema == input_type.model_json_schema()
    assert description.output_schema == output_adapter.json_schema()

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
        model_options={
            "model_factory": "app:factory",
            "temperature": 0.2,
            "custom": {"entries": ({"enabled": True},)},
        },
        model_factory=factory,
        input_type=None,
        output_type=output_type,
        output_mode="emulated",
        tools=(),
    )

    assert calls == [
        (
            "custom-model",
            {"temperature": 0.2, "custom": {"entries": [{"enabled": True}]}},
        )
    ]
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
        result = '{"results":[{"title":"Source","url":"https://example.test","snippet":"Supported fact."}]}'

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
            cast_context.state["temp:_adk_grounding_metadata"] = {"renderedContent": "<div>Search suggestion</div>"}
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

    FakeAgentTool.result = '{"results":[{"title":"Source","url":"https://example.test"}]}'
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
        self.tool_confirmation = _Confirmation(confirmed) if confirmed is not None else None
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
