from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contract4agents import compile_project, materialize
from contract4agents.assurance import assess_controls
from contract4agents.ir import FrozenMap, SemanticId, semantic_id
from contract4agents.tracing import (
    NormalizedTrace,
    OpenAINormalizedTraceRouter,
    ProviderCorrelation,
    RecordingNormalizedTraceSink,
    RedactionMetadata,
    TraceAttempt,
    TraceConformanceError,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
    normalize_openai_exception_responses,
    normalize_openai_response_events,
    resolve_provider_tool_grant,
    validate_trace_conformance,
)

CONTRACT_DIGEST = f"sha256:{'a' * 64}"
PLAN_DIGEST = f"sha256:{'b' * 64}"
ROOT = Path(__file__).resolve().parents[2]


def _context(
    *,
    run_id: str = "run-123",
    thread_id: str = "thread-1",
    contract_digest: str = CONTRACT_DIGEST,
    plan_digest: str = PLAN_DIGEST,
) -> TraceRunContext:
    return TraceRunContext(run_id, thread_id, contract_digest, plan_digest)


def _event(
    event_id: str,
    event_type: str,
    *,
    parent_event_id: str | None = None,
    context: TraceRunContext | None = None,
    timestamp: float = 1784098974.25,
    data: dict[str, object] | None = None,
    redaction: RedactionMetadata | None = None,
) -> TraceEvent:
    return TraceEvent(
        context=context or _context(),
        event_id=event_id,
        parent_event_id=parent_event_id,
        event_type=event_type,
        timestamp=timestamp,
        semantic=TraceSemanticRefs(
            agent_id=SemanticId.parse("agent:IncidentCommander"),
            capability_id=SemanticId.parse("tool:status.publish"),
            grant_id=SemanticId.parse("grant:IncidentCommander:status.publish"),
            composition_id=SemanticId.parse("edge:publish_status"),
            context_id=SemanticId.parse("context:IncidentCommander:incident"),
            isolation_id=SemanticId.parse("isolation:RestrictedPublisher"),
            quality_id=SemanticId.parse("quality:IncidentCommander:clear_update"),
            control_ids=(SemanticId.parse("control:IncidentCommander:approval:status.publish"),),
        ),
        data=data or {"approved": True},
        provider=ProviderCorrelation(
            "openai",
            run_id="provider-run-456",
            span_id=f"provider-{event_id}",
        ),
        evidence_refs=(f"provider:openai:provider-{event_id}",),
        provenance={"source": "approval_callback"},
        redaction=redaction or RedactionMetadata(),
    )

def test_openai_response_normalization_resolves_hosted_grants_and_excludes_payloads() -> None:
    project = ROOT / "examples" / "market-research-brief"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    agent_id = SemanticId.parse("agent:CurrentTruthScout")
    context = TraceRunContext(
        "run-hosted",
        "thread-hosted",
        system.plan.contract_digest,
        system.plan.plan_digest,
    )
    response = SimpleNamespace(
        response_id="resp_123",
        request_id="req_123",
        model="gpt-observed",
        output=[
            SimpleNamespace(
                id="ws_123",
                type="web_search_call",
                status="completed",
                action={"query": "secret provider query", "results": ["raw output"]},
            )
        ],
    )

    grant = resolve_provider_tool_grant(
        system.plan,
        agent_id=agent_id,
        provider="openai",
        tool="web_search",
    )
    events = normalize_openai_response_events(
        system.plan,
        [response],
        agent=agent_id,
        context=context,
    )

    assert grant.id == SemanticId.parse("grant:CurrentTruthScout:web.search")
    assert len(events) == 3
    event = events[1]
    assert event.event_type == "tool.completed"
    assert event.semantic.capability_id == SemanticId.parse("tool:web.search")
    assert event.semantic.grant_id == grant.id
    assert event.provider.run_id == "resp_123"
    assert event.provider.request_id == "req_123"
    assert event.data == {
        "provider_model": "gpt-observed",
        "provider_tool": "openai.web_search",
        "status": "completed",
    }
    assert event.evidence_refs == (
        "provider:openai:call:ws_123",
        "provider:openai:response:resp_123",
    )
    assert "secret provider query" not in json.dumps(event.to_dict())
    assert "raw output" not in json.dumps(event.to_dict())
    validate_trace_conformance(artifacts.ir, system.plan, NormalizedTrace(events))

    with pytest.raises(ValueError, match="found 0"):
        resolve_provider_tool_grant(
            system.plan,
            agent_id=agent_id,
            provider="openai",
            tool="file_search",
        )
    duplicate_id = semantic_id("grant", "CurrentTruthScout", "web.search.duplicate")
    duplicate = replace(grant, id=duplicate_id)
    ambiguous_plan = replace(
        system.plan,
        grants=FrozenMap((*system.plan.grants.items(), (duplicate_id, duplicate))),
    )
    with pytest.raises(ValueError, match="found 2"):
        resolve_provider_tool_grant(
            ambiguous_plan,
            agent_id=agent_id,
            provider="openai",
            tool="web_search",
        )


def test_openai_response_normalization_emits_undeclared_evidence_and_assurance_rejects_it() -> None:
    project = ROOT / "examples" / "market-research-brief"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    context = TraceRunContext(
        "run-undeclared",
        "run-undeclared",
        system.plan.contract_digest,
        system.plan.plan_digest,
    )
    response = {
        "response_id": "resp_undeclared",
        "output": [{"id": "ws_undeclared", "type": "web_search_call"}],
    }

    events = normalize_openai_response_events(
        system.plan,
        [response],
        agent="ReportWriter",
        context=context,
    )
    trace = NormalizedTrace(events)

    assert events[1].event_type == "capability.undeclared"
    with pytest.raises(TraceConformanceError, match="TRC004") as exc_info:
        validate_trace_conformance(artifacts.ir, system.plan, trace)
    assert exc_info.value.issues[0].event_id == (
        "openai:hosted-tool:resp_undeclared:ws_undeclared"
    )
    with pytest.raises(TraceConformanceError, match="TRC004"):
        assess_controls(artifacts.ir, system.plan, trace)


