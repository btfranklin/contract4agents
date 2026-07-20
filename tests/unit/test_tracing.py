from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from contract4agents import compile_project, materialize
from contract4agents.ir import FrozenMap, SemanticId, semantic_id
from contract4agents.tracing import (
    TRACE_CLOSURE_MANIFEST_VERSION,
    TRACE_SCHEMA_VERSION,
    AtomicTraceFileSink,
    NoOpNormalizedTraceSink,
    NormalizedTrace,
    ProviderCorrelation,
    RecordingNormalizedTraceSink,
    RedactionMetadata,
    RedactionRule,
    TraceAttempt,
    TraceAttemptClosure,
    TraceClosureEvidence,
    TraceClosureManifest,
    TraceConformanceError,
    TraceEvent,
    TraceEvidenceAssessment,
    TraceFrontier,
    TraceLoadError,
    TraceRunContext,
    TraceSemanticRefs,
    assess_trace_evidence,
    dumps_trace_jsonl,
    export_open_telemetry,
    load_trace_jsonl,
    loads_trace_jsonl,
    normalize_openai_response_events,
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
    assert first_payload["schema_version"] == TRACE_SCHEMA_VERSION == "1"
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


def test_trace_evidence_returns_complete_with_expected_evidence() -> None:
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

    result = assess_trace_evidence(
        trace,
        {"run.started", "approval.completed", "run.completed"},
        closure=closure,
    )

    assert isinstance(result, TraceEvidenceAssessment)
    assert result.status == "complete"
    assert result.complete
    assert result.missing_event_types == ()
    assert "trace-event:evt-2" in result.evidence_refs
    assert "provider:openai:provider-evt-2" in result.evidence_refs


def test_trace_evidence_marks_missing_evidence_unverified() -> None:
    trace = NormalizedTrace((_event("evt-1", "run.started"),))

    result = assess_trace_evidence(trace, {"run.started", "run.completed"})

    assert result.status == "unverified"
    assert not result.complete
    assert result.missing_event_types == ("run.completed",)
    assert "not observed" in result.reason


def test_trace_closure_manifest_rejects_an_unsupported_version() -> None:
    assert TRACE_CLOSURE_MANIFEST_VERSION == "1"

    with pytest.raises(ValueError, match="Unsupported trace-closure manifest version `2`"):
        TraceClosureManifest.from_dict({"closures": [], "version": "2"})


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
        assess_trace_evidence(trace, {"agent.started"}, closure=closure)


def test_trace_evidence_requires_an_explicit_set_and_run_scope() -> None:
    first = _event("evt-1", "run.started")
    second = _event("evt-2", "run.started", context=_context(run_id="run-2", thread_id="thread-2"))
    trace = NormalizedTrace((first, second))

    with pytest.raises(ValueError, match="multiple runs"):
        assess_trace_evidence(trace, {"run.started"})

    result = assess_trace_evidence(trace, set(), run_id="run-2")
    assert result.status == "unverified"
    assert result.reason == "No expected event types were declared for this run."


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
