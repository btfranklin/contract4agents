from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

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


@pytest.mark.asyncio
async def test_openai_adapter_with_mocked_agents_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _install_fake_agents_module(monkeypatch)

    agent = build_openai_agent({"agent": "A", "model": "test"}, "instructions")
    result = await run_openai_agent(agent, "hello")

    assert agent.kwargs["name"] == "A"
    assert module.runner_inputs[-1] == "hello"
    assert result.final_output == {"ok": True}
    assert result.last_agent == "Fake"


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


def test_openai_adapter_plan_includes_contract_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
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


def test_openai_agent_factory_builds_agents_from_typed_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
    tool = object()
    hosted_tool = object()
    child_tool = object()

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_hosted_tool=True, composition=["agent_as_tool(ChildAgent)"]),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model"},
        tool_registry={"tools.lookup": tool},
        hosted_tool_registry={"openai.web_search": hosted_tool},
        agent_tool_registry={"ChildAgent": child_tool},
        instruction_overrides={"ParentAgent": "override"},
        default_model="default-model",
    )

    parent = result.agents["ParentAgent"]
    child = result.agents["ChildAgent"]
    assert result.plan.agents["ParentAgent"].instructions == "override"
    assert result.caveats == []
    assert parent.kwargs["model"] == "parent-model"
    assert parent.kwargs["instructions"] == "override"
    assert parent.kwargs["tools"] == [tool, hosted_tool, child_tool]
    assert parent.kwargs["handoffs"] == []
    assert parent.kwargs["output_type"] is dict
    assert child.kwargs["model"] == "default-model"
    assert child.kwargs["output_type"] is list


def test_openai_agent_factory_builds_from_existing_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    plan = plan_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, include_agent_dependency=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
    )
    result = build_openai_agents_from_plan(plan)

    assert result.plan is plan
    assert result.agents["ParentAgent"].kwargs["model"] == "parent-model"


def test_openai_tool_name_mapping_is_injective_for_dots_and_underscores() -> None:
    first = openai_tool_name("a.b__c")
    second = openai_tool_name("a__b.c")

    assert first != second
    assert contract_tool_name(first) == "a.b__c"
    assert contract_tool_name(second) == "a__b.c"
    assert contract_tool_name("plain_tool") == "plain_tool"
    with pytest.raises(OpenAIAgentFactoryError, match="ambiguous legacy"):
        contract_tool_name("a__b")


def test_openai_agent_factory_reports_unwired_agent_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
    )

    assert [caveat.kind for caveat in result.caveats] == ["agent_dependency_unwired"]
    assert result.plan.agents["ParentAgent"].composition[0].mode == "unwired"


def test_openai_agent_factory_rejects_missing_output_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    with pytest.raises(OpenAIAgentFactoryError, match="No output type registered"):
        build_openai_agents_from_contracts(
            _factory_artifacts(),
            output_type_registry={"ChildResult": list},
            model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
            tool_registry={"tools.lookup": object()},
        )


def test_openai_agent_factory_rejects_missing_declared_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    with pytest.raises(OpenAIAgentFactoryError, match="No host tool registered"):
        build_openai_agents_from_contracts(
            _factory_artifacts(),
            output_type_registry={"ParentResult": dict, "ChildResult": list},
            model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        )


def test_openai_agent_factory_rejects_missing_hosted_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    with pytest.raises(OpenAIAgentFactoryError, match="No hosted tool registered"):
        build_openai_agents_from_contracts(
            _factory_artifacts(include_tool=False, include_hosted_tool=True, include_agent_dependency=False),
            output_type_registry={"ParentResult": dict, "ChildResult": list},
            model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        )


def test_openai_agent_factory_rejects_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    with pytest.raises(OpenAIAgentFactoryError, match="No model configured"):
        build_openai_agents_from_contracts(
            _factory_artifacts(include_tool=False),
            output_type_registry={"ParentResult": dict, "ChildResult": list},
            model_registry={"ParentAgent": "parent-model"},
        )


