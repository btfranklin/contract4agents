from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contract4agents import compile_project, materialize
from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.assurance import assess_controls
from contract4agents.ir import FrozenMap, SemanticId, semantic_id
from contract4agents.tracing import (
    TRACE_CLOSURE_MANIFEST_VERSION,
    TRACE_SCHEMA_VERSION,
    AtomicTraceFileSink,
    NoOpNormalizedTraceSink,
    NormalizedTrace,
    OpenAINormalizedTraceRouter,
    ProviderCorrelation,
    RecordingNormalizedTraceSink,
    RedactionMetadata,
    RedactionRule,
    TraceAttempt,
    TraceAttemptClosure,
    TraceClosureCheckpoint,
    TraceClosureError,
    TraceClosureEvidence,
    TraceClosureManifest,
    TraceCompletenessResult,
    TraceConformanceError,
    TraceEvent,
    TraceFrontier,
    TraceLoadError,
    TraceRunContext,
    TraceSemanticRefs,
    assess_trace_completeness,
    dumps_trace_jsonl,
    export_open_telemetry,
    load_trace_jsonl,
    loads_trace_jsonl,
    normalize_openai_exception_responses,
    normalize_openai_response_events,
    resolve_provider_tool_grant,
    validate_trace_closure,
    validate_trace_conformance,
    write_trace_jsonl,
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


def test_trace_round_trips_as_deterministic_jsonl(tmp_path: Path) -> None:
    trace = NormalizedTrace(
        (
            _event("evt-000001", "approval.requested"),
            _event("evt-000002", "approval.completed", parent_event_id="evt-000001"),
        )
    )

    rendered = dumps_trace_jsonl(trace)
    loaded = loads_trace_jsonl(rendered)
    path = tmp_path / "trace.jsonl"
    write_trace_jsonl(path, loaded)

    first_payload = json.loads(rendered.splitlines()[0])
    assert first_payload["schema_version"] == TRACE_SCHEMA_VERSION == "2"
    assert first_payload["run_id"] == "run-123"
    assert first_payload["thread_id"] == "thread-1"
    assert first_payload["contract_digest"] == CONTRACT_DIGEST
    assert first_payload["plan_digest"] == PLAN_DIGEST
    assert first_payload["semantic"]["capability_id"] == "tool:status.publish"
    assert first_payload["semantic"]["composition_id"] == "edge:publish_status"
    assert first_payload["semantic"]["context_id"] == "context:IncidentCommander:incident"
    assert first_payload["semantic"]["isolation_id"] == "isolation:RestrictedPublisher"
    assert first_payload["semantic"]["quality_id"] == "quality:IncidentCommander:clear_update"
    assert first_payload["provider"]["span_id"] == "provider-evt-000001"
    assert first_payload["evidence_refs"] == ["provider:openai:provider-evt-000001"]
    assert first_payload["provenance"] == {"source": "approval_callback"}
    assert first_payload["redaction"] == {"applied": [], "state": "safe"}
    assert dumps_trace_jsonl(loaded) == rendered
    assert load_trace_jsonl(path) == trace
    with pytest.raises(FrozenInstanceError):
        trace.events[0].event_id = "changed"  # type: ignore[misc]


def test_trace_attempt_round_trips_as_validated_event_data() -> None:
    attempt = TraceAttempt("stage:research:1", "attempt-1", 1)
    event = _event(
        "evt-attempt",
        "agent.started",
        data={"attempt": attempt.to_dict()},
    )

    assert TraceAttempt.from_dict(event.data["attempt"]) == attempt
    assert loads_trace_jsonl(dumps_trace_jsonl(NormalizedTrace((event,)))).events[0] == event

    with pytest.raises(ValueError, match="must identify retry_of"):
        TraceAttempt("stage:research:1", "attempt-2", 2)
    with pytest.raises(ValueError, match="first attempt cannot retry"):
        TraceAttempt("stage:research:1", "attempt-1", 1, retry_of="attempt-0")


def test_atomic_trace_writer_preserves_previous_file_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "trace.jsonl"
    original = NormalizedTrace((_event("evt-original", "run.started"),))
    write_trace_jsonl(path, original)

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("contract4agents.tracing._io.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        write_trace_jsonl(path, NormalizedTrace((_event("evt-new", "run.started"),)))

    assert load_trace_jsonl(path) == original
    assert list(tmp_path.glob(".trace.jsonl.*.tmp")) == []


def test_normalized_trace_sinks_record_resume_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    context = _context()
    first = _event("evt-1", "run.started", context=context)
    second = _event("evt-2", "run.completed", context=context)
    recording = RecordingNormalizedTraceSink()
    recording.emit(first)
    NoOpNormalizedTraceSink().emit(first)
    assert recording.events == [first]

    sink = AtomicTraceFileSink(path, context)
    sink.emit(first)
    resumed = AtomicTraceFileSink(path, context, append=True)
    resumed.emit(second)
    assert resumed.events == (first, second)
    assert resumed.normalized_trace() == NormalizedTrace((first, second))
    assert load_trace_jsonl(path) == NormalizedTrace((first, second))

    with pytest.raises(ValueError, match="run context"):
        AtomicTraceFileSink(path, _context(run_id="other"), append=True)
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(TraceLoadError, match="invalid JSON"):
        AtomicTraceFileSink(path, context, append=True)


def test_atomic_trace_file_sink_preserves_file_and_memory_on_candidate_failure(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    context = _context()
    first = _event("evt-1", "run.started", context=context)
    sink = AtomicTraceFileSink(path, context)
    sink.emit(first)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="Duplicate trace event_id"):
        sink.emit(first)

    assert sink.events == (first,)
    assert path.read_bytes() == before


def test_atomic_trace_file_sink_does_not_advance_memory_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "trace.jsonl"
    context = _context()
    first = _event("evt-1", "run.started", context=context)
    second = _event("evt-2", "run.completed", context=context)
    sink = AtomicTraceFileSink(path, context)
    sink.emit(first)
    before = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("contract4agents.tracing._io.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        sink.emit(second)

    assert sink.events == (first,)
    assert path.read_bytes() == before


def test_openai_processor_correlates_native_spans_without_copying_provider_payloads() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    processor = router.open_session(
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
    with processor:
        with processor.bind_attempt(attempt, agent="IncidentCommander"):
            provider_trace = SimpleNamespace(trace_id="trace-provider")
            router.on_trace_start(provider_trace)
            router.on_span_start(agent)
            router.on_span_start(tool)
            router.on_span_end(tool)
            router.on_span_end(agent)
            router.on_trace_end(provider_trace)
    trace = processor.normalized_trace()

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

    closure = session.closure_evidence
    assert closure.complete
    assert closure.covers("provider_response")
    assert "provider.response_batch.normalized" in {
        event.event_type for event in session.normalized_trace().events
    }
    assert TraceClosureManifest.from_json(TraceClosureManifest((closure,)).to_json()).closures == (closure,)
    assert router.active_trace_count == 0


def test_openai_session_checkpoint_binds_frontier_without_requiring_terminal_selection() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(artifacts.ir, system.plan, run_id="run-checkpoint")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    provider_trace = SimpleNamespace(trace_id="trace-checkpoint")
    span = SimpleNamespace(
        trace_id="trace-checkpoint",
        span_id="span-checkpoint",
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
        first = session.checkpoint()
        assert isinstance(first, TraceClosureCheckpoint)
        assert first.closure.complete
        assert first.trace == session.normalized_trace()
        assert first.closure.frontier == TraceFrontier.from_trace(first.trace)
        with pytest.raises(RuntimeError, match="has not been closed"):
            _ = session.closure_evidence

        session.record_terminal_attempt(
            agent="IncidentCommander",
            attempt=attempt,
            outcome="succeeded",
        )
        second = session.checkpoint()
        assert second.closure.complete
        assert second.closure.frontier.event_count == first.closure.frontier.event_count + 1
        assert second.closure.frontier.prefix_digest != first.closure.frontier.prefix_digest
        with pytest.raises(TraceClosureError, match="frontier"):
            validate_trace_closure(second.trace, first.closure)

    assert session.closure_evidence == second.closure


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
    prior_closure = first.closure_evidence
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
        reconciliation = reconciled.checkpoint()
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
    assert conservative.closure_evidence.status == "incomplete"

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
        assert resumed.checkpoint() == TraceClosureCheckpoint(prior_trace, prior_closure)
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

    closure = resumed.closure_evidence
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

    assert session.closure_evidence.status == "incomplete"
    assert router.active_trace_count == 0

    unbound = router.open_session(artifacts.ir, system.plan, run_id="run-unbound")
    with unbound:
        router.on_trace_start(SimpleNamespace(trace_id="trace-without-attempt"))
    assert unbound.closure_evidence.status == "incomplete"
    assert "without attempt identity" in unbound.closure_evidence.reason
    assert router.active_trace_count == 0


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


def test_openai_processor_retains_model_metadata_without_generation_payloads() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    processor = router.open_session(
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

    with processor:
        with processor.bind_attempt(TraceAttempt("generation:1", "generation-attempt-1", 1), agent="IncidentCommander"):
            provider_trace = SimpleNamespace(trace_id="trace-generation")
            router.on_trace_start(provider_trace)
            router.on_span_start(generation)
            router.on_span_end(generation)
            router.on_trace_end(provider_trace)
    rendered = json.dumps(
        [event.to_dict() for event in processor.normalized_trace().events]
    )

    assert all(
        event.data["provider_model"] == "gpt-observed"
        for event in processor.normalized_trace().events
    )
    assert "provider input" not in rendered
    assert "provider output" not in rendered


def test_openai_processor_retains_model_from_agents_sdk_response_span() -> None:
    project = ROOT / "examples" / "market-research-brief"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    processor = router.open_session(
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

    with processor:
        with processor.bind_attempt(TraceAttempt("response:1", "response-attempt-1", 1), agent="CurrentTruthScout"):
            provider_trace = SimpleNamespace(trace_id="trace-response")
            router.on_trace_start(provider_trace)
            router.on_span_start(response)
            router.on_span_end(response)
            router.on_trace_end(provider_trace)
    events = processor.normalized_trace().events
    rendered = json.dumps([event.to_dict() for event in events])

    assert {event.data["provider_model"] for event in events} == {
        "gpt-observed"
    }
    assert "provider input" not in rendered
    assert "provider output" not in rendered


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
    processor = router.open_session(
        artifacts.ir,
        system.plan,
        run_id="run-responses",
        sink=durable,
    )

    attempt = TraceAttempt("scout:1", "scout-attempt-1", 1)
    events = processor.normalize_response_events(
        [
            SimpleNamespace(
                response_id="resp_processor",
                output=[{"id": "ws_processor", "type": "web_search_call"}],
            )
        ],
        agent="CurrentTruthScout",
        attempt=attempt,
    )

    assert processor.normalized_trace() == NormalizedTrace(events)
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


def test_openai_processor_binds_attempt_per_span_until_span_end() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    processor = router.open_session(
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

    with processor:
        with processor.bind_attempt(attempt, agent="IncidentCommander"):
            router.on_trace_start(SimpleNamespace(trace_id="trace-attempt"))
            router.on_span_start(span)
        router.on_span_end(span)
        router.on_trace_end(SimpleNamespace(trace_id="trace-attempt"))

    assert {
        TraceAttempt.from_dict(event.data["attempt"])
        for event in processor.normalized_trace().events
    } == {attempt}


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
    processor = OpenAINormalizedTraceRouter().open_session(
        artifacts.ir,
        system.plan,
        run_id="run-attempts",
    )
    failed = processor.record_output_schema_failure(
        agent="IncidentCommander",
        attempt=first,
    )
    accepted = TraceEvent(
        context=processor.context,
        event_id="host:commander-attempt-2:output-accepted",
        parent_event_id=None,
        event_type="output.accepted",
        timestamp=failed.timestamp + 1,
        semantic=TraceSemanticRefs(agent_id=semantic_id("agent", "IncidentCommander")),
        data={"attempt": second.to_dict()},
        provider=ProviderCorrelation("host"),
        provenance={"source": "host-output-schema-validation"},
    )
    processor.emit(accepted)
    processor.record_terminal_attempt(
        agent="IncidentCommander",
        attempt=second,
        outcome="succeeded",
    )

    trace = processor.normalized_trace()
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
    processor = OpenAINormalizedTraceRouter().open_session(
        artifacts.ir,
        system.plan,
        run_id="run-terminal-failure",
    )
    processor.emit(
        TraceEvent(
            context=processor.context,
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
    processor.record_terminal_attempt(
        agent="IncidentCommander",
        attempt=attempt,
        outcome="failed",
    )

    results = assess_controls(artifacts.ir, system.plan, processor.normalized_trace())
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
    processor = OpenAINormalizedTraceRouter().open_session(
        artifacts.ir,
        system.plan,
        run_id="run-selected-schema-failure",
    )
    processor.record_output_schema_failure(
        agent="IncidentCommander",
        attempt=attempt,
    )
    processor.record_terminal_attempt(
        agent="IncidentCommander",
        attempt=attempt,
        outcome="failed",
    )

    results = assess_controls(artifacts.ir, system.plan, processor.normalized_trace())
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
    processor = OpenAINormalizedTraceRouter().open_session(
        artifacts.ir,
        system.plan,
        run_id="run-missing-selection",
    )
    processor.record_output_schema_failure(
        agent="IncidentCommander",
        attempt=attempt,
    )

    results = assess_controls(artifacts.ir, system.plan, processor.normalized_trace())
    output_result = next(
        result
        for result in results
        if result.control_id == "control:IncidentCommander:output_conformance"
    )

    assert output_result.status == "unverified"
    assert "requires an explicit terminal-attempt selection" in output_result.reason


def test_trace_conformance_rejects_missing_unknown_disabled_and_mismatched_tool_identity() -> None:
    project = ROOT / "examples" / "market-research-brief"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    context = TraceRunContext(
        "run-conformance",
        "run-conformance",
        system.plan.contract_digest,
        system.plan.plan_digest,
    )
    base = normalize_openai_response_events(
        system.plan,
        [{"output": [{"id": "ws-conformance", "type": "web_search_call"}]}],
        agent="CurrentTruthScout",
        context=context,
    )[1]

    missing = replace(base, semantic=TraceSemanticRefs(agent_id=base.semantic.agent_id))
    with pytest.raises(TraceConformanceError, match="TRC005"):
        validate_trace_conformance(artifacts.ir, system.plan, NormalizedTrace((missing,)))

    unknown = replace(
        base,
        semantic=replace(
            base.semantic,
            grant_id=semantic_id("grant", "CurrentTruthScout", "unknown"),
        ),
    )
    with pytest.raises(TraceConformanceError, match="TRC008"):
        validate_trace_conformance(artifacts.ir, system.plan, NormalizedTrace((unknown,)))

    grant_id = base.semantic.grant_id
    assert grant_id is not None
    disabled_grant = replace(system.plan.grants[grant_id], availability="denied")
    disabled_plan = replace(
        system.plan,
        grants=FrozenMap(
            (identifier, disabled_grant if identifier == grant_id else grant)
            for identifier, grant in system.plan.grants.items()
        ),
    )
    disabled_context = replace(context, plan_digest=disabled_plan.plan_digest)
    disabled = replace(base, context=disabled_context)
    with pytest.raises(TraceConformanceError, match="TRC009"):
        validate_trace_conformance(artifacts.ir, disabled_plan, NormalizedTrace((disabled,)))

    mismatched_grant = next(
        grant
        for grant in system.plan.grants.values()
        if grant.agent_id == base.semantic.agent_id and grant.id != grant_id
    )
    mismatched = replace(base, semantic=replace(base.semantic, grant_id=mismatched_grant.id))
    with pytest.raises(TraceConformanceError, match="TRC010"):
        validate_trace_conformance(artifacts.ir, system.plan, NormalizedTrace((mismatched,)))

    wrong_digest = replace(
        base,
        context=replace(context, contract_digest=f"sha256:{'c' * 64}"),
    )
    with pytest.raises(TraceConformanceError, match="TRC002"):
        validate_trace_conformance(artifacts.ir, system.plan, NormalizedTrace((wrong_digest,)))


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "run_id",
        "thread_id",
        "event_id",
        "contract_digest",
        "plan_digest",
        "semantic",
        "provider",
        "provenance",
        "redaction",
    ],
)
def test_loader_rejects_absent_required_identity_and_envelope_fields(field: str) -> None:
    payload = _event("evt-1", "run.started").to_dict()
    del payload[field]

    with pytest.raises(TraceLoadError, match=f"missing required fields: {field}"):
        loads_trace_jsonl(json.dumps(payload))


def test_loader_rejects_duplicate_event_ids() -> None:
    event = _event("evt-1", "run.started")
    content = "\n".join((json.dumps(event.to_dict()), json.dumps(event.to_dict())))

    with pytest.raises(TraceLoadError, match="Duplicate trace event_id `evt-1`"):
        loads_trace_jsonl(content)


@pytest.mark.parametrize("parent_event_id", ["missing", "evt-child"])
def test_loader_rejects_missing_and_cyclic_parent_references(parent_event_id: str) -> None:
    child = _event("evt-child", "run.completed", parent_event_id=parent_event_id)

    with pytest.raises(TraceLoadError, match="missing parent_event_id|parent reference cycle"):
        loads_trace_jsonl(json.dumps(child.to_dict()))


def test_loader_rejects_cross_run_parent_references() -> None:
    parent = _event("evt-parent", "run.started")
    child = _event(
        "evt-child",
        "run.completed",
        parent_event_id="evt-parent",
        context=_context(run_id="run-456", thread_id="thread-2"),
    )

    with pytest.raises(TraceLoadError, match="parent from a different run"):
        loads_trace_jsonl("\n".join(json.dumps(item.to_dict()) for item in (parent, child)))


@pytest.mark.parametrize(
    ("changed_context", "message"),
    [
        (_context(contract_digest=f"sha256:{'c' * 64}"), "mixed contract digests"),
        (_context(plan_digest=f"sha256:{'c' * 64}"), "mixed plan digests"),
        (_context(thread_id="thread-2"), "mixed thread IDs"),
    ],
)
def test_loader_rejects_mixed_run_context(changed_context: TraceRunContext, message: str) -> None:
    events = (
        _event("evt-1", "run.started"),
        _event("evt-2", "run.completed", context=changed_context),
    )

    with pytest.raises(TraceLoadError, match=message):
        loads_trace_jsonl("\n".join(json.dumps(item.to_dict()) for item in events))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("agent_id", "tool:not-an-agent", "must have kind agent"),
        ("capability_id", "agent:not-a-capability", "must have kind tool, datasource"),
        ("grant_id", "not-qualified", "Invalid semantic ID"),
        ("control_ids", ["quality:not-a-control"], "must have kind control"),
    ],
)
def test_loader_rejects_malformed_semantic_references(field: str, value: object, message: str) -> None:
    payload = _event("evt-1", "run.started").to_dict()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    semantic[field] = value

    with pytest.raises(TraceLoadError, match=message):
        loads_trace_jsonl(json.dumps(payload))


def test_trace_completeness_returns_complete_with_expected_evidence() -> None:
    attempt = TraceAttempt("run:1", "run-attempt-1", 1)
    trace = NormalizedTrace(
        (
            _event("evt-1", "run.started", data={"attempt": attempt.to_dict()}),
            _event(
                "evt-2",
                "approval.completed",
                parent_event_id="evt-1",
                data={"attempt": attempt.to_dict(), "approved": True},
            ),
            _event("evt-3", "run.completed", parent_event_id="evt-2", data={"attempt": attempt.to_dict()}),
        )
    )
    closure = TraceClosureEvidence(
        context=trace.events[0].context,
        status="complete",
        reason="The test fixture covers the complete run.",
        frontier=TraceFrontier.from_trace(trace),
        channels=("agent", "approval"),
        attempts=(
            TraceAttemptClosure(
                attempt,
                semantic_id("agent", "IncidentCommander"),
                "complete",
                "complete",
                evidence_refs=("fixture:attempt",),
            ),
        ),
        evidence_refs=("fixture:closure",),
    )

    result = assess_trace_completeness(
        trace,
        {"run.started", "approval.completed", "run.completed"},
        closure=closure,
    )

    assert isinstance(result, TraceCompletenessResult)
    assert result.status == "complete"
    assert result.complete
    assert result.missing_telemetry == ()
    assert "trace-event:evt-2" in result.evidence_refs
    assert "provider:openai:provider-evt-2" in result.evidence_refs


def test_trace_completeness_marks_missing_evidence_unverified() -> None:
    trace = NormalizedTrace((_event("evt-1", "run.started"),))

    result = assess_trace_completeness(trace, {"run.started", "run.completed"})

    assert result.status == "unverified"
    assert not result.complete
    assert result.missing_telemetry == ("run.completed",)
    assert "not observed" in result.reason


def test_trace_closure_manifest_rejects_the_pre_frontier_schema() -> None:
    assert TRACE_CLOSURE_MANIFEST_VERSION == "2"

    with pytest.raises(ValueError, match="Unsupported trace-closure manifest version `1`"):
        TraceClosureManifest.from_dict({"closures": [], "version": "1"})


@pytest.mark.parametrize(
    ("event_count", "prefix_digest", "error"),
    [
        (True, f"sha256:{'a' * 64}", TypeError),
        (-1, f"sha256:{'a' * 64}", ValueError),
        (0, f"sha256:{'A' * 64}", ValueError),
        (0, "sha256:short", ValueError),
    ],
)
def test_trace_frontier_requires_canonical_identity(
    event_count: int,
    prefix_digest: str,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        TraceFrontier(event_count, prefix_digest)


def test_trace_closure_rejects_an_omitted_observed_attempt() -> None:
    first = TraceAttempt("run:1", "attempt-1", 1)
    second = TraceAttempt("run:2", "attempt-2", 1)
    trace = NormalizedTrace(
        (
            _event("evt-1", "agent.started", data={"attempt": first.to_dict()}),
            _event("evt-2", "agent.started", data={"attempt": second.to_dict()}),
        )
    )
    closure = TraceClosureEvidence(
        trace.events[0].context,
        "complete",
        "The caller incorrectly claims complete coverage.",
        TraceFrontier.from_trace(trace),
        ("agent",),
        (
            TraceAttemptClosure(
                first,
                semantic_id("agent", "IncidentCommander"),
                "complete",
                "complete",
                evidence_refs=("fixture:first",),
            ),
        ),
        ("fixture:closure",),
    )

    with pytest.raises(ValueError, match="exactly the attempts"):
        assess_trace_completeness(trace, {"agent.started"}, closure=closure)


def test_trace_completeness_requires_an_explicit_set_and_run_scope() -> None:
    first = _event("evt-1", "run.started")
    second = _event("evt-2", "run.started", context=_context(run_id="run-2", thread_id="thread-2"))
    trace = NormalizedTrace((first, second))

    with pytest.raises(ValueError, match="multiple runs"):
        assess_trace_completeness(trace, {"run.started"})

    result = assess_trace_completeness(trace, set(), run_id="run-2")
    assert result.status == "unverified"
    assert result.reason == "No expected telemetry was declared for this run."


def test_audience_export_redacts_sensitive_values_and_remains_loadable() -> None:
    event = _event(
        "evt-1",
        "tool.started",
        data={"arguments": {"query": "status", "token": "super-secret"}},
        redaction=RedactionMetadata(
            "sensitive",
            rules=(RedactionRule("/data/arguments/token", visible_to=("host", "reviewer")),),
        ),
    )
    trace = NormalizedTrace((event,))

    evaluator_export = dumps_trace_jsonl(trace, audience="evaluator")
    evaluator_payload = json.loads(evaluator_export)
    host_payload = json.loads(dumps_trace_jsonl(trace, audience="host"))

    assert "super-secret" not in evaluator_export
    assert evaluator_payload["data"] == {
        "arguments": {"query": "status", "token": "[redacted]"}
    }
    assert evaluator_payload["redaction"] == {
        "applied": ["/data/arguments/token"],
        "state": "redacted",
    }
    assert host_payload["data"]["arguments"]["token"] == "super-secret"
    assert host_payload["redaction"]["state"] == "sensitive"
    assert loads_trace_jsonl(evaluator_export).events[0].redaction.state == "redacted"


def test_audience_export_can_partially_redact_for_different_audiences() -> None:
    event = _event(
        "evt-1",
        "tool.started",
        data={"host_secret": "host", "reviewer_secret": "reviewer"},
        redaction=RedactionMetadata(
            "sensitive",
            rules=(
                RedactionRule("/data/host_secret", visible_to=("host",)),
                RedactionRule("/data/reviewer_secret", visible_to=("reviewer",)),
            ),
        ),
    )

    rendered = dumps_trace_jsonl(NormalizedTrace((event,)), audience="host")
    payload = json.loads(rendered)

    assert payload["data"] == {"host_secret": "host", "reviewer_secret": "[redacted]"}
    assert payload["redaction"]["state"] == "sensitive"
    assert payload["redaction"]["applied"] == ["/data/reviewer_secret"]
    assert loads_trace_jsonl(rendered).events[0].redaction.state == "sensitive"


def test_loader_rejects_a_false_claim_that_sensitive_data_was_redacted() -> None:
    payload = _event("evt-1", "tool.started", data={"token": "still-secret"}).to_dict()
    payload["redaction"] = {
        "applied": ["/data/token"],
        "state": "redacted",
    }

    with pytest.raises(TraceLoadError, match="is not redacted"):
        loads_trace_jsonl(json.dumps(payload))


def test_sensitive_redaction_rule_must_resolve_and_cannot_target_identity() -> None:
    with pytest.raises(ValueError, match="must target one of"):
        RedactionRule("/run_id")

    redaction = RedactionMetadata(
        "sensitive",
        rules=(RedactionRule("/data/missing"),),
    )
    with pytest.raises(ValueError, match="does not exist"):
        _event("evt-1", "tool.started", redaction=redaction)


def test_trace_context_rejects_noncanonical_digests() -> None:
    with pytest.raises(ValueError, match="canonical sha256"):
        replace(_context(), plan_digest="sha256:not-a-digest")


class _FakeSpan:
    def __init__(self, attributes: object) -> None:
        self.attributes = attributes
        self.events: list[tuple[str, object, int | None]] = []
        self.end_time: int | None = None

    def set_attribute(self, key: str, value: object) -> None:
        del key, value

    def add_event(self, name: str, attributes: object = None, timestamp: int | None = None) -> None:
        self.events.append((name, attributes, timestamp))

    def end(self, end_time: int | None = None) -> None:
        self.end_time = end_time


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []

    def start_span(
        self,
        name: str,
        *,
        attributes: object = None,
        start_time: int | None = None,
    ) -> _FakeSpan:
        assert name == "contract4agents.run"
        assert start_time is not None
        span = _FakeSpan(attributes)
        self.spans.append(span)
        return span


def test_open_telemetry_export_preserves_identity_and_applies_audience_redaction() -> None:
    event = _event(
        "evt-1",
        "tool.started",
        data={"token": "super-secret"},
        redaction=RedactionMetadata(
            "sensitive",
            rules=(RedactionRule("/data/token", visible_to=("reviewer",)),),
        ),
    )
    tracer = _FakeTracer()

    spans = export_open_telemetry(NormalizedTrace((event,)), tracer, audience="host")

    assert spans == tuple(tracer.spans)
    assert len(spans) == 1
    assert spans[0].end_time is not None
    name, attributes, timestamp = tracer.spans[0].events[0]
    assert name == "tool.started"
    assert timestamp is not None
    assert isinstance(attributes, dict)
    assert attributes["contract4agents.event_id"] == "evt-1"
    assert "super-secret" not in str(attributes["contract4agents.data"])
