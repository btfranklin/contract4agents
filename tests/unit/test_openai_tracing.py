from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contract4agents import compile_project, materialize
from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.ir import SemanticId
from contract4agents.tracing import (
    OpenAINormalizedTraceRouter,
    ProviderCorrelation,
    RedactionMetadata,
    TraceAttempt,
    TraceCaptureSnapshot,
    TraceClosureError,
    TraceClosureManifest,
    TraceEvent,
    TraceFrontier,
    TraceRunContext,
    TraceSemanticRefs,
    validate_trace_closure,
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

def test_openai_processor_correlates_native_spans_without_copying_provider_payloads() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-openai",
        thread_id="thread-openai",
    )
    agent = SimpleNamespace(
        trace_id="trace-provider",
        span_id="span-agent",
        parent_id=None,
        started_at="2026-07-15T12:00:00Z",
        ended_at="2026-07-15T12:00:02Z",
        error=None,
        span_data=SimpleNamespace(type="agent", name="IncidentCommander"),
    )
    tool = SimpleNamespace(
        trace_id="trace-provider",
        span_id="span-tool",
        parent_id="span-agent",
        started_at="2026-07-15T12:00:00.500000Z",
        ended_at="2026-07-15T12:00:01Z",
        error=None,
        span_data=SimpleNamespace(
            type="function",
            name=openai_tool_name("logs.search"),
            input='{"sensitive":"not normalized"}',
            output="also not normalized",
        ),
    )

    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    with session:
        with session.bind_attempt(attempt, agent="IncidentCommander"):
            provider_trace = SimpleNamespace(trace_id="trace-provider")
            router.on_trace_start(provider_trace)
            router.on_span_start(agent)
            router.on_span_start(tool)
            router.on_span_end(tool)
            router.on_span_end(agent)
            router.on_trace_end(provider_trace)
    trace = session.normalized_trace()

    assert [event.event_type for event in trace.events] == [
        "agent.started",
        "tool.started",
        "tool.completed",
        "output.accepted",
        "agent.completed",
    ]
    assert trace.events[1].semantic.agent_id == SemanticId.parse("agent:IncidentCommander")
    assert trace.events[1].semantic.capability_id == SemanticId.parse("tool:logs.search")
    assert trace.events[1].semantic.grant_id == SemanticId.parse(
        "grant:IncidentCommander:logs.search"
    )
    assert trace.events[1].provider.trace_id == "trace-provider"
    assert trace.events[1].parent_event_id == "openai:span-agent:started"
    assert all("sensitive" not in json.dumps(event.to_dict()) for event in trace.events)


def test_openai_processors_capture_only_their_bound_sdk_trace() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    first = router.open_session(artifacts.ir, system.plan, run_id="run-first")
    second = router.open_session(artifacts.ir, system.plan, run_id="run-second")

    def dispatch(trace_id: str, span_id: str) -> None:
        provider_trace = SimpleNamespace(trace_id=trace_id)
        span = SimpleNamespace(
            trace_id=trace_id,
            span_id=span_id,
            parent_id=None,
            started_at="2026-07-15T12:00:00Z",
            ended_at="2026-07-15T12:00:01Z",
            error=None,
            span_data=SimpleNamespace(type="agent", name="IncidentCommander"),
        )
        router.on_trace_start(provider_trace)
        router.on_span_start(span)
        router.on_span_end(span)
        router.on_trace_end(provider_trace)

    with first:
        with first.bind_attempt(TraceAttempt("first:1", "first-attempt-1", 1), agent="IncidentCommander"):
            dispatch("trace-first", "span-first")
    with second:
        with second.bind_attempt(TraceAttempt("second:1", "second-attempt-1", 1), agent="IncidentCommander"):
            dispatch("trace-second", "span-second")

    assert {event.provider.trace_id for event in first.normalized_trace().events} == {
        "trace-first"
    }
    assert {event.provider.trace_id for event in second.normalized_trace().events} == {
        "trace-second"
    }
    assert router.active_trace_count == 0


