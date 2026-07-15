from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contract4agents import compile_project, materialize
from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.ir import SemanticId
from contract4agents.tracing import (
    TRACE_SCHEMA_VERSION,
    NormalizedTrace,
    OpenAINormalizedTraceProcessor,
    ProviderCorrelation,
    RedactionMetadata,
    RedactionRule,
    TraceCompletenessResult,
    TraceEvent,
    TraceLoadError,
    TraceRunContext,
    TraceSemanticRefs,
    assess_trace_completeness,
    dumps_trace_jsonl,
    export_open_telemetry,
    load_trace_jsonl,
    loads_trace_jsonl,
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


def test_v2_trace_round_trips_as_deterministic_jsonl(tmp_path: Path) -> None:
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


def test_openai_processor_correlates_native_spans_without_copying_provider_payloads() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    processor = OpenAINormalizedTraceProcessor(
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

    processor.on_span_start(agent)
    processor.on_span_start(tool)
    processor.on_span_end(tool)
    processor.on_span_end(agent)
    processor.on_trace_start(SimpleNamespace())
    processor.on_trace_end(SimpleNamespace())
    processor.force_flush()
    processor.shutdown()
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
    trace = NormalizedTrace(
        (
            _event("evt-1", "run.started"),
            _event("evt-2", "approval.completed", parent_event_id="evt-1"),
            _event("evt-3", "run.completed", parent_event_id="evt-2"),
        )
    )

    result = assess_trace_completeness(
        trace,
        {"run.started", "approval.completed", "run.completed"},
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
