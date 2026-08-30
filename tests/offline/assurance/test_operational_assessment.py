from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from contract4agents.assurance import (
    AssessorIdentity,
    OperationalControlResult,
    assess_operational_controls,
)
from contract4agents.expressions import ExpressionError, parse_operational_requirement
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    FrozenMap,
    OperationalControlIR,
    TypeIR,
    contract_digest,
    parse_type_ref,
    semantic_id,
)
from contract4agents.planning import AdapterPlan, AgentPlan, MaterializationPlan, OperationalControlMappingPlan
from contract4agents.tracing import (
    NormalizedTrace,
    ProviderCorrelation,
    ProviderOutcomeEvidence,
    ProviderUsageEvidence,
    TraceAttempt,
    TraceAttemptClosure,
    TraceClosureError,
    TraceClosureEvidence,
    TraceClosureManifest,
    TraceEvent,
    TraceFrontier,
    TraceRunContext,
    TraceSemanticRefs,
)

ROOT = Path(__file__).resolve().parents[3]


def _outcome(**overrides: object) -> ProviderOutcomeEvidence:
    values: dict[str, object] = {
        "agent_id": semantic_id("agent", "Researcher"),
        "attempt_id": "attempt-1",
        "phase": "response",
        "outcome": "succeeded",
        "category": "transport",
        "state": "observed",
        "classifier_provenance": "test.provider",
    }
    values.update(overrides)
    return ProviderOutcomeEvidence(**values)  # type: ignore[arg-type]


def _usage(**overrides: object) -> ProviderUsageEvidence:
    values: dict[str, object] = {
        "scope": "attempt",
        "coverage": "complete",
        "aggregation_identity": "attempt-1",
        "aggregation_basis": "one test call",
        "provenance": "test.provider",
        "request_count": 1,
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "output_tokens": 5,
        "reasoning_tokens": 1,
        "total_tokens": 15,
        "agent_id": semantic_id("agent", "Researcher"),
        "attempt_id": "attempt-1",
    }
    values.update(overrides)
    return ProviderUsageEvidence(**values)  # type: ignore[arg-type]


def test_operational_result_is_frozen_and_strictly_serializable() -> None:
    result = OperationalControlResult(
        "operational:Researcher:latency",
        "passed",
        "Observed duration=2; required < 3.",
        assessor=AssessorIdentity("test", "1"),
        metric="duration",
        actual=2.0,
        target=3.0,
        operator="<",
        evidence_event_ids=("evt-2", "evt-1", "evt-1"),
    )
    assert OperationalControlResult.from_json(result.to_json()) == result
    assert result.control_id == result.operational_control_id
    with pytest.raises(ValueError, match="finite"):
        OperationalControlResult("operational:Researcher:x", "passed", "x", actual=-1)

    with pytest.raises(ValueError, match="missing"):
        OperationalControlResult.from_dict({"operational_control_id": "x"})
    with pytest.raises(ValueError, match="Invalid operational"):
        OperationalControlResult.from_dict({**result.to_dict(), "unexpected": True})
    with pytest.raises(ValueError, match="Invalid operational-control result JSON"):
        OperationalControlResult.from_json("not-json")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("operational_control_id", "", "non-empty"),
        ("status", "unknown", "Unsupported assurance status"),
        ("reason", "", "non-empty"),
        ("assessment", "unknown", "Unsupported operational assessment"),
        ("metric", "unknown", "Unsupported operational metric"),
        ("operator", "unknown", "Unsupported operational operator"),
        ("actual", True, "numeric"),
        ("target", float("inf"), "finite"),
    ],
)
def test_operational_result_rejects_invalid_fields(field: str, value: object, message: str) -> None:
    values: dict[str, object] = {
        "operational_control_id": "operational:Researcher:x",
        "status": "passed",
        "reason": "fixture",
    }
    values[field] = value
    with pytest.raises((TypeError, ValueError), match=message):
        OperationalControlResult(**values)  # type: ignore[arg-type]


def test_operational_expression_parser_is_small_and_explicit() -> None:
    parsed = parse_operational_requirement("trace.duration < 250ms")
    assert parsed.metric == "duration"
    assert parsed.target == 0.25
    assert parse_operational_requirement("trace.retries >= 1").metric == "retry_count"
    with pytest.raises(ExpressionError, match="Unsupported operational"):
        parse_operational_requirement("trace.duration < 2m")


