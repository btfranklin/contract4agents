from __future__ import annotations

from pathlib import Path

import pytest

from contract4agents import compile_project, materialize
from contract4agents.assurance import assess_controls
from contract4agents.ir import SemanticId, semantic_id
from contract4agents.tracing import (
    NormalizedTrace,
    OpenAINormalizedTraceRouter,
    ProviderCorrelation,
    RedactionMetadata,
    TraceAttempt,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
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

def test_trace_rejects_missing_retry_evidence_and_unobserved_selection() -> None:
    first = TraceAttempt("stage:1", "attempt-1", 1)
    retry = TraceAttempt("stage:1", "attempt-2", 2, retry_of=first.attempt_id)
    retry_event = _event(
        "evt-retry",
        "agent.started",
        data={"attempt": retry.to_dict()},
    )
    with pytest.raises(ValueError, match="missing retry_of"):
        NormalizedTrace((retry_event,))

    selected = _event(
        "evt-selected",
        "attempt.selected",
        data={"attempt": first.to_dict(), "outcome": "succeeded"},
    )
    with pytest.raises(ValueError, match="without observed execution evidence"):
        NormalizedTrace((selected,))


def test_attempt_validation_is_scoped_to_each_normalized_run() -> None:
    attempt = TraceAttempt("stage:1", "attempt-1", 1)
    first_context = _context(run_id="run-1", thread_id="thread-1")
    second_context = _context(run_id="run-2", thread_id="thread-2")
    events = (
        _event(
            "run-1-start",
            "agent.started",
            context=first_context,
            data={"attempt": attempt.to_dict()},
        ),
        _event(
            "run-1-selected",
            "attempt.selected",
            context=first_context,
            data={"attempt": attempt.to_dict(), "outcome": "succeeded"},
        ),
        _event(
            "run-2-start",
            "agent.started",
            context=second_context,
            data={"attempt": attempt.to_dict()},
        ),
        _event(
            "run-2-selected",
            "attempt.selected",
            context=second_context,
            data={"attempt": attempt.to_dict(), "outcome": "succeeded"},
        ),
    )

    trace = NormalizedTrace(events)

    assert trace.run_ids == ("run-1", "run-2")


def test_output_assurance_uses_explicit_terminal_attempt_without_erasing_failures() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    first = TraceAttempt("commander:1", "commander-attempt-1", 1)
    second = TraceAttempt(
        "commander:1",
        "commander-attempt-2",
        2,
        retry_of=first.attempt_id,
    )
    session = OpenAINormalizedTraceRouter().open_session(
        artifacts.ir,
        system.plan,
        run_id="run-attempts",
    )
    failed = session.record_output_schema_failure(
        agent="IncidentCommander",
        attempt=first,
    )
    accepted = TraceEvent(
        context=session.context,
        event_id="host:commander-attempt-2:output-accepted",
        parent_event_id=None,
        event_type="output.accepted",
        timestamp=failed.timestamp + 1,
        semantic=TraceSemanticRefs(agent_id=semantic_id("agent", "IncidentCommander")),
        data={"attempt": second.to_dict()},
        provider=ProviderCorrelation("host"),
        provenance={"source": "host-output-schema-validation"},
    )
    session.emit(accepted)
    session.record_terminal_attempt(
        agent="IncidentCommander",
        attempt=second,
        outcome="succeeded",
    )

    trace = session.normalized_trace()
    results = assess_controls(artifacts.ir, system.plan, trace)
    output_result = next(
        result
        for result in results
        if result.control_id == "control:IncidentCommander:output_conformance"
    )

    assert output_result.status == "passed"
    assert failed.event_id not in output_result.evidence_event_ids
    assert failed in trace.events


def test_failed_selected_terminal_attempt_leaves_output_assurance_unverified() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    session = OpenAINormalizedTraceRouter().open_session(
        artifacts.ir,
        system.plan,
        run_id="run-terminal-failure",
    )
    session.emit(
        TraceEvent(
            context=session.context,
            event_id="host:commander-attempt-1:failed",
            parent_event_id=None,
            event_type="agent.failed",
            timestamp=1784098974.25,
            semantic=TraceSemanticRefs(agent_id=semantic_id("agent", "IncidentCommander")),
            data={"attempt": attempt.to_dict()},
            provider=ProviderCorrelation("host"),
            provenance={"source": "host-runner"},
        )
    )
    session.record_terminal_attempt(
        agent="IncidentCommander",
        attempt=attempt,
        outcome="failed",
    )

    results = assess_controls(artifacts.ir, system.plan, session.normalized_trace())
    output_result = next(
        result
        for result in results
        if result.control_id == "control:IncidentCommander:output_conformance"
    )

    assert output_result.status == "unverified"
    assert "failed without output-schema evidence" in output_result.reason


def test_selected_schema_failed_attempt_violates_output_assurance() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    session = OpenAINormalizedTraceRouter().open_session(
        artifacts.ir,
        system.plan,
        run_id="run-selected-schema-failure",
    )
    session.record_output_schema_failure(
        agent="IncidentCommander",
        attempt=attempt,
    )
    session.record_terminal_attempt(
        agent="IncidentCommander",
        attempt=attempt,
        outcome="failed",
    )

    results = assess_controls(artifacts.ir, system.plan, session.normalized_trace())
    output_result = next(
        result
        for result in results
        if result.control_id == "control:IncidentCommander:output_conformance"
    )

    assert output_result.status == "violated"
    assert "schema validation failed" in output_result.reason


def test_attempt_scoped_output_without_terminal_selection_is_unverified() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    session = OpenAINormalizedTraceRouter().open_session(
        artifacts.ir,
        system.plan,
        run_id="run-missing-selection",
    )
    session.record_output_schema_failure(
        agent="IncidentCommander",
        attempt=attempt,
    )

    results = assess_controls(artifacts.ir, system.plan, session.normalized_trace())
    output_result = next(
        result
        for result in results
        if result.control_id == "control:IncidentCommander:output_conformance"
    )

    assert output_result.status == "unverified"
    assert "requires an explicit terminal-attempt selection" in output_result.reason
