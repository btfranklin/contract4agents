from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from agents import (
    Agent,
    FunctionTool,
    Model,
    ModelResponse,
    RunResult,
    Usage,
    WebSearchTool,
    handoff,
    set_tracing_disabled,
)
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from contract4agents.adapters.openai import (
    OpenAIAgentFactoryError,
    OpenAIApprovalRequest,
    OpenAISemanticJudge,
    OpenAIToolRegistration,
    build_openai_agent,
    build_openai_agents_from_contracts,
    build_openai_agents_from_plan,
    build_openai_output_type_registry,
    contract_tool_name,
    openai_tool_name,
    plan_openai_agents_from_contracts,
    run_openai_agent,
    run_openai_agent_with_contract,
)
from contract4agents.compiler import compile_project
from contract4agents.monitor import MonitorRule, run_monitors
from contract4agents.runtime import ContextValue, RuntimeContext
from tests.fixtures.pydantic_models import ResearchSummaryModel

ROOT = Path(__file__).resolve().parents[2]
HOSTED_TOOL_AGENT_CONFIGS = ROOT / "tests" / "fixtures" / "contract_projects" / "hosted-tool-agent-configs"
PYDANTIC_FIXTURE = ROOT / "tests" / "fixtures" / "contract_projects" / "pydantic-model-interop"


@pytest.fixture(autouse=True)
def _disable_openai_sdk_tracing() -> Any:
    set_tracing_disabled(True)
    yield
    set_tracing_disabled(False)


