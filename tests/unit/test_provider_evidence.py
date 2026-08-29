from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contract4agents import compile_project, materialize
from contract4agents.assurance import (
    AssessorIdentity,
    OperationalControlResult,
    assess_operational_controls,
)
from contract4agents.eval_campaigns import TrialMetrics
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
    OpenAINormalizedTraceRouter,
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
    provider_outcome_event_data,
    provider_usage_event_data,
)
from contract4agents.tracing import _google_adk as google_adk_tracing
from contract4agents.tracing import _openai as openai_tracing
from contract4agents.tracing import _strands as strands_tracing

ROOT = Path(__file__).resolve().parents[2]


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


@pytest.mark.parametrize("state", ("observed", "inferred", "unavailable", "unverified"))
def test_outcome_states_are_exact_and_round_trip(state: str) -> None:
    value = _outcome(state=state)
    encoded = value.to_json()
    assert json.loads(encoded) == value.to_dict()
    assert ProviderOutcomeEvidence.from_dict(json.loads(encoded)) == value
    assert value.claim_state == state


def test_outcome_rejects_negative_and_content_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _outcome(http_status=-1)
    with pytest.raises(ValueError, match="unsupported content"):
        _outcome(provider_error_code="provider error")
    with pytest.raises(ValueError, match="failure category"):
        _outcome(category="provider_error")
    with pytest.raises(ValueError, match="unsupported content"):
        _outcome(response_id="response\nsecret")


def test_outcome_event_helper_has_only_evidence_payload() -> None:
    value = _outcome(response_id="response-1")
    assert provider_outcome_event_data(value) == {"evidence": value.to_dict()}
    assert "secret" not in json.dumps(provider_outcome_event_data(value))


def test_usage_zero_is_distinct_from_unavailable() -> None:
    zero = _usage(
        request_count=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        total_tokens=0,
    )
    unavailable = _usage(
        coverage="unavailable",
        request_count=None,
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        total_tokens=None,
    )
    assert zero.request_count == 0
    assert unavailable.request_count is None
    assert unavailable.to_dict()["input_tokens"] is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scope", "invalid", "Unsupported scope"),
        ("coverage", "invalid", "Unsupported coverage"),
        ("request_count", -1, "non-negative"),
        ("cached_input_tokens", 11, "cannot exceed"),
        ("reasoning_tokens", 6, "cannot exceed"),
        ("agent_id", 42, "semantic ID"),
    ],
)
def test_usage_rejects_invalid_fields(field: str, value: object, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _usage(**{field: value})


def test_usage_rejects_inconsistent_totals_and_complete_missing_values() -> None:
    with pytest.raises(ValueError, match="equal"):
        _usage(total_tokens=16)
    with pytest.raises(ValueError, match="requires input"):
        _usage(input_tokens=None)
    with pytest.raises(ValueError, match="unavailable"):
        _usage(
            coverage="unavailable",
            request_count=None,
            input_tokens=0,
            cached_input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("agent_id", 42, "semantic ID"),
        ("attempt_number", 0, "at least one"),
        ("http_status", 1000, "at most 999"),
        ("retry_after_seconds", True, "numeric"),
        ("retry_after_seconds", float("inf"), "finite"),
    ],
)
def test_outcome_rejects_invalid_structured_facts(field: str, value: object, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _outcome(**{field: value})


def test_outcome_rejects_refusal_and_cancellation_category_mismatches() -> None:
    with pytest.raises(ValueError, match="refused outcome"):
        _outcome(outcome="refused", category="provider_error")
    with pytest.raises(ValueError, match="cancelled outcome"):
        _outcome(outcome="cancelled", category="provider_error")


def test_evidence_deserializers_are_strict() -> None:
    with pytest.raises(TypeError, match="object"):
        ProviderOutcomeEvidence.from_dict([])
    with pytest.raises(ValueError, match="missing required fields"):
        ProviderOutcomeEvidence.from_dict({"agent_id": "agent:Researcher"})
    with pytest.raises(ValueError, match="unknown fields"):
        ProviderUsageEvidence.from_dict(
            {
                "scope": "run",
                "coverage": "unavailable",
                "aggregation_identity": "run-1",
                "aggregation_basis": "fixture",
                "provenance": "fixture",
                "unknown": True,
            }
        )
    with pytest.raises(TypeError, match="object"):
        ProviderUsageEvidence.from_dict([])
    with pytest.raises(ValueError, match="non-empty"):
        _outcome(classifier_provenance="")
    with pytest.raises(ValueError, match="non-empty"):
        _usage(aggregation_basis="")


def test_usage_round_trip_and_trial_metric_derivation_deduplicates_identity() -> None:
    value = _usage()
    encoded = json.loads(value.to_json())
    assert ProviderUsageEvidence.from_dict(encoded) == value
    assert provider_usage_event_data(value) == {"evidence": value.to_dict()}
    derived = TrialMetrics.from_provider_usage((value, value))
    assert derived.input_tokens == 10
    assert derived.output_tokens == 5
    conflicting = _usage(output_tokens=6, total_tokens=16)
    assert TrialMetrics.from_provider_usage((value, conflicting)).input_tokens is None


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
def test_operational_assessor_preserves_upper_bound_and_open_channel_semantics(
    requirement: str, expected: str
) -> None:
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
            {
                windowed.id: replace(next(iter(plan.operational_controls.values())), window="last 7 days")
            }
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
        assess_operational_controls(windowed_ir, windowed_plan, windowed_trace, closure=None)[0].status
        == "unverified"
    )