def test_openai_router_session_closes_lifecycle_and_zero_response_batch() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(artifacts.ir, system.plan, run_id="run-closed")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    span = SimpleNamespace(
        trace_id="trace-closed",
        span_id="span-closed",
        parent_id=None,
        started_at="2026-07-15T12:00:00Z",
        ended_at="2026-07-15T12:00:01Z",
        error=None,
        span_data=SimpleNamespace(type="agent", name="IncidentCommander"),
    )

    with session:
        with session.bind_attempt(attempt, agent="IncidentCommander"):
            provider_trace = SimpleNamespace(trace_id="trace-closed")
            router.on_trace_start(provider_trace)
            router.on_span_start(span)
            router.on_span_end(span)
            router.on_trace_end(provider_trace)
            session.record_result(
                SimpleNamespace(raw_responses=[]),
                agent="IncidentCommander",
                attempt=attempt,
            )

    closure = session.closed_snapshot.closure
    assert closure.complete
    assert closure.covers("provider_response")
    assert "provider.response_batch.normalized" in {
        event.event_type for event in session.normalized_trace().events
    }
    assert TraceClosureManifest.from_json(TraceClosureManifest((closure,)).to_json()).closures == (closure,)
    assert router.active_trace_count == 0
    assert session.close() is session.closed_snapshot


def test_openai_session_snapshot_binds_frontier_without_requiring_terminal_selection() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(artifacts.ir, system.plan, run_id="run-snapshot")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    provider_trace = SimpleNamespace(trace_id="trace-snapshot")
    span = SimpleNamespace(
        trace_id="trace-snapshot",
        span_id="span-snapshot",
        parent_id=None,
        started_at="2026-07-15T12:00:00Z",
        ended_at="2026-07-15T12:00:01Z",
        error=None,
        span_data=SimpleNamespace(type="agent", name="IncidentCommander"),
    )

    with session:
        with session.bind_attempt(attempt, agent="IncidentCommander"):
            router.on_trace_start(provider_trace)
            router.on_span_start(span)
            router.on_span_end(span)
            router.on_trace_end(provider_trace)
            session.record_result(
                SimpleNamespace(raw_responses=[]),
                agent="IncidentCommander",
                attempt=attempt,
            )
        first = session.snapshot()
        assert isinstance(first, TraceCaptureSnapshot)
        assert first.closure.complete
        assert first.trace == session.normalized_trace()
        assert first.closure.frontier == TraceFrontier.from_trace(first.trace)
        with pytest.raises(RuntimeError, match="has not been closed"):
            _ = session.closed_snapshot.closure

        session.record_terminal_attempt(
            agent="IncidentCommander",
            attempt=attempt,
            outcome="succeeded",
        )
        second = session.snapshot()
        assert second.closure.complete
        assert second.closure.frontier.event_count == first.closure.frontier.event_count + 1
        assert second.closure.frontier.prefix_digest != first.closure.frontier.prefix_digest
        with pytest.raises(TraceClosureError, match="frontier"):
            validate_trace_closure(second.trace, first.closure)

    assert session.closed_snapshot.closure == second.closure


