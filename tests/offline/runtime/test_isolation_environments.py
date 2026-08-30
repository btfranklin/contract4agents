from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from contract4agents import materialize
from contract4agents.ir import (
    FrozenMap,
    semantic_id,
)
from contract4agents.materialization import (
    OpenAIMaterializationProvider,
)
from contract4agents.planning import PlanningError
from contract4agents.runtime import EnvironmentRunRequest, InProcessEnvironment
from tests.support.openai import FakeOpenAISDK, FakeTool, write_project


def test_supported_in_process_isolation_is_configured_and_evidenced(tmp_path: Path) -> None:
    write_project(tmp_path, isolation=True)

    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    edge = result.graph.composition_objects[semantic_id("edge", "ask_child")]
    assert isinstance(edge, FakeTool)
    assert isinstance(edge.environment, InProcessEnvironment)
    assert edge.dimensions == FrozenMap(
        {
            "context": "explicit_only",
            "capabilities": "declared_only",
            "state": "fresh",
            "return": "final_output_only",
        }
    )
    assert edge.declared_capabilities == ("tool:records.lookup",)
    evidence = result.graph.environment_evidence[0]
    assert evidence.isolation_id == semantic_id("isolation", "CleanContext")
    assert evidence.provider == InProcessEnvironment.provider_id
    assert evidence.mechanisms["context"] == "in_process.fresh_context"


def test_concrete_openai_materializer_constructs_isolated_delegate_without_running_it(
    tmp_path: Path,
) -> None:
    from agents import FunctionTool

    write_project(tmp_path, isolation=True)

    result = materialize(tmp_path, "openai", "test")

    parent = cast(Any, result.agents["Parent"])
    isolated_tool = next(item for item in parent.tools if item.name.endswith("ask_child"))
    assert isinstance(isolated_tool, FunctionTool)
    assert isolated_tool.params_json_schema["additionalProperties"] is False
    assert result.graph.environment_evidence[0].provider == InProcessEnvironment.provider_id


@pytest.mark.asyncio
async def test_in_process_environment_passes_only_explicit_fresh_declared_state() -> None:
    environment = InProcessEnvironment()
    observed: tuple[object, object | None, object | None, tuple[str, ...] | None] | None = None

    async def invoke(
        payload: object,
        context: object | None,
        state: object | None,
        capabilities: tuple[str, ...] | None,
    ) -> object:
        nonlocal observed
        observed = (payload, context, state, capabilities)
        return {"final": True}

    result = await environment.run(
        EnvironmentRunRequest(
            semantic_id("isolation", "CleanContext"),
            {"request": "only this"},
            FrozenMap(
                {
                    "context": "explicit_only",
                    "capabilities": "declared_only",
                    "state": "fresh",
                    "return": "final_output_only",
                }
            ),
            ("tool:records.lookup",),
            parent_context={"secret": True},
            parent_state={"conversation": True},
        ),
        invoke,
    )

    assert result == {"final": True}
    assert observed is not None
    assert observed[0] == {"request": "only this"}
    assert observed[1] is None
    assert observed[2] is not None and observed[2] != {"conversation": True}
    assert observed[3] == ("tool:records.lookup",)


def test_strong_isolation_dimension_fails_closed_before_graph_construction(tmp_path: Path) -> None:
    write_project(tmp_path, isolation=True, network="denied")

    with pytest.raises(PlanningError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        )

    assert any(
        issue.code == "PLN009"
        and issue.semantic_id == semantic_id("isolation", "CleanContext")
        and "network" in issue.message
        for issue in caught.value.issues
    )


def test_unsupported_operational_control_fails_before_graph_construction(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        operational_source="""\
operational_control latency for Parent:
    severity = medium
    window = 15m
    require = trace.duration < 10s

""",
    )

    with pytest.raises(PlanningError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        )

    issue = next(item for item in caught.value.issues if item.code == "PLN012")
    assert issue.semantic_id == semantic_id("operational", "Parent", "latency")


def test_strong_environment_provider_can_satisfy_filesystem_and_network_dimensions(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        isolation=True,
        network="denied",
        filesystem="none",
        strong_environment=True,
    )

    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    isolation = result.plan.isolation[semantic_id("isolation", "CleanContext")]
    assert isolation.dimensions["network"].outcome == "host_enforced"
    assert isolation.dimensions["filesystem"].mechanism == "test_sandbox.filesystem"
    assert result.graph.environment_evidence[0].provider == "app_impl:StrongEnvironment"
