from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from contract4agents import materialize
from contract4agents.ir import semantic_id
from contract4agents.tracing import (
    OpenAINormalizedTraceProcessor,
    RecordingNormalizedTraceSink,
    validate_trace_conformance,
)
from examples.incident_command_imports.seed import seed_incident_data

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "examples" / "incident-command"
PROMPT = ROOT / "tests" / "fixtures" / "prompts" / "openai-live-incident.md"
WEB_SEARCH_PROJECT = ROOT / "examples" / "market-research-brief"
WEB_SEARCH_PROMPT = ROOT / "tests" / "fixtures" / "prompts" / "openai-live-web-search.md"


@pytest.mark.integration
@pytest.mark.live
@pytest.mark.asyncio
async def test_contract_first_incident_graph_runs_through_openai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("CONTRACT4AGENTS_RUN_OPENAI_LIVE") != "1":
        pytest.skip("set CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 to run the live OpenAI smoke test")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not configured")

    from agents import RunConfig, Runner, set_trace_processors

    database = seed_incident_data(tmp_path / "incident.sqlite")
    monkeypatch.setenv("CONTRACT4AGENTS_INCIDENT_DB", str(database))
    runtime_events = RecordingNormalizedTraceSink()
    system = materialize(
        PROJECT,
        target="openai",
        profile="production",
        runtime_trace_sink=runtime_events,
    )
    run_id = "openai-live-incident"
    invocation = {
        "request": {
            "service": "checkout-api",
            "start": "2026-05-01T10:00:00Z",
            "end": "2026-05-01T11:00:00Z",
            "symptom": "Checkout latency and timeout spike",
        },
        "service": {"id": "checkout-api", "name": "Checkout API", "owner": "payments"},
        "window": {"start": "2026-05-01T10:00:00Z", "end": "2026-05-01T11:00:00Z"},
    }
    context = await system.context.resolve_agent("IncidentCommander", invocation, run_id=run_id)
    rendered_context = "\n\n".join(
        f"### {name}\n\n{value.rendered}" for name, value in context.items()
    )

    processor = OpenAINormalizedTraceProcessor(
        system.context.ir,
        system.plan,
        run_id=run_id,
    )
    set_trace_processors([processor])
    prompt = PROMPT.read_text(encoding="utf-8").replace("{{CONTEXT}}", rendered_context)
    result = await Runner.run(
        system.agents["IncidentCommander"],
        prompt,
        max_turns=12,
        run_config=RunConfig(
            workflow_name="Contract4Agents live Incident Command",
            trace_include_sensitive_data=False,
        ),
    )

    assert result.final_output is not None
    assert result.final_output.summary
    assert result.final_output.evidence
    trace = processor.normalized_trace()
    event_types = {event.event_type for event in trace.events}
    completed_agents = {
        event.semantic.agent_id
        for event in trace.events
        if event.event_type == "agent.completed"
    }
    assert {"agent.started", "agent.completed", "output.accepted"} <= event_types
    assert {
        semantic_id("agent", "IncidentCommander"),
        semantic_id("agent", "LogInvestigator"),
        semantic_id("agent", "DeployAnalyst"),
        semantic_id("agent", "MetricsAnalyst"),
    } <= completed_agents
    assert "composition.completed" in event_types
    assert {event.event_type for event in runtime_events.events} == {
        "context.resolved",
        "datasource.resolved",
    }


@pytest.mark.integration
@pytest.mark.live
@pytest.mark.asyncio
async def test_openai_hosted_web_search_normalizes_to_exact_grant() -> None:
    if os.environ.get("CONTRACT4AGENTS_RUN_OPENAI_LIVE") != "1":
        pytest.skip("set CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 to run the live OpenAI smoke test")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not configured")

    from agents import RunConfig, Runner, set_trace_processors

    system = materialize(WEB_SEARCH_PROJECT, target="openai", profile="production")
    run_id = "openai-live-web-search"
    processor = OpenAINormalizedTraceProcessor(
        system.context.ir,
        system.plan,
        run_id=run_id,
    )
    set_trace_processors([processor])
    # The SDK only attaches the provider response (including its model field)
    # when data capture is enabled. Our sole processor deliberately retains
    # correlation and model metadata while excluding response input/output.
    result = await Runner.run(
        system.agents["CurrentTruthScout"],
        WEB_SEARCH_PROMPT.read_text(encoding="utf-8"),
        max_turns=4,
        run_config=RunConfig(
            workflow_name="Contract4Agents live hosted web search",
            trace_include_sensitive_data=True,
        ),
    )
    processor.normalize_response_events(
        result.raw_responses,
        agent="CurrentTruthScout",
    )
    trace = processor.normalized_trace()
    hosted_events = [
        event
        for event in trace.events
        if event.event_type == "tool.completed"
        and event.semantic.capability_id == semantic_id("tool", "web.search")
    ]

    assert len(hosted_events) == 1
    assert hosted_events[0].semantic.grant_id == semantic_id(
        "grant", "CurrentTruthScout", "web.search"
    )
    assert hosted_events[0].provider.run_id
    assert hosted_events[0].evidence_refs
    assert any(event.data.get("provider_model") for event in trace.events)
    rendered_trace = json.dumps(
        [event.to_dict() for event in trace.events],
        sort_keys=True,
    )
    assert "Use the hosted web-search tool exactly once" not in rendered_trace
    validate_trace_conformance(system.context.ir, system.plan, trace)