def _operational_fixture(
    requirements: tuple[str, ...],
    *,
    include_usage: bool = True,
    include_outcome: bool = True,
    closed: bool = True,
) -> tuple[CanonicalIR, MaterializationPlan, NormalizedTrace, TraceClosureEvidence | None]:
    agent_id = semantic_id("agent", "Researcher")
    controls = tuple(
        OperationalControlIR(
            semantic_id("operational", "Researcher", f"control-{index}"),
            f"control-{index}",
            agent_id,
            "high",
            requirement,
        )
        for index, requirement in enumerate(requirements, 1)
    )
    ir = CanonicalIR.create(
        types=(TypeIR(semantic_id("type", "Result"), "Result", ()),),
        agents=(AgentIR(agent_id, "Researcher", (), parse_type_ref("Result"), "Research."),),
        operational_controls=controls,
    )
    mappings = FrozenMap(
        {
            control.id: OperationalControlMappingPlan(
                control.id,
                control.agent_id,
                control.severity,
                control.requirement,
                control.window,
                "emulated",
                "contract4agents.single_run_operational_assessor",
                ("agent.started", "agent.completed"),
            )
            for control in controls
        }
    )
    plan = MaterializationPlan(
        contract_digest(ir),
        "test",
        "test",
        AdapterPlan("test", "1"),
        FrozenMap({agent_id: AgentPlan(agent_id, "Researcher", "test", FrozenMap(), parse_type_ref("Result"), ())}),
        FrozenMap(),
        FrozenMap(),
        FrozenMap(),
        FrozenMap(),
        FrozenMap(),
        FrozenMap(),
        (),
        (
            "attempt.selected",
            "agent.started",
            "agent.completed",
            "provider.outcome.reported",
            "provider.usage.reported",
        ),
        mappings,
    )
    context = TraceRunContext("run-1", "thread-1", contract_digest(ir), plan.plan_digest)
    attempt = TraceAttempt("research:1", "research-attempt-1", 1)
    events = [
        TraceEvent(
            context,
            "attempt-selected",
            None,
            "attempt.selected",
            0.5,
            TraceSemanticRefs(agent_id=agent_id),
            {"attempt": attempt.to_dict(), "outcome": "succeeded"},
            ProviderCorrelation("test"),
        ),
        TraceEvent(
            context,
            "agent-started",
            None,
            "agent.started",
            1.0,
            TraceSemanticRefs(agent_id=agent_id),
            {"attempt": attempt.to_dict()},
            ProviderCorrelation("test"),
        ),
        TraceEvent(
            context,
            "agent-completed",
            "agent-started",
            "agent.completed",
            3.0,
            TraceSemanticRefs(agent_id=agent_id),
            {"attempt": attempt.to_dict()},
            ProviderCorrelation("test"),
        ),
    ]
    if include_outcome:
        failed = _outcome(
            agent_id=agent_id,
            attempt_id=attempt.attempt_id,
            invocation_id=attempt.invocation_id,
            outcome="failed",
            category="provider_error",
            classifier_provenance="fixture",
            request_id="request-1",
        )
        events.append(
            TraceEvent(
                context,
                "provider-outcome",
                None,
                "provider.outcome.reported",
                2.0,
                TraceSemanticRefs(agent_id=agent_id),
                {"attempt": attempt.to_dict(), "evidence": failed.to_dict()},
                ProviderCorrelation("test", request_id="request-1"),
            )
        )
    if include_usage:
        usage = _usage(
            agent_id=agent_id,
            attempt_id=attempt.attempt_id,
            invocation_id=attempt.invocation_id,
            aggregation_identity="usage-1",
        )
        events.append(
            TraceEvent(
                context,
                "provider-usage",
                None,
                "provider.usage.reported",
                2.5,
                TraceSemanticRefs(agent_id=agent_id),
                {"attempt": attempt.to_dict(), "evidence": usage.to_dict()},
                ProviderCorrelation("test"),
            )
        )
    trace = NormalizedTrace(tuple(events))
    if not closed:
        return ir, plan, trace, None
    closure = TraceClosureEvidence(
        context,
        "complete",
        "The fixture is closed.",
        TraceFrontier.from_trace(trace),
        tuple(
            channel
            for channel, present in (
                ("agent", True),
                ("provider_outcome", include_outcome),
                ("provider_usage", include_usage),
            )
            if present
        ),
        (
            TraceAttemptClosure(
                attempt,
                agent_id,
                "complete",
                "complete",
                evidence_refs=("fixture:attempt",),
                outcome_status="complete" if include_outcome else None,
                usage_status="complete" if include_usage else None,
            ),
        ),
        ("fixture:closure",),
    )
    return ir, plan, trace, closure