class _ScriptedModel(Model):
    def __init__(self, responses: list[list[Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[SimpleNamespace] = []

    async def get_response(
        self,
        system_instructions: str | None,
        input: Any,
        model_settings: Any,
        tools: list[Any],
        output_schema: Any,
        handoffs: list[Any],
        tracing: Any,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> ModelResponse:
        self.calls.append(
            SimpleNamespace(
                system_instructions=system_instructions,
                input=input,
                model_settings=model_settings,
                tools=tools,
                output_schema=output_schema,
                handoffs=handoffs,
                tracing=tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            )
        )
        if not self.responses:
            raise AssertionError("Scripted OpenAI model received an unexpected call")
        return ModelResponse(
            output=self.responses.pop(0),
            usage=Usage(),
            response_id=f"response-{len(self.calls)}",
        )

    async def stream_response(self, *_args: Any, **_kwargs: Any) -> Any:
        if False:
            yield None


def _message_output(text: str, *, item_id: str = "message-1") -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id=item_id,
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )


def _function_call_output(
    name: str,
    arguments: dict[str, Any],
    *,
    call_id: str = "call-1",
) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=json.dumps(arguments),
        call_id=call_id,
        name=name,
        type="function_call",
    )


@pytest.mark.asyncio
async def test_openai_adapter_constructs_and_translates_real_sdk_result() -> None:
    model = _ScriptedModel([[_message_output("done")]])

    agent = build_openai_agent({"agent": "A", "model": model}, "instructions")
    result = await run_openai_agent(agent, "hello")

    assert isinstance(agent, Agent)
    assert agent.name == "A"
    assert model.calls[0].input == [{"content": "hello", "role": "user"}]
    assert result.final_output == "done"
    assert result.last_agent == "A"
    assert isinstance(result.raw_result, RunResult)


@pytest.mark.asyncio
async def test_openai_semantic_judge_with_mocked_client(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("openai")
    outputs = iter(["PASS", "NOT PASS"])

    class Responses:
        async def create(self, **_kwargs: object) -> object:
            return SimpleNamespace(output_text=next(outputs))

    class FakeClient:
        def __init__(self) -> None:
            self.responses = Responses()

    module.AsyncOpenAI = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    judge = OpenAISemanticJudge(model="test")
    assert await judge.judge(output={"answer": "ok"}, criterion="is ok")
    assert not await judge.judge(output={"answer": "bad"}, criterion="is ok")


def test_openai_adapter_plan_includes_contract_metadata() -> None:
    child_tool = object()

    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(
            include_hosted_tool=True,
            composition=["agent_as_tool(ChildAgent)"],
            parent_assertions=["expect(output.ok == true)"],
            parent_inputs=[{"name": "request", "type": "Request", "required": True}],
            parent_datasources=[
                {
                    "name": "RequestContext",
                    "python": "app.context:load",
                    "produces": "Request",
                    "requires": [],
                    "render": "markdown",
                    "cache": "run",
                }
            ],
        ),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        tool_registry={"tools.lookup": object()},
        hosted_tool_registry={"openai.web_search": object()},
        agent_tool_registry={"ChildAgent": child_tool},
    )

    parent = plan.agents["ParentAgent"]
    assert parent.source_path == "agents/parent.contract"
    assert parent.instruction_ref == "instructions/ParentAgent.md"
    assert parent.output_schema_ref == "schemas/ParentResult.json"
    assert parent.assertions == ["expect(output.ok == true)"]
    assert parent.inputs[0]["name"] == "request"
    assert parent.datasources[0]["name"] == "RequestContext"
    assert parent.hosted_tools[0].name == "openai.web_search"
    assert parent.tools[0].name == "tools.lookup"
    assert parent.composition[0].mode == "agent_as_tool"
    assert parent.composition[0].sdk_object is child_tool


def test_openai_agent_factory_builds_real_agents_from_typed_plan() -> None:
    parent_model = _ScriptedModel([])
    child_model = _ScriptedModel([])
    child_sdk_agent = Agent(name="ChildSdkAgent", instructions="child", model=child_model)
    child_tool = child_sdk_agent.as_tool(tool_name="child_agent", tool_description="Run the child agent")

    def lookup(query: str) -> str:
        """Look up a query."""

        return query

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_hosted_tool=True, composition=["agent_as_tool(ChildAgent)"]),
        model_registry={"ParentAgent": parent_model},
        tool_registry={"tools.lookup": lookup},
        hosted_tool_registry={"openai.web_search": True},
        agent_tool_registry={"ChildAgent": child_tool},
        instruction_overrides={"ParentAgent": "override"},
        default_model=child_model,
        generate_output_types=True,
    )

    parent = result.agents["ParentAgent"]
    child = result.agents["ChildAgent"]
    assert result.plan.agents["ParentAgent"].instructions == "override"
    assert result.caveats == []
    assert isinstance(parent, Agent)
    assert parent.model is parent_model
    assert parent.instructions == "override"
    assert isinstance(parent.tools[0], FunctionTool)
    assert parent.tools[0].name == openai_tool_name("tools.lookup")
    assert isinstance(parent.tools[1], WebSearchTool)
    assert parent.tools[2] is child_tool
    assert parent.handoffs == []
    assert parent.output_type.__name__ == "ParentResult"
    assert child.model is child_model
    assert child.output_type.__name__ == "ChildResult"


def test_openai_agent_factory_builds_real_agent_from_existing_plan() -> None:
    model = _ScriptedModel([])

    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, include_agent_dependency=False),
        model_registry={"ParentAgent": model, "ChildAgent": model},
        generate_output_types=True,
    )
    result = build_openai_agents_from_plan(plan)

    assert result.plan is plan
    assert isinstance(result.agents["ParentAgent"], Agent)
    assert result.agents["ParentAgent"].model is model


def test_openai_tool_name_mapping_is_injective_for_dots_and_underscores() -> None:
    first = openai_tool_name("a.b__c")
    second = openai_tool_name("a__b.c")

    assert first != second
    assert contract_tool_name(first) == "a.b__c"
    assert contract_tool_name(second) == "a__b.c"
    assert contract_tool_name("plain_tool") == "plain_tool"
    with pytest.raises(OpenAIAgentFactoryError, match="ambiguous legacy"):
        contract_tool_name("a__b")


