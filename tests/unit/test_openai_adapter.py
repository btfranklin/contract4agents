from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from contract4agents.adapters.openai import (
    OpenAIAgentFactoryError,
    OpenAISemanticJudge,
    build_openai_agent,
    build_openai_agents_from_contracts,
    run_openai_agent,
)


@pytest.mark.asyncio
async def test_openai_adapter_with_mocked_agents_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("agents")

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeRunner:
        @staticmethod
        async def run(agent: object, user_input: str, **_kwargs: object) -> object:
            return SimpleNamespace(final_output=f"ran {user_input}", last_agent=SimpleNamespace(name="Fake"))

    module.Agent = FakeAgent  # type: ignore[attr-defined]
    module.Runner = FakeRunner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents", module)

    agent = build_openai_agent({"agent": "A", "model": "test"}, "instructions")
    result = await run_openai_agent(agent, "hello")

    assert agent.kwargs["name"] == "A"
    assert result.final_output == "ran hello"
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


def test_openai_agent_factory_builds_agents_from_registries(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
    tool = object()
    child_tool = object()
    handoff = object()

    result = build_openai_agents_from_contracts(
        _factory_artifacts(),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model"},
        tool_registry={"tools.lookup": tool},
        agent_tool_registry={"ChildAgent": child_tool},
        handoff_registry={"ChildAgent": handoff},
        instruction_overrides={"ParentAgent": "override"},
        default_model="default-model",
    )

    parent = result.agents["ParentAgent"]
    child = result.agents["ChildAgent"]
    assert result.caveats == []
    assert parent.kwargs["model"] == "parent-model"
    assert parent.kwargs["instructions"] == "override"
    assert parent.kwargs["tools"] == [tool, child_tool]
    assert parent.kwargs["handoffs"] == [handoff]
    assert parent.kwargs["output_type"] is dict
    assert child.kwargs["model"] == "default-model"
    assert child.kwargs["output_type"] is list


def test_openai_agent_factory_reports_unwired_agent_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)

    result = build_openai_agents_from_contracts(
        _factory_artifacts(include_tool=False),
        output_type_registry={"ParentResult": dict, "ChildResult": list},
        model_registry={"ParentAgent": "parent-model", "ChildAgent": "child-model"},
    )

    assert [caveat.kind for caveat in result.caveats] == ["agent_dependency_unwired"]
    assert "ChildAgent" in result.caveats[0].message


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
    assert result.caveats == []


def test_openai_agent_factory_reports_approval_required_tool_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agents_module(monkeypatch)
    tool = object()
    child_tool = object()

    result = build_openai_agents_from_contracts(
        _factory_artifacts(
            tool_permission="requires_approval",
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
        agent_tool_registry={"ChildAgent": child_tool},
    )

    assert result.agents["ParentAgent"].kwargs["tools"] == [tool, child_tool]
    assert [caveat.kind for caveat in result.caveats] == ["approval_required_tool"]


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


def _install_fake_agents_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("agents")

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    module.Agent = FakeAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents", module)


def _factory_artifacts(
    include_tool: bool = True,
    include_agent_dependency: bool = True,
    tool_permission: str = "available",
    guard_plan: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    parent_tools = (
        [{"name": "tools.lookup", "module": "tools", "permission": tool_permission}] if include_tool else []
    )
    parent_agents = (
        [{"name": "ChildAgent", "module": "./child", "permission": "available"}] if include_agent_dependency else []
    )
    return {
        "schemas": {},
        "manifests": {
            "ParentAgent": {
                "agent": "ParentAgent",
                "description": "",
                "goal": "",
                "inputs": [],
                "output": {"type": "ParentResult", "schema_ref": "schemas/ParentResult.json"},
                "tools": parent_tools,
                "agents": parent_agents,
                "datasources": [],
                "policy": [],
                "success": [],
                "routes": [],
                "composition": [],
                "guards": [],
                "assertions": [],
            },
            "ChildAgent": {
                "agent": "ChildAgent",
                "description": "",
                "goal": "",
                "inputs": [],
                "output": {"type": "ChildResult", "schema_ref": "schemas/ChildResult.json"},
                "tools": [],
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