def test_operational_assessor_supports_single_run_metrics_and_closure() -> None:
    requirements = (
        "trace.duration < 10s",
        "trace.provider_request_count >= 1",
        "trace.input_tokens == 10",
        "trace.output_tokens == 5",
        "trace.total_tokens == 15",
        "trace.failed_provider_call_count == 1",
        "trace.attempt_count == 1",
        "trace.retry_count == 0",
    )
    ir, plan, trace, closure = _operational_fixture(requirements)
    results = assess_operational_controls(ir, plan, trace, closure=closure)
    assert [item.status for item in results] == ["passed"] * len(requirements)
    assert all(item.actual is not None for item in results)


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [("trace.duration < 1s", "violated"), ("trace.total_tokens >= 1", "unverified")],
)
def test_operational_assessor_preserves_upper_bound_and_open_channel_semantics(requirement: str, expected: str) -> None:
    ir, plan, trace, closure = _operational_fixture((requirement,), closed=False)
    result = assess_operational_controls(ir, plan, trace, closure=closure)[0]
    assert result.status == expected


def test_operational_assessor_marks_missing_usage_and_windowed_controls_unverified() -> None:
    ir, plan, trace, closure = _operational_fixture(("trace.input_tokens == 10",), include_usage=False)
    result = assess_operational_controls(ir, plan, trace, closure=closure)[0]
    assert result.status == "unverified"
    control = next(iter(ir.operational_controls.values()))
    windowed = replace(control, window="last 7 days")
    windowed_ir = replace(ir, operational_controls=FrozenMap({windowed.id: windowed}))
    windowed_plan = replace(
        plan,
        contract_digest=contract_digest(windowed_ir),
        operational_controls=FrozenMap(
            {windowed.id: replace(next(iter(plan.operational_controls.values())), window="last 7 days")}
        ),
    )
    windowed_trace = NormalizedTrace(
        tuple(
            replace(
                event,
                context=replace(
                    event.context,
                    contract_digest=contract_digest(windowed_ir),
                    plan_digest=windowed_plan.plan_digest,
                ),
            )
            for event in trace.events
        )
    )
    assert (
        assess_operational_controls(windowed_ir, windowed_plan, windowed_trace, closure=None)[0].status == "unverified"
    )


def test_operational_assessor_handles_unsupported_grammar_and_bad_evidence() -> None:
    ir, plan, trace, closure = _operational_fixture(("trace.duration < 10s",))
    control_id = next(iter(plan.operational_controls))
    unsupported_plan = replace(
        plan,
        operational_controls=FrozenMap(
            {control_id: replace(plan.operational_controls[control_id], outcome="unsupported", mechanism=None)}
        ),
    )
    unsupported_trace = NormalizedTrace(
        tuple(
            replace(event, context=replace(event.context, plan_digest=unsupported_plan.plan_digest))
            for event in trace.events
        )
    )
    assert assess_operational_controls(ir, unsupported_plan, unsupported_trace)[0].status == "unverified"
    bad_ir, bad_plan, bad_trace, _ = _operational_fixture(("not an operational expression",))
    assert assess_operational_controls(bad_ir, bad_plan, bad_trace)[0].status == "unverified"

    attempt_ir, attempt_plan, attempt_source, _ = _operational_fixture(("trace.attempt_count == 1",))
    no_attempt_trace = NormalizedTrace(
        tuple(replace(event, data={}) for event in attempt_source.events if event.event_type != "attempt.selected")
    )
    assert assess_operational_controls(attempt_ir, attempt_plan, no_attempt_trace)[0].status == "unverified"

    usage_ir, usage_plan, usage_source, _ = _operational_fixture(("trace.input_tokens == 10",))
    usage_event = next(event for event in usage_source.events if event.event_type == "provider.usage.reported")
    malformed_usage = replace(usage_event, data={"attempt": usage_event.data["attempt"], "evidence": {}})
    malformed_trace = NormalizedTrace(
        tuple(malformed_usage if event.event_id == usage_event.event_id else event for event in usage_source.events)
    )
    assert assess_operational_controls(usage_ir, usage_plan, malformed_trace)[0].status == "unverified"

    duration_ir, duration_plan, duration_source, _ = _operational_fixture(("trace.duration == 2s",))
    other_agent = semantic_id("agent", "Other")
    no_duration_trace = NormalizedTrace(
        tuple(replace(event, semantic=TraceSemanticRefs(agent_id=other_agent)) for event in duration_source.events)
    )
    assert assess_operational_controls(duration_ir, duration_plan, no_duration_trace)[0].status == "unverified"

    outcome_ir, outcome_plan, outcome_source, _ = _operational_fixture(
        ("trace.failed_provider_call_count == 1",), include_usage=False
    )
    outcome_event = next(event for event in outcome_source.events if event.event_type == "provider.outcome.reported")
    bad_outcome = replace(outcome_event, data={"attempt": outcome_event.data["attempt"], "evidence": {}})
    bad_outcome_trace = NormalizedTrace(
        tuple(bad_outcome if event.event_id == outcome_event.event_id else event for event in outcome_source.events)
    )
    malformed_result = assess_operational_controls(outcome_ir, outcome_plan, bad_outcome_trace)[0]
    assert malformed_result.status == "unverified"
    assert malformed_result.actual is None


