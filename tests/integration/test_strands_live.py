from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest

from contract4agents import materialize
from contract4agents.ir import semantic_id
from contract4agents.materialization import StrandsMaterializationProvider
from contract4agents.tracing import (
    StrandsNormalizedTraceRouter,
    TraceAttempt,
    validate_trace_closure,
    validate_trace_conformance,
)
from examples.incident_command_imports.seed import seed_incident_data

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "examples" / "incident-command"
PROMPT = ROOT / "tests" / "fixtures" / "prompts" / "openai-live-incident.md"


@pytest.mark.integration
@pytest.mark.live
@pytest.mark.asyncio
async def test_contract_first_incident_graph_runs_through_strands_bedrock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("CONTRACT4AGENTS_RUN_STRANDS_LIVE") != "1":
        pytest.skip(
            "set CONTRACT4AGENTS_RUN_STRANDS_LIVE=1 to run the live Strands smoke test"
        )

    database = seed_incident_data(tmp_path / "incident.sqlite")
    monkeypatch.setenv("CONTRACT4AGENTS_INCIDENT_DB", str(database))
    provider = StrandsMaterializationProvider()
    system = materialize(
        PROJECT,
        target="strands",
        profile="production",
        provider=provider,
    )
    run_id = "strands-live-incident"
    invocation = {
        "request": {
            "service": "checkout-api",
            "start": "2026-05-01T10:00:00Z",
            "end": "2026-05-01T11:00:00Z",
            "symptom": "Checkout latency and timeout spike",
        },
        "service": {
            "id": "checkout-api",
            "name": "Checkout API",
            "owner": "payments",
        },
        "window": {
            "start": "2026-05-01T10:00:00Z",
            "end": "2026-05-01T11:00:00Z",
        },
    }
    context = await system.context.resolve_agent(
        "IncidentCommander",
        invocation,
        run_id=run_id,
    )
    rendered_context = "\n\n".join(
        f"### {name}\n\n{value.rendered}"
        for name, value in context.items()
    )
    prompt = PROMPT.read_text(encoding="utf-8").replace(
        "{{CONTEXT}}",
        rendered_context,
    )
    commander = system.graph.agent("IncidentCommander")
    router = StrandsNormalizedTraceRouter()
    router.attach(system.graph)
    session = router.open_session(
        system.context.ir,
        system.plan,
        run_id=run_id,
    )
    attempt = TraceAttempt(
        "incident-command:1",
        "incident-command-attempt-1",
        1,
    )
    approval_tools = {
        provider.sdk.describe_tool(native_tool).native_name: native_tool
        for grant_id, native_tool in system.graph.grant_objects.items()
        if (
            grant := system.context.ir.grants[grant_id]
        ).agent_id == semantic_id("agent", "IncidentCommander")
        and grant.authorization == "approval_required"
    }

    with session:
        with session.bind_attempt(attempt, agent="IncidentCommander"):
            result = await cast(Any, commander).invoke_async(
                prompt,
                limits={"turns": 12},
            )
            approval_rounds = 0
            while result.stop_reason == "interrupt":
                approval_rounds += 1
                assert approval_rounds <= len(approval_tools)
                selected_tools = _selected_tool_names(result.message)
                assert selected_tools
                for tool_name in selected_tools:
                    native_tool = approval_tools[tool_name]
                    session.record_approval_requested(native_tool=native_tool)
                    session.record_approval(
                        native_tool=native_tool,
                        approved=False,
                    )
                result = await cast(Any, commander).invoke_async(
                    [
                        {
                            "interruptResponse": {
                                "interruptId": interrupt.id,
                                "response": "no",
                            }
                        }
                        for interrupt in result.interrupts
                    ],
                    limits={"turns": 12},
                )

    output = provider.validate_result(commander, result)
    assert output.summary
    assert output.evidence
    snapshot = session.closed_snapshot
    validate_trace_conformance(
        system.context.ir,
        system.plan,
        snapshot.trace,
    )
    validate_trace_closure(snapshot.trace, snapshot.closure)
    assert snapshot.closure.status == "complete"
    completed_agents = {
        event.semantic.agent_id
        for event in snapshot.trace.events
        if event.event_type == "agent.completed"
    }
    assert {
        semantic_id("agent", "IncidentCommander"),
        semantic_id("agent", "LogInvestigator"),
        semantic_id("agent", "DeployAnalyst"),
        semantic_id("agent", "MetricsAnalyst"),
    } <= completed_agents
    assert any(
        event.event_type == "composition.completed"
        for event in snapshot.trace.events
    )


def _selected_tool_names(message: object) -> tuple[str, ...]:
    if not isinstance(message, dict):
        return ()
    content = message.get("content")
    if not isinstance(content, list):
        return ()
    names: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        tool_use = block.get("toolUse")
        if not isinstance(tool_use, dict):
            continue
        name = tool_use.get("name")
        if isinstance(name, str):
            names.append(name)
    return tuple(names)
