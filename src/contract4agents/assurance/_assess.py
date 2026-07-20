"""One control assessor shared by offline evals and production assessment."""

from __future__ import annotations

from typing import cast

from contract4agents.assurance._models import (
    AssessorIdentity,
    AssuranceStatus,
    ControlApplicability,
    ControlResult,
)
from contract4agents.expressions import ExpressionError, parse_expectation
from contract4agents.expressions._trace_evaluation import assess_trace_expression
from contract4agents.ir import CanonicalIR, ControlIR, SemanticId
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing import (
    NormalizedTrace,
    TraceAttempt,
    TraceClosureEvidence,
    TraceCompletenessResult,
    TraceEvent,
    assess_trace_completeness,
    validate_trace_conformance,
)

_ASSESSOR = AssessorIdentity("contract4agents", "1")


def assess_controls(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    trace: NormalizedTrace,
    *,
    closure: TraceClosureEvidence | None = None,
    run_id: str | None = None,
) -> tuple[ControlResult, ...]:
    """Assess every planned control without treating absent evidence as success."""

    selected = _select_run(trace, run_id)
    validate_trace_conformance(ir, plan, selected)
    completeness = assess_trace_completeness(
        selected,
        plan.expected_telemetry,
        closure=closure,
        run_id=selected.run_ids[0],
    )
    results = [
        _assess_control(control, ir, selected, completeness)
        for control_id in plan.controls
        if (control := ir.controls.get(control_id)) is not None
    ]
    return tuple(sorted(results, key=lambda item: item.control_id))


def _assess_control(
    control: ControlIR,
    ir: CanonicalIR,
    trace: NormalizedTrace,
    completeness: TraceCompletenessResult,
) -> ControlResult:
    relevant = tuple(event for event in trace.events if control.id in event.semantic.control_ids)
    condition_events: tuple[TraceEvent, ...] = ()
    if control.condition is not None:
        condition = _evaluate_control_expression(control.condition, ir, trace, completeness)
        condition_events = condition[1]
        if condition[0] == "violated":
            return _result(
                control,
                "passed",
                "The control condition was proven false, so the requirement did not apply.",
                condition_events,
                applicability="not_applicable",
            )
        if condition[0] == "unverified":
            return _result(
                control,
                "unverified",
                "The control condition could not be established from complete evidence.",
                condition_events,
                applicability="unverified",
            )
    if control.derived_from is not None and control.derived_from.kind == "grant":
        grant = ir.grants.get(control.derived_from)
        if grant is not None and grant.authorization == "approval_required":
            return _assess_approval(control, grant.id, grant.capability_id, trace, completeness)
    if control.name == "output_conformance":
        return _assess_output(control, trace, completeness)
    explicit = _explicit_result(relevant)
    if explicit is not None:
        status, reason = explicit
        return _result(control, status, reason, condition_events + relevant)
    if control.requirement:
        status, evidence = _evaluate_control_expression(control.requirement, ir, trace, completeness)
        return _result(
            control,
            status,
            {
                "passed": "The trace satisfies the declared requirement.",
                "violated": "The trace violates the declared requirement.",
                "unverified": "The declared requirement cannot be established from complete evidence.",
            }[status],
            condition_events + evidence,
        )
    return _result(control, "unverified", "No assessable requirement or evidence was available.", relevant)


def _assess_approval(
    control: ControlIR,
    grant_id: SemanticId,
    capability_id: SemanticId,
    trace: NormalizedTrace,
    completeness: TraceCompletenessResult,
) -> ControlResult:
    events = tuple(
        event
        for event in trace.events
        if event.semantic.grant_id == grant_id or event.semantic.capability_id == capability_id
    )
    starts = [event for event in events if event.event_type == "tool.started"]
    approvals = [
        event
        for event in events
        if event.event_type == "approval.completed" and event.data.get("approved") is True
    ]
    if starts and not approvals:
        return _result(control, "violated", "The capability started without recorded approval.", events)
    if starts and min(event.timestamp for event in approvals) >= min(event.timestamp for event in starts):
        return _result(control, "violated", "Approval was not recorded before the capability started.", events)
    if starts and approvals:
        return _result(control, "passed", "Approval was granted before the capability started.", events)
    if completeness.complete_for("tool"):
        return _result(control, "passed", "The approval-gated capability was not invoked.", events)
    return _result(
        control,
        "unverified",
        "No capability invocation was observed and trace completeness is insufficient for a negative claim.",
        events,
    )