def test_operational_assessor_handles_unsupported_grammar_and_bad_evidence() -> None:
    ir, plan, trace, closure = _operational_fixture(("trace.duration < 10s",))
    control_id = next(iter(plan.operational_controls))
    unsupported_plan = replace(
        plan,
        operational_controls=FrozenMap(
            {
                control_id: replace(
                    plan.operational_controls[control_id], outcome="unsupported", mechanism=None
                )
            }
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
        tuple(
            replace(event, data={})
            for event in attempt_source.events
            if event.event_type != "attempt.selected"
        )
    )
    assert assess_operational_controls(attempt_ir, attempt_plan, no_attempt_trace)[0].status == "unverified"

    usage_ir, usage_plan, usage_source, _ = _operational_fixture(("trace.input_tokens == 10",))
    usage_event = next(event for event in usage_source.events if event.event_type == "provider.usage.reported")
    malformed_usage = replace(usage_event, data={"attempt": usage_event.data["attempt"], "evidence": {}})
    malformed_trace = NormalizedTrace(
        tuple(
            malformed_usage if event.event_id == usage_event.event_id else event
            for event in usage_source.events
        )
    )
    assert assess_operational_controls(usage_ir, usage_plan, malformed_trace)[0].status == "unverified"

    duration_ir, duration_plan, duration_source, _ = _operational_fixture(("trace.duration == 2s",))
    other_agent = semantic_id("agent", "Other")
    no_duration_trace = NormalizedTrace(
        tuple(
            replace(event, semantic=TraceSemanticRefs(agent_id=other_agent))
            for event in duration_source.events
        )
    )
    assert assess_operational_controls(duration_ir, duration_plan, no_duration_trace)[0].status == "unverified"

    outcome_ir, outcome_plan, outcome_source, _ = _operational_fixture(
        ("trace.failed_provider_call_count == 1",), include_usage=False
    )
    outcome_event = next(
        event for event in outcome_source.events if event.event_type == "provider.outcome.reported"
    )
    bad_outcome = replace(outcome_event, data={"attempt": outcome_event.data["attempt"], "evidence": {}})
    bad_outcome_trace = NormalizedTrace(
        tuple(bad_outcome if event.event_id == outcome_event.event_id else event for event in outcome_source.events)
    )
    malformed_result = assess_operational_controls(outcome_ir, outcome_plan, bad_outcome_trace)[0]
    assert malformed_result.status == "unverified"
    assert malformed_result.actual is None


def test_provider_outcome_metric_ignores_malformed_unselected_attempt_evidence() -> None:
    ir, plan, trace, _ = _operational_fixture(
        ("trace.failed_provider_call_count == 1",), include_usage=False
    )
    event = next(
        item for item in trace.events if item.event_type == "provider.outcome.reported"
    )
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
    ir, plan, trace, closure = _operational_fixture(
        ("trace.failed_provider_call_count == 1",), include_usage=False
    )
    assert closure is not None
    event = next(
        item for item in trace.events if item.event_type == "provider.outcome.reported"
    )
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
    ir, plan, trace, closure = _operational_fixture(
        ("trace.failed_provider_call_count == 1",), include_usage=False
    )
    assert closure is not None
    event = next(
        item for item in trace.events if item.event_type == "provider.outcome.reported"
    )
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
    ir, plan, trace, closure = _operational_fixture(
        ("trace.failed_provider_call_count == 1",), include_usage=False
    )
    assert closure is not None
    event = next(
        item for item in trace.events if item.event_type == "provider.outcome.reported"
    )
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
        tuple(
            inconclusive if item.event_id == event.event_id else item
            for item in trace.events
        )
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
    ir, plan, trace, closure = _operational_fixture(
        ("trace.failed_provider_call_count == 1",), include_usage=False
    )
    assert closure is not None
    event = next(
        item for item in trace.events if item.event_type == "provider.outcome.reported"
    )
    without_outcome = NormalizedTrace(
        tuple(item for item in trace.events if item.event_id != event.event_id)
    )
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
                parent_event_id=(
                    f"run-2:{event.parent_event_id}" if event.parent_event_id is not None else None
                ),
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


def test_session_provider_reports_bind_attempt_identity_and_close_channels() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(artifacts.ir, system.plan, run_id="provider-evidence")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    agent_id = semantic_id("agent", "IncidentCommander")
    outcome = ProviderOutcomeEvidence(
        agent_id=agent_id,
        attempt_id=attempt.attempt_id,
        phase="response",
        outcome="succeeded",
        category="transport",
        state="observed",
        classifier_provenance="fixture",
        response_id="response-1",
    )
    usage = ProviderUsageEvidence(
        scope="attempt",
        coverage="complete",
        aggregation_identity="attempt-1",
        aggregation_basis="fixture",
        provenance="fixture",
        request_count=1,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        agent_id=agent_id,
        attempt_id=attempt.attempt_id,
    )
    with session:
        with session.bind_attempt(attempt, agent="IncidentCommander"):
            session.record_provider_outcome(outcome, provider_identity="response-1")
            session.report_provider_outcome(outcome, provider_identity="response-1")
            session.record_provider_usage(usage, provider_identity="response-1")
            session.report_provider_usage(usage, provider_identity="response-1")
            with pytest.raises(ValueError, match="attempt_id"):
                session.record_provider_outcome(
                    replace(outcome, attempt_id="other-attempt"), provider_identity="response-1"
                )
            with pytest.raises(ValueError, match="agent"):
                session.record_provider_usage(
                    replace(usage, agent_id=semantic_id("agent", "Missing")), provider_identity="response-1"
                )
        unbound = TraceAttempt("commander:2", "commander-attempt-2", 1)
        with pytest.raises(ValueError, match="requires agent_id"):
            session.record_provider_usage(
                replace(usage, agent_id=None, attempt_id=None), attempt=unbound, provider_identity="response-2"
            )
        session.record_provider_usage(
            replace(usage, aggregation_identity="unbound", attempt_id=unbound.attempt_id),
            attempt=unbound,
            provider_identity="response-2",
        )
    assert {event.event_type for event in session.normalized_trace().events} >= {
        "provider.outcome.reported",
        "provider.usage.reported",
    }
    exception_session = router.open_session(artifacts.ir, system.plan, run_id="provider-exception")
    exception_attempt = TraceAttempt("commander:exception", "commander-exception-1", 1)
    with exception_session:
        with exception_session.bind_attempt(exception_attempt, agent="IncidentCommander"):
            assert exception_session.normalize_exception_responses(
                RuntimeError("secret exception text"),
                agent="IncidentCommander",
                attempt=exception_attempt,
            ) == ()
    assert all(
        "secret exception text" not in json.dumps(event.to_dict())
        for event in exception_session.normalized_trace().events
    )


def test_provider_extraction_helpers_use_structured_fields_only() -> None:
    attempt = TraceAttempt("research:1", "attempt-1", 1)
    agent_id = semantic_id("agent", "Researcher")
    complete_usage = SimpleNamespace(
        requests=2,
        input_tokens=10,
        input_tokens_details=SimpleNamespace(cached_tokens=2),
        output_tokens=5,
        output_tokens_details=SimpleNamespace(reasoning_tokens=1),
        total_tokens=15,
    )
    assert openai_tracing._openai_usage_evidence(
        SimpleNamespace(context_wrapper=SimpleNamespace(usage=complete_usage)),
        agent_id=agent_id,
        attempt=attempt,
    ).coverage == "complete"
    assert openai_tracing._openai_usage_evidence(
        SimpleNamespace(context_wrapper=SimpleNamespace(usage=SimpleNamespace(input_tokens=10))),
        agent_id=agent_id,
        attempt=attempt,
    ).coverage == "partial"
    assert openai_tracing._openai_usage_evidence(
        SimpleNamespace(context_wrapper=SimpleNamespace(usage=None)), agent_id=agent_id, attempt=attempt
    ).coverage == "unavailable"
    inconsistent = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=16)
    assert openai_tracing._openai_usage_evidence(
        SimpleNamespace(context_wrapper=SimpleNamespace(usage=inconsistent)), agent_id=agent_id, attempt=attempt
    ).coverage == "partial"
    assert openai_tracing._openai_usage_evidence(
        SimpleNamespace(
            context_wrapper=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    input_tokens_details=SimpleNamespace(cached_tokens=11),
                )
            )
        ),
        agent_id=agent_id,
        attempt=attempt,
    ).cached_input_tokens is None
    assert openai_tracing._openai_usage_evidence(
        SimpleNamespace(context_wrapper=SimpleNamespace(usage=SimpleNamespace(requests=0))),
        agent_id=agent_id,
        attempt=attempt,
    ).coverage == "partial"
    assert openai_tracing._classify_openai_exception(SimpleNamespace(status_code=401))[1] == "authentication"
    assert openai_tracing._classify_openai_exception(SimpleNamespace(status_code=403))[1] == "authorization"
    assert openai_tracing._classify_openai_exception(SimpleNamespace(status_code=429))[1] == "rate_limit"
    assert openai_tracing._classify_openai_exception(SimpleNamespace(status_code=500))[1] == "provider_error"
    assert openai_tracing._classify_openai_exception(asyncio.CancelledError())[3] == "unverified"
    from agents.exceptions import MCPToolCancellationError, ModelRefusalError, ModelTimeoutError

    assert openai_tracing._classify_openai_exception(ModelTimeoutError(1))[0] == "failed"
    assert openai_tracing._classify_openai_exception(ModelRefusalError("redacted"))[0] == "refused"
    assert openai_tracing._classify_openai_exception(MCPToolCancellationError("redacted"))[0] == "cancelled"
    assert openai_tracing._safe_code_attr(SimpleNamespace(code="safe-code"), "code") == "safe-code"
    assert openai_tracing._safe_code_attr(SimpleNamespace(code="unsafe code"), "code") is None
    assert openai_tracing._safe_code_attr(SimpleNamespace(code="x" * 129), "code") is None
    assert openai_tracing._safe_float_attr(SimpleNamespace(value=1), "value") == 1.0
    assert openai_tracing._safe_float_attr(SimpleNamespace(value=float("nan")), "value") is None
    assert openai_tracing._safe_float_attr(SimpleNamespace(value=True), "value") is None