def test_openai_session_resumes_validated_closure_and_retry_chain() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    first = router.open_session(artifacts.ir, system.plan, run_id="run-resumed")
    original = TraceAttempt("commander:1", "commander-attempt-1", 1)

    with first:
        with first.bind_attempt(original, agent="IncidentCommander"):
            first_trace = SimpleNamespace(trace_id="trace-original")
            first_span = SimpleNamespace(
                trace_id="trace-original",
                span_id="span-original",
                parent_id=None,
                started_at="2026-07-15T12:00:00Z",
                ended_at="2026-07-15T12:00:01Z",
                error=None,
                span_data=SimpleNamespace(type="agent", name="IncidentCommander"),
            )
            router.on_trace_start(first_trace)
            router.on_span_start(first_span)
            router.on_span_end(first_span)
            router.on_trace_end(first_trace)
            first.record_result(
                SimpleNamespace(raw_responses=[]),
                agent="IncidentCommander",
                attempt=original,
            )
        first.attest_channels(("approval",), evidence_refs=("host:approval-log",))

    prior_trace = first.normalized_trace()
    prior_closure = first.closed_snapshot.closure
    with pytest.raises(ValueError, match="supplied together"):
        router.open_session(
            artifacts.ir,
            system.plan,
            run_id="run-resumed",
            prior_trace=prior_trace,
        )
    with pytest.raises(ValueError, match="session context"):
        router.open_session(
            artifacts.ir,
            system.plan,
            run_id="run-resumed",
            thread_id="different-thread",
            prior_trace=prior_trace,
            prior_closure=prior_closure,
        )
    reconciled = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-resumed",
        prior_trace=prior_trace,
        prior_closure=prior_closure,
    )
    with reconciled:
        with pytest.raises(ValueError, match="belongs to"):
            reconciled.record_terminal_attempt(
                agent="MetricsAnalyst",
                attempt=original,
                outcome="succeeded",
            )
        reconciled.record_terminal_attempt(
            agent="IncidentCommander",
            attempt=original,
            outcome="succeeded",
        )
        reconciliation = reconciled.snapshot()
        assert reconciliation.closure.complete
        assert "approval" in reconciliation.closure.channels
        validate_trace_closure(reconciliation.trace, reconciliation.closure)

    conservative = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-resumed",
        prior_trace=prior_trace,
        prior_closure=replace(
            prior_closure,
            status="incomplete",
            reason="The prior process did not close every instrumentation path.",
        ),
    )
    with conservative:
        conservative.record_output_schema_failure(
            agent="IncidentCommander",
            attempt=original,
        )
    assert conservative.closed_snapshot.closure.status == "incomplete"

    resumed = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-resumed",
        prior_trace=prior_trace,
        prior_closure=prior_closure,
    )
    retry = TraceAttempt(
        "commander:1",
        "commander-attempt-2",
        2,
        retry_of=original.attempt_id,
    )

    with resumed:
        assert resumed.snapshot() == TraceCaptureSnapshot(prior_trace, prior_closure)
        with pytest.raises(ValueError, match="sealed by prior closure"):
            resumed.record_result(
                SimpleNamespace(raw_responses=[]),
                agent="IncidentCommander",
                attempt=original,
            )
        with pytest.raises(ValueError, match="not present in prior or current"):
            resumed.record_terminal_attempt(
                agent="IncidentCommander",
                attempt=TraceAttempt("other:1", "other-attempt-1", 1),
                outcome="failed",
            )
        with pytest.raises(ValueError, match="sealed by prior closure"):
            with resumed.bind_attempt(original, agent="IncidentCommander"):
                pass
        with resumed.bind_attempt(retry, agent="IncidentCommander"):
            retry_trace = SimpleNamespace(trace_id="trace-retry")
            retry_span = SimpleNamespace(
                trace_id="trace-retry",
                span_id="span-retry",
                parent_id=None,
                started_at="2026-07-15T12:00:02Z",
                ended_at="2026-07-15T12:00:03Z",
                error=None,
                span_data=SimpleNamespace(type="agent", name="IncidentCommander"),
            )
            router.on_trace_start(retry_trace)
            router.on_span_start(retry_span)
            router.on_span_end(retry_span)
            router.on_trace_end(retry_trace)
            resumed.record_result(
                SimpleNamespace(raw_responses=[]),
                agent="IncidentCommander",
                attempt=retry,
            )
        resumed.record_terminal_attempt(
            agent="IncidentCommander",
            attempt=retry,
            outcome="succeeded",
        )

    closure = resumed.closed_snapshot.closure
    trace = resumed.normalized_trace()
    assert closure.complete
    assert [item.attempt for item in closure.attempts] == [original, retry]
    assert "approval" not in closure.channels
    assert closure.frontier == TraceFrontier.from_trace(trace)
    validate_trace_closure(trace, closure)