def _assess_output(
    control: ControlIR,
    trace: NormalizedTrace,
    completeness: TraceCompletenessResult,
) -> ControlResult:
    events = tuple(
        event
        for event in trace.events
        if event.semantic.agent_id == control.agent_id
        and event.event_type in {"output.accepted", "output.schema_failed"}
    )
    output_invocations = {
        TraceAttempt.from_dict(event.data["attempt"]).invocation_id
        for event in events
        if event.data.get("attempt") is not None
    }
    terminal = tuple(
        event
        for event in trace.events
        if event.event_type == "attempt.selected"
        and (
            event.semantic.agent_id == control.agent_id
            or TraceAttempt.from_dict(event.data.get("attempt")).invocation_id
            in output_invocations
        )
    )
    if output_invocations and not terminal:
        return _result(
            control,
            "unverified",
            "Attempt-scoped output evidence requires an explicit terminal-attempt selection.",
            events,
        )
    if terminal:
        evidence = events + terminal
        unscoped = tuple(event for event in events if event.data.get("attempt") is None)
        if unscoped:
            return _result(
                control,
                "unverified",
                "Output evidence without attempt identity cannot be attributed to the selected terminal attempt.",
                evidence,
            )
        selections: dict[str, list[tuple[TraceAttempt, TraceEvent]]] = {}
        for event in terminal:
            attempt = TraceAttempt.from_dict(event.data.get("attempt"))
            selections.setdefault(attempt.invocation_id, []).append((attempt, event))
        if any(len(items) != 1 for items in selections.values()):
            return _result(
                control,
                "unverified",
                "Each invocation must select exactly one terminal attempt.",
                evidence,
            )
        attributed = tuple(
            (TraceAttempt.from_dict(event.data["attempt"]), event) for event in events
        )
        observed_invocations = {attempt.invocation_id for attempt, _ in attributed}
        if not observed_invocations.issubset(selections):
            return _result(
                control,
                "unverified",
                "Every invocation with output evidence must select a terminal attempt.",
                evidence,
            )
        assessed_evidence: list[TraceEvent] = []
        for invocation_id, items in selections.items():
            selected, selection_event = items[0]
            selected_events = tuple(
                event
                for attempt, event in attributed
                if attempt.invocation_id == invocation_id
                and attempt.attempt_id == selected.attempt_id
            )
            selected_evidence = selected_events + (selection_event,)
            assessed_evidence.extend(selected_evidence)
            outcome = selection_event.data.get("outcome")
            if outcome not in {"succeeded", "failed"}:
                return _result(
                    control,
                    "unverified",
                    "A selected terminal attempt has no valid outcome.",
                    selected_evidence,
                )
            if any(event.event_type == "output.schema_failed" for event in selected_events):
                return _result(
                    control,
                    "violated",
                    "Output schema validation failed for a selected terminal attempt.",
                    selected_evidence,
                )
            if outcome == "failed":
                return _result(
                    control,
                    "unverified",
                    "A selected terminal attempt failed without output-schema evidence.",
                    selected_evidence,
                )
            if not any(event.event_type == "output.accepted" for event in selected_events):
                return _result(
                    control,
                    "unverified",
                    "A selected terminal attempt has no output validation evidence.",
                    selected_evidence,
                )
        return _result(
            control,
            "passed",
            "Every selected terminal attempt matched the canonical output schema.",
            tuple(assessed_evidence),
        )
    if any(event.event_type == "output.schema_failed" for event in events):
        return _result(control, "violated", "Output schema validation failed.", events)
    if any(event.event_type == "output.accepted" for event in events):
        return _result(control, "passed", "Output matched the canonical schema.", events)
    invoked = any(
        event.semantic.agent_id == control.agent_id
        and event.event_type in {"agent.started", "agent.completed", "composition.started", "composition.completed"}
        for event in trace.events
    )
    if not invoked and completeness.complete_for("agent"):
        return _result(control, "passed", "The agent was not invoked during this complete run.", events)
    reason = (
        "No output validation event was observed despite otherwise complete telemetry."
        if completeness.complete_for("output")
        else "Output validation evidence is missing from an incomplete trace."
    )
    return _result(control, "unverified", reason, events)


def _explicit_result(events: tuple[TraceEvent, ...]) -> tuple[AssuranceStatus, str] | None:
    for event in events:
        if event.event_type != "control.assessed":
            continue
        status = event.data.get("status")
        reason = event.data.get("reason")
        if status in {"passed", "violated", "unverified"} and isinstance(reason, str):
            return cast(AssuranceStatus, status), reason
    return None


def _evaluate_control_expression(
    expression: str,
    ir: CanonicalIR,
    trace: NormalizedTrace,
    completeness: TraceCompletenessResult,
) -> tuple[AssuranceStatus, tuple[TraceEvent, ...]]:
    clauses = tuple(item.strip() for item in expression.split(" and ") if item.strip())
    try:
        parsed = tuple(parse_expectation(clause) for clause in clauses)
    except ExpressionError:
        return "unverified", ()
    if not parsed or any(item.kind != "trace" for item in parsed):
        return "unverified", ()
    results = tuple(
        assess_trace_expression(item, ir=ir, trace=trace, completeness=completeness)
        for item in parsed
    )
    evidence = {
        event.event_id: event
        for result in results
        for event in result.events
    }
    status: AssuranceStatus
    if any(result.status == "violated" for result in results):
        status = "violated"
    elif any(result.status == "unverified" for result in results):
        status = "unverified"
    else:
        status = "passed"
    return status, tuple(evidence[event_id] for event_id in sorted(evidence))


def _result(
    control: ControlIR,
    status: AssuranceStatus,
    reason: str,
    events: tuple[TraceEvent, ...],
    *,
    applicability: ControlApplicability = "applicable",
) -> ControlResult:
    return ControlResult(
        control_id=str(control.id),
        status=status,
        reason=reason,
        assessment=control.assessment,
        assessor=_ASSESSOR,
        applicability=applicability,
        evidence_event_ids=tuple(event.event_id for event in events),
        evidence_refs=tuple(reference for event in events for reference in event.evidence_refs),
    )


def _select_run(trace: NormalizedTrace, run_id: str | None) -> NormalizedTrace:
    if run_id is not None:
        return trace.for_run(run_id)
    if len(trace.run_ids) != 1:
        raise ValueError("Trace contains multiple runs; pass run_id explicitly")
    return trace


__all__ = ["assess_controls"]