def test_openai_agent_factory_reports_unwired_agent_dependency() -> None:
    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
    )

    assert [caveat.kind for caveat in plan.caveats] == ["agent_dependency_unwired"]
    assert plan.agents["ParentAgent"].composition[0].mode == "unwired"


def test_openai_agent_factory_rejects_missing_output_type() -> None:
    with pytest.raises(OpenAIAgentFactoryError, match="No output type registered"):
        plan_openai_agents_from_contracts(
            _factory_artifacts(),
            output_type_registry={"ChildResult": list},
            model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
            tool_registry={"tools.lookup": object()},
        )


def test_openai_agent_factory_rejects_missing_declared_tool() -> None:
    with pytest.raises(OpenAIAgentFactoryError, match="No host tool registered"):
        plan_openai_agents_from_contracts(
            _factory_artifacts(),
            output_type_registry={"ParentResult": dict, "ChildResult": list},
            model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        )


def test_openai_agent_factory_rejects_missing_hosted_tool() -> None:
    with pytest.raises(OpenAIAgentFactoryError, match="No hosted tool registered"):
        plan_openai_agents_from_contracts(
            _factory_artifacts(include_tool=False, include_hosted_tool=True, include_agent_dependency=False),
            output_type_registry={"ParentResult": dict, "ChildResult": list},
            model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        )


def test_openai_agent_factory_rejects_missing_model() -> None:
    with pytest.raises(OpenAIAgentFactoryError, match="No model configured"):
        plan_openai_agents_from_contracts(
            _factory_artifacts(include_tool=False),
            output_type_registry={"ParentResult": dict, "ChildResult": list},
            model_registry={"ParentAgent": "parent-model"},
        )


def test_openai_agent_factory_omits_denied_guard_tool() -> None:
    child_tool = object()

    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(
            tool_permission="denied",
            composition=["agent_as_tool(ChildAgent)"],
            guard_plan=[
                _guard_item(
                    "denied_tool",
                    "adapter_tool_omission",
                    "forbid(tool.tools.lookup)",
                    target="tools.lookup",
                    declared_permission="denied",
                )
            ],
        ),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        agent_tool_registry={"ChildAgent": child_tool},
    )

    assert plan.agents["ParentAgent"].tools == []
    assert plan.agents["ParentAgent"].composition[0].sdk_object is child_tool
    assert [caveat.kind for caveat in plan.caveats] == ["denied_tool_omitted"]


def test_openai_agent_factory_builds_enabled_real_openai_web_search() -> None:
    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, include_hosted_tool=True, include_agent_dependency=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        hosted_tool_registry={"openai.web_search": True},
    )

    hosted_tool = result.agents["ParentAgent"].tools[0]
    assert isinstance(hosted_tool, WebSearchTool)
    assert hosted_tool.search_context_size == "medium"
    assert result.plan.agents["ParentAgent"].hosted_tools[0].config == {"context_size": "medium"}


def test_openai_agent_factory_uses_each_manifest_hosted_tool_config() -> None:
    result = build_openai_agents_from_contracts(
        compile_project(HOSTED_TOOL_AGENT_CONFIGS),
        output_type_registry={
            "ResearchAgenda": dict,
            "SectionResearchBrief": dict,
            "SynthesisBrief": dict,
            "VerificationReport": dict,
        },
        model_registry={
            "ResearchManagerAgent": "test-model",
            "SectionResearchAgent": "test-model",
            "SynthesisAgent": "test-model",
            "VerifierAgent": "test-model",
        },
        hosted_tool_registry={"openai.web_search": True},
    )

    assert {
        agent_name: agent_plan.hosted_tools[0].config
        for agent_name, agent_plan in result.plan.agents.items()
        if agent_plan.hosted_tools
    } == {
        "ResearchManagerAgent": {"context_size": "medium"},
        "SectionResearchAgent": {"context_size": "high"},
        "VerifierAgent": {"context_size": "medium"},
    }
    assert {
        agent_name: agent.tools[0].search_context_size
        for agent_name, agent in result.agents.items()
        if agent.tools
    } == {
        "ResearchManagerAgent": "medium",
        "SectionResearchAgent": "high",
        "VerifierAgent": "medium",
    }
    assert result.agents["SynthesisAgent"].tools == []


