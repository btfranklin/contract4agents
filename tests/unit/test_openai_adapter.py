from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from contract4agents.adapters.openai import OpenAISemanticJudge, build_openai_agent, run_openai_agent


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