def test_google_and_strands_usage_helpers_accept_public_shapes_and_reject_content() -> None:
    attempt = TraceAttempt("research:1", "attempt-1", 1)
    agent_id = semantic_id("agent", "Researcher")
    assert google_adk_tracing._adk_identity(SimpleNamespace(response_id="response-1")) == "response-1"
    assert google_adk_tracing._adk_refusal(SimpleNamespace(finish_reason="SAFETY"))
    assert google_adk_tracing._adk_refusal(SimpleNamespace(refused=True))
    assert google_adk_tracing._adk_safe_code(SimpleNamespace(error_code=429)) == "429"
    assert google_adk_tracing._adk_safe_code(SimpleNamespace(code="bad code")) is None
    assert google_adk_tracing._adk_status_code(SimpleNamespace(status_code=403)) == 403
    assert google_adk_tracing._adk_error_classification(SimpleNamespace(status_code=401)) == (
        "authentication",
        "inferred",
    )
    assert google_adk_tracing._adk_error_classification(SimpleNamespace(status_code=403)) == (
        "authorization",
        "inferred",
    )
    assert google_adk_tracing._adk_error_classification(SimpleNamespace(status_code=429)) == (
        "rate_limit",
        "inferred",
    )
    assert google_adk_tracing._adk_error_classification(SimpleNamespace(status_code=503)) == (
        "provider_error",
        "inferred",
    )
    assert google_adk_tracing._adk_error_classification(SimpleNamespace()) == ("unknown", "observed")
    adk_complete = SimpleNamespace(
        usage_metadata={
            "prompt_token_count": 10,
            "cached_content_token_count": 2,
            "candidates_token_count": 5,
            "thoughts_token_count": 1,
            "total_token_count": 15,
        }
    )
    assert (
        google_adk_tracing._adk_usage_evidence(
            adk_complete,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-1",
        ).coverage
        == "complete"
    )
    assert (
        google_adk_tracing._adk_usage_evidence(
            SimpleNamespace(),
            agent_id=agent_id,
            attempt=attempt,
            identity="response-2",
        ).coverage
        == "unavailable"
    )
    adk_partial = SimpleNamespace(
        usage_metadata={"prompt_token_count": 10, "candidates_token_count": 5, "total_token_count": 16}
    )
    assert (
        google_adk_tracing._adk_usage_evidence(
            adk_partial,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-5",
        ).coverage
        == "partial"
    )
    adk_invalid_categories = SimpleNamespace(
        usage_metadata={
            "prompt_token_count": 10,
            "cached_content_token_count": 11,
            "candidates_token_count": 5,
            "thoughts_token_count": 6,
            "total_token_count": 15,
        }
    )
    adk_invalid_result = google_adk_tracing._adk_usage_evidence(
        adk_invalid_categories,
        agent_id=agent_id,
        attempt=attempt,
        identity="response-6",
    )
    assert adk_invalid_result.coverage == "complete"
    assert adk_invalid_result.cached_input_tokens is None
    assert adk_invalid_result.reasoning_tokens is None
    assert google_adk_tracing._adk_usage_value(
        SimpleNamespace(input_tokens=3), "missing", "input_tokens"
    ) == 3
    strands_complete = SimpleNamespace(
        metrics=SimpleNamespace(
            accumulated_usage={
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 2,
            }
        )
    )
    assert (
        strands_tracing._strands_usage_evidence(
            strands_complete,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-3",
        ).coverage
        == "complete"
    )
    assert (
        strands_tracing._strands_usage_evidence(
            SimpleNamespace(),
            agent_id=agent_id,
            attempt=attempt,
            identity="response-4",
        ).coverage
        == "unavailable"
    )
    strands_partial = SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={"inputTokens": 10}))
    assert (
        strands_tracing._strands_usage_evidence(
            strands_partial,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-5",
        ).coverage
        == "partial"
    )
    strands_mismatch = SimpleNamespace(
        metrics=SimpleNamespace(
            accumulated_usage={"inputTokens": 10, "outputTokens": 5, "totalTokens": 16}
        )
    )
    assert (
        strands_tracing._strands_usage_evidence(
            strands_mismatch,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-6",
        ).coverage
        == "partial"
    )
    strands_cached = SimpleNamespace(
        metrics=SimpleNamespace(
            accumulated_usage={
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 11,
            }
        )
    )
    assert (
        strands_tracing._strands_usage_evidence(
            strands_cached,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-7",
        ).cached_input_tokens
        is None
    )
    assert strands_tracing._strands_usage_value({"inputTokens": True}, "inputTokens") is None
    assert [
        strands_tracing._strands_stop_outcome(value)[0]
        for value in ("cancelled", "content_filtered", "interrupt", "stop")
    ] == ["cancelled", "refused", "unknown", "succeeded"]
    assert strands_tracing._provider_identity({"request_id": "request-1"}) == "request-1"
    assert strands_tracing._tool_use_identity(SimpleNamespace(tool_use={"toolUseId": "tool-1"})) == "tool-1"
    assert strands_tracing._tool_name(SimpleNamespace(tool_use={"name": "search"})) == "search"
    assert strands_tracing._is_structured_output_tool(SimpleNamespace(tool_type="structured_output"))