def test_openai_agent_factory_omits_denied_hosted_tool() -> None:
    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(
            include_tool=False,
            include_hosted_tool=True,
            hosted_tool_permission="denied",
            include_agent_dependency=False,
        ),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        hosted_tool_registry={"openai.web_search": object()},
    )

    assert plan.agents["ParentAgent"].hosted_tools == []
    assert [caveat.kind for caveat in plan.caveats] == ["denied_hosted_tool_omitted"]


def test_openai_agent_factory_wraps_raw_callable_with_real_sdk_approval() -> None:
    def lookup(identifier: str) -> dict[str, str]:
        """Look up an identifier."""

        return {"identifier": identifier}

    result = build_openai_agents_from_contracts(
        _factory_artifacts(tool_permission="requires_approval", include_agent_dependency=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        tool_registry={"tools.lookup": OpenAIToolRegistration(lookup, raw_callable=True, description="Lookup")},
    )

    tool = result.agents["ParentAgent"].tools[0]
    assert isinstance(tool, FunctionTool)
    assert tool.name == openai_tool_name("tools.lookup")
    assert tool.needs_approval is True
    assert tool.description == "Lookup"
    assert result.plan.agents["ParentAgent"].tools[0].wrapped


def test_openai_agent_factory_reports_unverified_approval_for_prebuilt_tool() -> None:
    tool = SimpleNamespace(name=openai_tool_name("tools.lookup"))

    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(
            tool_permission="requires_approval",
            include_agent_dependency=False,
            guard_plan=[
                _guard_item(
                    "approval_required_tool",
                    "host_approval_required",
                    "forbid(tool.tools.lookup unless approved_by_human)",
                    target="tools.lookup",
                    declared_permission="requires_approval",
                )
            ],
        ),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        tool_registry={"tools.lookup": tool},
    )

    assert plan.agents["ParentAgent"].tools[0].tool is tool
    assert [caveat.kind for caveat in plan.caveats] == ["approval_enforcement_unverified"]


def test_openai_agent_factory_reports_unsupported_guard_caveat() -> None:
    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(
            include_tool=False,
            include_agent_dependency=False,
            guard_plan=[
                _guard_item(
                    "unsupported",
                    "unsupported",
                    "expect(output.ok == true)",
                    status="unsupported",
                    message="Guard syntax is valid but unsupported.",
                )
            ],
        ),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
    )

    assert [caveat.kind for caveat in plan.caveats] == ["unsupported_guard"]


def test_openai_agent_factory_maps_output_guard_when_registered() -> None:
    child_tool = object()

    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(
            include_tool=False,
            composition=["agent_as_tool(ChildAgent)"],
            guard_plan=[
                _guard_item(
                    "output_conformance",
                    "output_schema",
                    "require(output conforms ParentResult)",
                    output_type="ParentResult",
                )
            ],
        ),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        agent_tool_registry={"ChildAgent": child_tool},
    )

    assert plan.agents["ParentAgent"].output_type is dict
    assert plan.caveats == []


def test_openai_agent_factory_maps_handoff_composition() -> None:
    handoff = object()

    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, composition=["handoff(ChildAgent)"]),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        handoff_registry={"ChildAgent": handoff},
    )

    assert plan.agents["ParentAgent"].tools == []
    assert plan.agents["ParentAgent"].composition[0].sdk_object is handoff
    assert plan.agents["ParentAgent"].composition[0].mode == "handoff"