def test_provider_outcome_metric_ignores_malformed_unselected_attempt_evidence() -> None:
    ir, plan, trace, _ = _operational_fixture(("trace.failed_provider_call_count == 1",), include_usage=False)
    event = next(item for item in trace.events if item.event_type == "provider.outcome.reported")
    unselected_attempt = TraceAttempt("research:2", "research-attempt-2", 1)
    malformed_unselected = replace(
        event,
        event_id="provider-outcome-unselected",
        data={"attempt": unselected_attempt.to_dict(), "evidence": {}},
    )

    result = assess_operational_controls(
        ir,
        plan,
        NormalizedTrace(trace.events + (malformed_unselected,)),
    )[0]

    assert result.actual == 1
    assert result.status == "unverified"


def test_provider_outcome_metric_deduplicates_identical_valid_evidence() -> None:
    ir, plan, trace, closure = _operational_fixture(("trace.failed_provider_call_count == 1",), include_usage=False)
    assert closure is not None
    event = next(item for item in trace.events if item.event_type == "provider.outcome.reported")
    duplicate = replace(event, event_id="provider-outcome-duplicate")
    duplicated_trace = NormalizedTrace(trace.events + (duplicate,))
    duplicated_closure = replace(
        closure,
        frontier=TraceFrontier.from_trace(duplicated_trace),
    )

    result = assess_operational_controls(
        ir,
        plan,
        duplicated_trace,
        closure=duplicated_closure,
    )[0]

    assert result.status == "passed"
    assert result.actual == 1


def test_complete_provider_outcome_closure_rejects_malformed_evidence() -> None:
    ir, plan, trace, closure = _operational_fixture(("trace.failed_provider_call_count == 1",), include_usage=False)
    assert closure is not None
    event = next(item for item in trace.events if item.event_type == "provider.outcome.reported")
    invalid = replace(
        event,
        data={"attempt": event.data["attempt"], "evidence": {}},
    )
    invalid_trace = NormalizedTrace(
        tuple(invalid if item.event_id == event.event_id else item for item in trace.events)
    )
    invalid_closure = replace(
        closure,
        frontier=TraceFrontier.from_trace(invalid_trace),
    )

    with pytest.raises(TraceClosureError, match="malformed outcome evidence"):
        assess_operational_controls(ir, plan, invalid_trace, closure=invalid_closure)


def test_complete_provider_outcome_closure_keeps_inconclusive_metric_unverified() -> None:
    ir, plan, trace, closure = _operational_fixture(("trace.failed_provider_call_count == 1",), include_usage=False)
    assert closure is not None
    event = next(item for item in trace.events if item.event_type == "provider.outcome.reported")
    inconclusive_evidence = _outcome(
        agent_id=semantic_id("agent", "Researcher"),
        attempt_id="research-attempt-1",
        invocation_id="research:1",
        outcome="unknown",
        category="unknown",
        state="unverified",
        request_id="request-1",
    )
    inconclusive = replace(
        event,
        data={
            "attempt": event.data["attempt"],
            "evidence": inconclusive_evidence.to_dict(),
        },
    )
    inconclusive_trace = NormalizedTrace(
        tuple(inconclusive if item.event_id == event.event_id else item for item in trace.events)
    )
    inconclusive_closure = replace(
        closure,
        frontier=TraceFrontier.from_trace(inconclusive_trace),
    )

    result = assess_operational_controls(
        ir,
        plan,
        inconclusive_trace,
        closure=inconclusive_closure,
    )[0]

    assert result.status == "unverified"
    assert result.actual is None