def test_openai_response_normalization_fails_closed_for_other_hosted_calls() -> None:
    project = ROOT / "examples" / "market-research-brief"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    context = TraceRunContext(
        "run-other-hosted",
        "run-other-hosted",
        system.plan.contract_digest,
        system.plan.plan_digest,
    )
    response = {
        "response_id": "resp_other_hosted",
        "output": [
            {"id": "fs_123", "type": "file_search_call", "status": "completed"},
            {"id": "mcp_123", "type": "mcp_list_tools", "status": "completed"},
            {"id": "future_123", "type": "future_provider_call"},
        ],
    }

    events = normalize_openai_response_events(
        system.plan,
        [response],
        agent="CurrentTruthScout",
        context=context,
    )

    assert [event.event_type for event in events[1:-1]] == [
        "capability.undeclared",
        "capability.undeclared",
        "capability.undeclared",
    ]
    assert events[1].data["provider_tool"] == "openai.file_search"
    assert events[2].data["provider_tool"] == "openai.mcp_list_tools"
    assert events[3].data["provider_tool"] == (
        "openai.unrecognized:future_provider_call"
    )
    with pytest.raises(TraceConformanceError, match="TRC004"):
        validate_trace_conformance(artifacts.ir, system.plan, NormalizedTrace(events))


@pytest.mark.parametrize(
    ("status", "event_type"),
    [
        ("in_progress", "tool.started"),
        ("completed", "tool.completed"),
        ("failed", "tool.failed"),
        ("incomplete", "tool.failed"),
    ],
)
def test_openai_response_normalization_preserves_hosted_call_status(
    status: str,
    event_type: str,
) -> None:
    project = ROOT / "examples" / "market-research-brief"
    system = materialize(project, "openai", "test")
    context = TraceRunContext(
        f"run-{status}",
        f"run-{status}",
        system.plan.contract_digest,
        system.plan.plan_digest,
    )

    events = normalize_openai_response_events(
        system.plan,
        [{"output": [{"id": f"ws-{status}", "type": "web_search_call", "status": status}]}],
        agent="CurrentTruthScout",
        context=context,
    )

    assert events[1].event_type == event_type


def test_openai_response_normalization_ignores_non_hosted_response_items() -> None:
    project = ROOT / "examples" / "market-research-brief"
    system = materialize(project, "openai", "test")
    context = TraceRunContext(
        "run-non-hosted",
        "run-non-hosted",
        system.plan.contract_digest,
        system.plan.plan_digest,
    )
    response = {
        "response_id": "resp_non_hosted",
        "output": [
            {"id": "fn_123", "type": "function_call"},
            {"id": "custom_123", "type": "custom_tool_call"},
            {"id": "computer_123", "type": "computer_call"},
            {"id": "msg_123", "type": "message"},
            {"id": "reason_123", "type": "reasoning"},
        ],
    }

    events = normalize_openai_response_events(
        system.plan,
        [response],
        agent="CurrentTruthScout",
        context=context,
    )
    assert [event.event_type for event in events] == [
        "provider.response.normalized",
        "provider.response_batch.normalized",
    ]


def test_openai_processor_can_merge_hosted_response_events_into_its_sink() -> None:
    project = ROOT / "examples" / "market-research-brief"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    durable = RecordingNormalizedTraceSink()
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-responses",
        sink=durable,
    )

    attempt = TraceAttempt("scout:1", "scout-attempt-1", 1)
    events = session.normalize_response_events(
        [
            SimpleNamespace(
                response_id="resp_processor",
                output=[{"id": "ws_processor", "type": "web_search_call"}],
            )
        ],
        agent="CurrentTruthScout",
        attempt=attempt,
    )

    assert session.normalized_trace() == NormalizedTrace(events)
    assert durable.events == list(events)


def test_openai_exception_normalization_preserves_responses_and_attempt_identity() -> None:
    project = ROOT / "examples" / "market-research-brief"
    system = materialize(project, "openai", "test")
    context = TraceRunContext(
        "run-exception",
        "run-exception",
        system.plan.contract_digest,
        system.plan.plan_digest,
    )
    attempt = TraceAttempt("planner:1", "planner-attempt-1", 1)
    exception = RuntimeError("provider run failed")
    exception.run_data = SimpleNamespace(  # type: ignore[attr-defined]
        raw_responses=[
            SimpleNamespace(
                response_id="resp_exception",
                output=[{"id": "ws_exception", "type": "web_search_call"}],
            )
        ]
    )

    events = normalize_openai_exception_responses(
        system.plan,
        exception,
        agent="CurrentTruthScout",
        context=context,
        attempt=attempt,
    )

    assert len(events) == 3
    assert all(TraceAttempt.from_dict(event.data["attempt"]) == attempt for event in events)
    assert events[1].provider.run_id == "resp_exception"
    assert normalize_openai_exception_responses(
        system.plan,
        RuntimeError("no run data"),
        agent="CurrentTruthScout",
        context=context,
    ) == ()

    malformed = RuntimeError("malformed run data")
    malformed.run_data = SimpleNamespace(raw_responses=None)  # type: ignore[attr-defined]
    with pytest.raises(TypeError, match="raw_responses"):
        normalize_openai_exception_responses(
            system.plan,
            malformed,
            agent="CurrentTruthScout",
            context=context,
        )