def test_valid_composition_source_compiles_and_maps_in_openai_adapter(
    tmp_path: Path,
) -> None:
    child_tool = object()
    (tmp_path / "agents.contract").write_text(
        """
type Result:
    ok: bool

agent Parent() -> Result:
    use agent Child from ./child
    composition = [agent_as_tool(Child)]
    goal = "parent"

agent Child() -> Result:
    goal = "child"
""".strip()
    )

    plan = plan_openai_agents_from_contracts(
        compile_project(tmp_path),
        output_type_registry={"Result": dict},
        model_registry={"Parent": "parent-model", "Child": "child-model"},
        agent_tool_registry={"Child": child_tool},
    )

    assert plan.agents["Parent"].composition[0].sdk_object is child_tool
    assert plan.agents["Parent"].composition[0].mode == "agent_as_tool"
    assert plan.caveats == []


def test_openai_agent_factory_does_not_infer_undeclared_composition() -> None:
    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        agent_tool_registry={"ChildAgent": "agent-tool"},
        handoff_registry={"ChildAgent": "handoff"},
    )

    assert plan.agents["ParentAgent"].tools == []
    assert plan.agents["ParentAgent"].composition[0].sdk_object is None
    assert [caveat.kind for caveat in plan.caveats] == ["agent_dependency_unwired"]


def test_openai_agent_factory_reports_unsupported_isolated_subagent() -> None:
    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, composition=["isolated_subagent(ChildAgent)"]),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
    )

    assert plan.agents["ParentAgent"].composition[0].mode == "unsupported"
    assert [caveat.kind for caveat in plan.caveats] == ["unsupported_composition"]


def test_openai_output_type_registry_generates_pydantic_models() -> None:
    registry = build_openai_output_type_registry(_factory_artifacts())
    parent_model = registry["ParentResult"]
    parsed = parent_model.model_validate(
        {
            "ok": True,
            "status": "ready",
            "score": 0.5,
            "tags": ["a"],
            "child": {"message": "hello"},
            "note": None,
        }
    )

    assert parsed.ok is True
    assert parsed.status == "ready"
    assert parent_model.model_json_schema()["additionalProperties"] is False


def test_openai_output_type_registry_uses_imported_pydantic_models() -> None:
    artifacts = compile_project(PYDANTIC_FIXTURE, allow_python_imports=True)

    registry = build_openai_output_type_registry(artifacts)
    parsed = registry["ResearchSummary"].model_validate(
        {
            "title": "Migration plan",
            "source_count": 1,
            "plan": {
                "topic": "Pydantic interop",
                "priority": "high",
                "source": {"url": "https://example.test", "confidence": 0.8},
            },
        }
    )

    assert registry["ResearchSummary"] is ResearchSummaryModel
    assert parsed.plan.priority == "high"
    assert parsed.plan.source is not None
    assert parsed.plan.source.confidence == 0.8


def test_openai_factory_can_generate_output_types_for_real_sdk_agent() -> None:
    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, include_agent_dependency=False),
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        generate_output_types=True,
    )

    assert isinstance(result.agents["ParentAgent"], Agent)
    assert result.agents["ParentAgent"].output_type.__name__ == "ParentResult"


def test_openai_output_type_registry_rejects_unsupported_schema() -> None:
    artifacts = _factory_artifacts()
    artifacts["schemas"]["BadResult"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "BadResult",
        "type": "object",
        "properties": {"bad": {"type": "object", "properties": {}}},
        "additionalProperties": False,
    }

    with pytest.raises(OpenAIAgentFactoryError, match="unsupported schema"):
        build_openai_output_type_registry(artifacts)