def test_openai_agent_factory_omits_denied_guard_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
    child_tool = object()

    result = build_openai_agents_from_contracts(
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

    assert result.agents["ParentAgent"].kwargs["tools"] == [child_tool]
    assert [caveat.kind for caveat in result.caveats] == ["denied_tool_omitted"]


def test_openai_agent_factory_builds_enabled_openai_web_search(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, include_hosted_tool=True, include_agent_dependency=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        hosted_tool_registry={"openai.web_search": True},
    )

    hosted_tool = result.agents["ParentAgent"].kwargs["tools"][0]
    assert hosted_tool.kwargs == {"search_context_size": "medium"}
    assert result.plan.agents["ParentAgent"].hosted_tools[0].config == {"context_size": "medium"}


def test_openai_agent_factory_uses_each_manifest_hosted_tool_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_agents_module(monkeypatch)

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
        agent_name: agent.kwargs["tools"][0].kwargs
        for agent_name, agent in result.agents.items()
        if agent.kwargs["tools"]
    } == {
        "ResearchManagerAgent": {"search_context_size": "medium"},
        "SectionResearchAgent": {"search_context_size": "high"},
        "VerifierAgent": {"search_context_size": "medium"},
    }
    assert result.agents["SynthesisAgent"].kwargs["tools"] == []


def test_openai_agent_factory_omits_denied_hosted_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    result = build_openai_agents_from_contracts(
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

    assert result.agents["ParentAgent"].kwargs["tools"] == []
    assert [caveat.kind for caveat in result.caveats] == ["denied_hosted_tool_omitted"]


def test_openai_agent_factory_wraps_raw_callable_with_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    def lookup() -> dict[str, bool]:
        return {"ok": True}

    result = build_openai_agents_from_contracts(
        _factory_artifacts(tool_permission="requires_approval", include_agent_dependency=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        tool_registry={"tools.lookup": OpenAIToolRegistration(lookup, raw_callable=True, description="Lookup")},
    )

    tool = result.agents["ParentAgent"].kwargs["tools"][0]
    assert tool.name == openai_tool_name("tools.lookup")
    assert tool.needs_approval is True
    assert tool.description == "Lookup"
    assert result.plan.agents["ParentAgent"].tools[0].wrapped


def test_openai_agent_factory_reports_unverified_approval_for_prebuilt_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_agents_module(monkeypatch)
    tool = SimpleNamespace(name=openai_tool_name("tools.lookup"))

    result = build_openai_agents_from_contracts(
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

    assert result.agents["ParentAgent"].kwargs["tools"] == [tool]
    assert [caveat.kind for caveat in result.caveats] == ["approval_enforcement_unverified"]


def test_openai_agent_factory_reports_unsupported_guard_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    result = build_openai_agents_from_contracts(
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

    assert [caveat.kind for caveat in result.caveats] == ["unsupported_guard"]


def test_openai_agent_factory_maps_output_guard_when_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
    child_tool = object()

    result = build_openai_agents_from_contracts(
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

    assert result.agents["ParentAgent"].kwargs["output_type"] is dict
    assert result.caveats == []


def test_openai_agent_factory_maps_handoff_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
    handoff = object()

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, composition=["handoff(ChildAgent)"]),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        handoff_registry={"ChildAgent": handoff},
    )

    assert result.agents["ParentAgent"].kwargs["tools"] == []
    assert result.agents["ParentAgent"].kwargs["handoffs"] == [handoff]
    assert result.plan.agents["ParentAgent"].composition[0].mode == "handoff"


def test_valid_composition_source_compiles_and_maps_in_openai_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_agents_module(monkeypatch)
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

    result = build_openai_agents_from_contracts(
        compile_project(tmp_path),
        output_type_registry={"Result": dict},
        model_registry={"Parent": "parent-model", "Child": "child-model"},
        agent_tool_registry={"Child": child_tool},
    )

    assert result.agents["Parent"].kwargs["tools"] == [child_tool]
    assert result.agents["Parent"].kwargs["handoffs"] == []
    assert result.plan.agents["Parent"].composition[0].mode == "agent_as_tool"
    assert result.caveats == []


def test_openai_agent_factory_does_not_infer_undeclared_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        agent_tool_registry={"ChildAgent": "agent-tool"},
        handoff_registry={"ChildAgent": "handoff"},
    )

    assert result.agents["ParentAgent"].kwargs["tools"] == []
    assert result.agents["ParentAgent"].kwargs["handoffs"] == []
    assert [caveat.kind for caveat in result.caveats] == ["agent_dependency_unwired"]


def test_openai_agent_factory_reports_unsupported_isolated_subagent(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, composition=["isolated_subagent(ChildAgent)"]),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
    )

    assert result.plan.agents["ParentAgent"].composition[0].mode == "unsupported"
    assert [caveat.kind for caveat in result.caveats] == ["unsupported_composition"]


def test_openai_output_type_registry_generates_pydantic_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

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


def test_openai_output_type_registry_uses_imported_pydantic_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
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


def test_openai_factory_can_generate_output_types(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False, include_agent_dependency=False),
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
        generate_output_types=True,
    )

    assert result.agents["ParentAgent"].kwargs["output_type"].__name__ == "ParentResult"


