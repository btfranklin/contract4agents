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


def _install_fake_agents_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("agents")

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    module.Agent = FakeAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents", module)


def _factory_artifacts(include_tool: bool = True) -> dict[str, object]:
    parent_tools = [{"name": "tools.lookup", "module": "tools", "permission": "available"}] if include_tool else []
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
                "agents": [{"name": "ChildAgent", "module": "./child", "permission": "available"}],
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
        "adapter_capability_matrix": {},
        "docs": {},
    }