@pytest.mark.asyncio
async def test_openai_real_sdk_executes_wrapped_host_tool_offline() -> None:
    model = _ScriptedModel(
        [
            [_function_call_output(openai_tool_name("tools.lookup"), {"query": "status"})],
            [_message_output("done", item_id="message-2")],
        ]
    )
    calls: list[str] = []

    def lookup(query: str) -> str:
        """Look up a query."""

        calls.append(query)
        return f"found {query}"

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_agent_dependency=False),
        output_type_registry={"ParentResult": None, "ChildResult": None},
        model_registry={"ParentAgent": model},
        default_model=_ScriptedModel([]),
        tool_registry={"tools.lookup": lookup},
    )

    run_result = await run_openai_agent(result.agents["ParentAgent"], "hello")

    assert calls == ["status"]
    assert run_result.final_output == "done"
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_openai_real_sdk_executes_registered_agent_as_tool_offline() -> None:
    child_model = _ScriptedModel([[_message_output("child answer")]])
    child_agent = Agent(name="ChildRuntimeAgent", instructions="child", model=child_model)
    child_tool = child_agent.as_tool(tool_name="child_agent", tool_description="Run the child agent")
    parent_model = _ScriptedModel(
        [
            [_function_call_output("child_agent", {"input": "question"})],
            [_message_output("parent done", item_id="message-2")],
        ]
    )
    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, composition=["agent_as_tool(ChildAgent)"]),
        output_type_registry={"ParentResult": None, "ChildResult": None},
        model_registry={"ParentAgent": parent_model},
        default_model=_ScriptedModel([]),
        agent_tool_registry={"ChildAgent": child_tool},
    )

    run_result = await run_openai_agent(result.agents["ParentAgent"], "hello")

    assert run_result.final_output == "parent done"
    assert len(parent_model.calls) == 2
    assert len(child_model.calls) == 1
    assert child_model.calls[0].input == [{"content": "question", "role": "user"}]


def test_openai_agent_factory_constructs_real_sdk_handoff() -> None:
    model = _ScriptedModel([])
    child_agent = Agent(name="ChildRuntimeAgent", instructions="child", model=model)
    sdk_handoff = handoff(child_agent)

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, composition=["handoff(ChildAgent)"]),
        output_type_registry={"ParentResult": None, "ChildResult": None},
        model_registry={"ParentAgent": model},
        default_model=model,
        handoff_registry={"ChildAgent": sdk_handoff},
    )

    assert result.agents["ParentAgent"].handoffs == [sdk_handoff]


@pytest.mark.asyncio
async def test_openai_real_sdk_parses_structured_output_offline() -> None:
    model = _ScriptedModel([[_message_output(_parent_output_json())]])
    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, include_agent_dependency=False),
        model_registry={"ParentAgent": model},
        default_model=_ScriptedModel([]),
        generate_output_types=True,
    )

    run_result = await run_openai_agent(result.agents["ParentAgent"], "hello")

    assert run_result.final_output.model_dump()["ok"] is True
    assert isinstance(run_result.raw_result, RunResult)