def test_openai_output_type_registry_rejects_unsupported_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
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
async def test_openai_run_with_contract_renders_context_resolves_approvals_and_assertions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _install_fake_agents_module(monkeypatch, interrupt_once=True)
    agent = build_openai_agent({"agent": "ParentAgent", "model": "test"}, "instructions")
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
        contract=_factory_artifacts(parent_assertions=["expect(output.ok == true)"]),
        agent_name="ParentAgent",
        runtime_context=runtime_context,
        approval_callback=lambda request: request.tool == "tools.lookup",
    )

    assert result.passed
    assert result.approvals == [OpenAIApprovalRequest("tools.lookup", True, {"id": "123"})]
    first_input = module.runner_inputs[0]
    assert "public context" in first_input
    assert "secret context" not in first_input
    assert "hidden" not in first_input
    assert result.trace.count("approval.requested", "tools.lookup") == 1
    assert result.trace.count("approval.completed", "tools.lookup") == 1
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


@pytest.mark.asyncio
async def test_openai_run_with_contract_requires_approval_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch, interrupt_once=True)
    agent = build_openai_agent({"agent": "ParentAgent", "model": "test"}, "instructions")

    with pytest.raises(OpenAIAgentFactoryError, match="approval_callback"):
        await run_openai_agent_with_contract(
            agent,
            "hello",
            contract=_factory_artifacts(parent_assertions=["expect(output.ok == true)"]),
            agent_name="ParentAgent",
        )


def _install_fake_agents_module(monkeypatch: pytest.MonkeyPatch, interrupt_once: bool = False) -> ModuleType:
    module = ModuleType("agents")
    module.runner_inputs = []  # type: ignore[attr-defined]

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.name = kwargs.get("name")

    class FakeFunctionTool:
        def __init__(self, func: object, name: str, needs_approval: bool, description: str | None) -> None:
            self.func = func
            self.name = name
            self.needs_approval = needs_approval
            self.description = description

    class FakeWebSearchTool:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeInterruption:
        tool_name = openai_tool_name("tools.lookup")
        arguments = {"id": "123"}
        agent_name = "ParentAgent"

    class FakeState:
        def __init__(self) -> None:
            self.approved: list[object] = []
            self.rejected: list[object] = []

        def approve(self, interruption: object) -> None:
            self.approved.append(interruption)

        def reject(self, interruption: object, rejection_message: str) -> None:
            self.rejected.append((interruption, rejection_message))

    class FakeResult:
        def __init__(self, interruptions: list[object] | None = None) -> None:
            self.interruptions = interruptions or []
            self.final_output = {"ok": True}
            self.last_agent = SimpleNamespace(name="Fake")

        def to_state(self) -> FakeState:
            return FakeState()

    class FakeRunner:
        calls = 0

        @staticmethod
        async def run(agent: object, user_input: object, **_kwargs: object) -> object:
            FakeRunner.calls += 1
            module.runner_inputs.append(user_input)  # type: ignore[attr-defined]
            if interrupt_once and FakeRunner.calls == 1:
                return FakeResult([FakeInterruption()])
            return FakeResult()

    def function_tool(**kwargs: object) -> object:
        def decorate(func: object) -> FakeFunctionTool:
            return FakeFunctionTool(
                func,
                str(kwargs["name_override"]),
                bool(kwargs.get("needs_approval", False)),
                kwargs.get("description_override") if isinstance(kwargs.get("description_override"), str) else None,
            )

        return decorate

    module.Agent = FakeAgent  # type: ignore[attr-defined]
    module.Runner = FakeRunner  # type: ignore[attr-defined]
    module.WebSearchTool = FakeWebSearchTool  # type: ignore[attr-defined]
    module.function_tool = function_tool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents", module)
    return module


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