def test_complete_provider_outcome_closure_rejects_missing_and_contradictory_evidence() -> None:
    ir, plan, trace, closure = _operational_fixture(("trace.failed_provider_call_count == 1",), include_usage=False)
    assert closure is not None
    event = next(item for item in trace.events if item.event_type == "provider.outcome.reported")
    without_outcome = NormalizedTrace(tuple(item for item in trace.events if item.event_id != event.event_id))
    missing_closure = replace(
        closure,
        frontier=TraceFrontier.from_trace(without_outcome),
    )
    with pytest.raises(TraceClosureError, match="has no outcome report"):
        assess_operational_controls(ir, plan, without_outcome, closure=missing_closure)

    evidence = ProviderOutcomeEvidence.from_dict(event.data["evidence"])
    contradictory = replace(
        event,
        event_id="provider-outcome-contradictory",
        data={
            "attempt": event.data["attempt"],
            "evidence": replace(
                evidence,
                outcome="succeeded",
                category="transport",
            ).to_dict(),
        },
    )
    contradictory_trace = NormalizedTrace(trace.events + (contradictory,))
    contradictory_closure = replace(
        closure,
        frontier=TraceFrontier.from_trace(contradictory_trace),
    )
    with pytest.raises(TraceClosureError, match="contradictory outcome evidence"):
        assess_operational_controls(
            ir,
            plan,
            contradictory_trace,
            closure=contradictory_closure,
        )
    result = assess_operational_controls(ir, plan, contradictory_trace)[0]
    assert result.status == "unverified"
    assert result.actual is None


def test_operational_assessor_rejects_ambiguous_multi_run_trace() -> None:
    ir, plan, trace, _ = _operational_fixture(("trace.duration < 10s",))
    second = NormalizedTrace(
        tuple(
            replace(
                event,
                context=replace(event.context, run_id="run-2"),
                event_id=f"run-2:{event.event_id}",
                parent_event_id=(f"run-2:{event.parent_event_id}" if event.parent_event_id is not None else None),
            )
            for event in trace.events
        )
    )
    with pytest.raises(ValueError, match="multiple runs"):
        assess_operational_controls(ir, plan, NormalizedTrace(trace.events + second.events))


def test_trace_closure_round_trips_provider_statuses() -> None:
    _, _, trace, closure = _operational_fixture(("trace.input_tokens == 10",))
    assert closure is not None
    assert closure.covers_provider_outcome()
    assert closure.covers_provider_usage()
    assert TraceClosureEvidence.from_json(json.dumps(closure.to_dict())) == closure


def test_trace_closure_manifest_and_validation_reject_bad_identity() -> None:
    _, _, trace, closure = _operational_fixture(("trace.input_tokens == 10",))
    assert closure is not None
    manifest = TraceClosureManifest((closure,))
    assert TraceClosureManifest.from_json(manifest.to_json()) == manifest
    with pytest.raises(ValueError, match="Invalid trace-closure JSON"):
        TraceClosureEvidence.from_json("not-json")
    with pytest.raises(ValueError, match="Unsupported trace-closure manifest version"):
        TraceClosureManifest.from_dict({"closures": [], "version": "2"})
    with pytest.raises(ValueError, match="Unsupported trace-closure manifest version"):
        TraceClosureManifest((), version="2")
    with pytest.raises(ValueError, match="Invalid trace-closure manifest JSON"):
        TraceClosureManifest.from_json("not-json")
    with pytest.raises(ValueError, match="run IDs must be unique"):
        TraceClosureManifest((closure, closure))
    from contract4agents.tracing import validate_trace_closure

    with pytest.raises(ValueError, match="run_id"):
        validate_trace_closure(trace, replace(closure, context=replace(closure.context, run_id="run-2")))
    with pytest.raises(ValueError, match="context"):
        validate_trace_closure(trace, replace(closure, context=replace(closure.context, thread_id="thread-2")))