@pytest.mark.asyncio
async def test_openai_run_with_contract_renders_context_resolves_approvals_and_assertions() -> None:
    model = _ScriptedModel(
        [
            [_function_call_output(openai_tool_name("tools.lookup"), {"id": "123"})],
            [_message_output(_parent_output_json(), item_id="message-2")],
        ]
    )
    executed: list[str] = []

    def lookup(id: str) -> str:
        """Look up an identifier."""

        executed.append(id)
        return f"found {id}"

    artifacts = _factory_artifacts(
        include_agent_dependency=False,
        tool_permission="requires_approval",
        parent_assertions=["expect(output.ok == true)"],
    )
    agent = build_openai_agents_from_contracts(
        artifacts,
        model_registry={"ParentAgent": model},
        default_model=_ScriptedModel([]),
        tool_registry={"tools.lookup": lookup},
        generate_output_types=True,
    ).agents["ParentAgent"]
    runtime_context = RuntimeContext(
        values={
            "VisibleContext": ContextValue(
                "VisibleContext",
                {"value": "public"},
                "public context",
                "test",
            ),
            "SensitiveContext": ContextValue(
                "SensitiveContext",
                {"value": "secret"},
                "secret context",
                "test",
                sensitive=True,
            ),
        },
        hidden={"HiddenState": {"value": "hidden"}},
    )

    result = await run_openai_agent_with_contract(
        agent,
        "hello",
        contract=artifacts,
        agent_name="ParentAgent",
        runtime_context=runtime_context,
        approval_callback=lambda request: request.tool == "tools.lookup",
    )

    assert result.passed
    assert result.approvals == [OpenAIApprovalRequest("tools.lookup", True, {"id": "123"})]
    assert executed == ["123"]
    first_input = str(model.calls[0].input)
    assert "public context" in first_input
    assert "secret context" not in first_input
    assert "hidden" not in first_input
    assert result.trace.count("approval.requested", "tools.lookup") == 1
    assert result.trace.count("approval.completed", "tools.lookup") == 1
    assert result.trace.count("tool.started", "tools.lookup") == 1
    assert result.trace.count("tool.completed", "tools.lookup") == 1
    approval_events = [event for event in result.trace.events if event.type.startswith("approval.")]
    assert {event.data["agent"] for event in approval_events} == {"ParentAgent"}
    assert run_monitors(
        [
            MonitorRule(
                "other_agent_approval",
                "OtherAgent",
                "high",
                "",
                "trace.approval_granted(tools.lookup)",
            )
        ],
        result.trace,
        run_id=result.trace.run_id,
    )
    assert result.trace.count("assertion.evaluated", "expect(output.ok == true)") == 1
    assert result.adapter_result.final_output["ok"] is True
    assert result.adapter_result.last_agent == "ParentAgent"
    assert isinstance(result.adapter_result.raw_result, RunResult)


@pytest.mark.asyncio
async def test_openai_run_with_contract_rejects_and_resumes_real_sdk_state() -> None:
    model = _ScriptedModel(
        [
            [_function_call_output(openai_tool_name("tools.lookup"), {"id": "denied"})],
            [_message_output(_parent_output_json(), item_id="message-2")],
        ]
    )
    executed: list[str] = []

    def lookup(id: str) -> str:
        """Look up an identifier."""

        executed.append(id)
        return id

    artifacts = _factory_artifacts(
        include_agent_dependency=False,
        tool_permission="requires_approval",
    )
    agent = build_openai_agents_from_contracts(
        artifacts,
        model_registry={"ParentAgent": model},
        default_model=_ScriptedModel([]),
        tool_registry={"tools.lookup": lookup},
        generate_output_types=True,
    ).agents["ParentAgent"]

    result = await run_openai_agent_with_contract(
        agent,
        "hello",
        contract=artifacts,
        agent_name="ParentAgent",
        approval_callback=lambda _request: False,
    )

    assert executed == []
    assert result.approvals == [OpenAIApprovalRequest("tools.lookup", False, {"id": "denied"})]
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_openai_run_with_contract_requires_approval_callback() -> None:
    model = _ScriptedModel(
        [[_function_call_output(openai_tool_name("tools.lookup"), {"id": "123"})]]
    )

    def lookup(id: str) -> str:
        """Look up an identifier."""

        return id

    artifacts = _factory_artifacts(
        include_agent_dependency=False,
        tool_permission="requires_approval",
    )
    agent = build_openai_agents_from_contracts(
        artifacts,
        model_registry={"ParentAgent": model},
        default_model=_ScriptedModel([]),
        tool_registry={"tools.lookup": lookup},
        generate_output_types=True,
    ).agents["ParentAgent"]

    with pytest.raises(OpenAIAgentFactoryError, match="approval_callback"):
        await run_openai_agent_with_contract(
            agent,
            "hello",
            contract=artifacts,
            agent_name="ParentAgent",
        )