def test_openai_session_close_releases_unended_provider_trace() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(artifacts.ir, system.plan, run_id="run-abandoned")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)

    with session:
        with session.bind_attempt(attempt, agent="IncidentCommander"):
            router.on_trace_start(SimpleNamespace(trace_id="trace-never-ended"))
            session.record_result(
                SimpleNamespace(raw_responses=[]),
                agent="IncidentCommander",
                attempt=attempt,
            )
        assert router.active_trace_count == 1

    assert session.closed_snapshot.closure.status == "incomplete"
    assert router.active_trace_count == 0

    unbound = router.open_session(artifacts.ir, system.plan, run_id="run-unbound")
    with unbound:
        router.on_trace_start(SimpleNamespace(trace_id="trace-without-attempt"))
    assert unbound.closed_snapshot.closure.status == "incomplete"
    assert "without attempt identity" in unbound.closed_snapshot.closure.reason
    assert {event.event_type for event in unbound.closed_snapshot.trace.events} == {
        "instrumentation.unbound"
    }
    assert router.active_trace_count == 0

    empty = router.open_session(artifacts.ir, system.plan, run_id="run-empty")
    empty_snapshot = empty.close()
    assert empty_snapshot.closure.status == "unverified"
    assert [event.event_type for event in empty_snapshot.trace.events] == [
        "instrumentation.empty"
    ]


def test_openai_processor_retains_model_metadata_without_generation_payloads() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-generation",
    )
    generation = SimpleNamespace(
        trace_id="trace-generation",
        span_id="span-generation",
        parent_id=None,
        started_at="2026-07-15T12:00:00Z",
        ended_at="2026-07-15T12:00:01Z",
        error=None,
        span_data=SimpleNamespace(
            type="generation",
            model="gpt-observed",
            input=[{"secret": "provider input"}],
            output=[{"secret": "provider output"}],
        ),
    )

    with session:
        with session.bind_attempt(TraceAttempt("generation:1", "generation-attempt-1", 1), agent="IncidentCommander"):
            provider_trace = SimpleNamespace(trace_id="trace-generation")
            router.on_trace_start(provider_trace)
            router.on_span_start(generation)
            router.on_span_end(generation)
            router.on_trace_end(provider_trace)
    rendered = json.dumps(
        [event.to_dict() for event in session.normalized_trace().events]
    )

    assert all(
        event.data["provider_model"] == "gpt-observed"
        for event in session.normalized_trace().events
    )
    assert "provider input" not in rendered
    assert "provider output" not in rendered


def test_openai_processor_retains_model_from_agents_sdk_response_span() -> None:
    project = ROOT / "examples" / "market-research-brief"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-response",
    )
    response = SimpleNamespace(
        trace_id="trace-response",
        span_id="span-response",
        parent_id=None,
        started_at="2026-07-15T12:00:00Z",
        ended_at="2026-07-15T12:00:01Z",
        error=None,
        span_data=SimpleNamespace(
            type="response",
            response=SimpleNamespace(
                model="gpt-observed",
                output=[{"secret": "provider output"}],
            ),
            input=[{"secret": "provider input"}],
        ),
    )

    with session:
        with session.bind_attempt(TraceAttempt("response:1", "response-attempt-1", 1), agent="CurrentTruthScout"):
            provider_trace = SimpleNamespace(trace_id="trace-response")
            router.on_trace_start(provider_trace)
            router.on_span_start(response)
            router.on_span_end(response)
            router.on_trace_end(provider_trace)
    events = session.normalized_trace().events
    rendered = json.dumps([event.to_dict() for event in events])

    assert {event.data["provider_model"] for event in events} == {
        "gpt-observed"
    }
    assert "provider input" not in rendered
    assert "provider output" not in rendered


def test_openai_processor_binds_attempt_per_span_until_span_end() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-bound-attempt",
    )
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    span = SimpleNamespace(
        trace_id="trace-attempt",
        span_id="span-attempt",
        parent_id=None,
        started_at="2026-07-15T12:00:00Z",
        ended_at="2026-07-15T12:00:01Z",
        error=None,
        span_data=SimpleNamespace(type="agent", name="IncidentCommander"),
    )

    with session:
        with session.bind_attempt(attempt, agent="IncidentCommander"):
            router.on_trace_start(SimpleNamespace(trace_id="trace-attempt"))
            router.on_span_start(span)
        router.on_span_end(span)
        router.on_trace_end(SimpleNamespace(trace_id="trace-attempt"))

    assert {
        TraceAttempt.from_dict(event.data["attempt"])
        for event in session.normalized_trace().events
    } == {attempt}