def _parent_output_json() -> str:
    return json.dumps(
        {
            "ok": True,
            "status": "ready",
            "score": 1.0,
            "tags": ["offline-sdk"],
            "child": {"message": "done"},
            "note": None,
        }
    )


def _factory_artifacts(
    include_tool: bool = True,
    include_hosted_tool: bool = False,
    include_agent_dependency: bool = True,
    tool_permission: str = "available",
    hosted_tool_permission: str = "available",
    composition: list[str] | None = None,
    parent_assertions: list[str] | None = None,
    parent_inputs: list[dict[str, object]] | None = None,
    parent_datasources: list[dict[str, object]] | None = None,
    guard_plan: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    parent_tools = (
        [{"name": "tools.lookup", "module": "tools", "permission": tool_permission}] if include_tool else []
    )
    parent_hosted_tools = (
        [
            {
                "name": "openai.web_search",
                "provider": "openai",
                "tool": "web_search",
                "config": {"context_size": "medium"},
                "permission": hosted_tool_permission,
            }
        ]
        if include_hosted_tool
        else []
    )
    parent_agents = (
        [{"name": "ChildAgent", "module": "./child", "permission": "available"}] if include_agent_dependency else []
    )
    return {
        "schemas": {
            "ChildResult": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "ChildResult",
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            "ParentResult": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "ParentResult",
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "status": {"type": "string", "enum": ["ready", "blocked"]},
                    "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "child": {"$ref": "#/$defs/ChildResult"},
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["ok", "status", "score", "tags", "child"],
                "additionalProperties": False,
            },
        },
        "type_bindings": [
            {
                "type": "ParentResult",
                "source": "native",
                "python_ref": None,
                "schema_ref": "schemas/ParentResult.json",
                "schema_hash": "parent",
            },
            {
                "type": "ChildResult",
                "source": "native",
                "python_ref": None,
                "schema_ref": "schemas/ChildResult.json",
                "schema_hash": "child",
            },
        ],
        "manifests": {
            "ParentAgent": {
                "agent": "ParentAgent",
                "source_path": "agents/parent.contract",
                "description": "",
                "goal": "",
                "inputs": parent_inputs or [],
                "output": {
                    "type": "ParentResult",
                    "schema_ref": "schemas/ParentResult.json",
                    "python_ref": None,
                },
                "tools": parent_tools,
                "hosted_tools": parent_hosted_tools,
                "agents": parent_agents,
                "datasources": parent_datasources or [],
                "policy": [],
                "success": [],
                "routes": [],
                "composition": composition or [],
                "guards": [],
                "assertions": parent_assertions or [],
            },
            "ChildAgent": {
                "agent": "ChildAgent",
                "source_path": "agents/child.contract",
                "description": "",
                "goal": "",
                "inputs": [],
                "output": {
                    "type": "ChildResult",
                    "schema_ref": "schemas/ChildResult.json",
                    "python_ref": None,
                },
                "tools": [],
                "hosted_tools": [],
                "agents": [],
                "datasources": [],
                "policy": [],
                "success": [],
                "routes": [],
                "composition": [],
                "guards": [],
                "assertions": [],
            },
        },
        "instructions": {"ParentAgent": "parent instructions", "ChildAgent": "child instructions"},
        "evals": [],
        "monitors": [],
        "guard_plan": guard_plan or [],
        "adapter_capability_matrix": {},
        "docs": {},
    }


def _guard_item(
    kind: str,
    enforcement: str,
    expression: str,
    *,
    status: str = "supported",
    target: str | None = None,
    output_type: str | None = None,
    declared_permission: str | None = None,
    message: str | None = None,
) -> dict[str, object]:
    return {
        "agent": "ParentAgent",
        "expression": expression,
        "kind": kind,
        "status": status,
        "enforcement": enforcement,
        "target": target,
        "output_type": output_type,
        "declared_permission": declared_permission,
        "message": message,
    }
