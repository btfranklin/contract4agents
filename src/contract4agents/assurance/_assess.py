"""One control assessor shared by offline evals and production assessment."""

from __future__ import annotations

import re
from typing import cast

from contract4agents.assurance._models import AssessorIdentity, AssuranceStatus, ControlResult
from contract4agents.ir import CanonicalIR, ControlIR, SemanticId
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing import NormalizedTrace, TraceEvent, assess_trace_completeness

_ASSESSOR = AssessorIdentity("contract4agents", "1")
_CALL = re.compile(r"trace\.(?P<operation>[a-z_]+)\((?P<arguments>[^)]*)\)\Z")


def assess_controls(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    trace: NormalizedTrace,
    *,
    run_id: str | None = None,
) -> tuple[ControlResult, ...]:
    """Assess every planned control without treating absent evidence as success."""

    selected = _select_run(trace, run_id)
    completeness = assess_trace_completeness(
        selected,
        plan.expected_telemetry,
        run_id=selected.run_ids[0],
    )
    results = [
        _assess_control(control, ir, selected, completeness.complete)
        for control_id in plan.controls
        if (control := ir.controls.get(control_id)) is not None
    ]
    return tuple(sorted(results, key=lambda item: item.control_id))


def _assess_control(
    control: ControlIR,
    ir: CanonicalIR,
    trace: NormalizedTrace,
    trace_complete: bool,
) -> ControlResult:
    relevant = tuple(event for event in trace.events if control.id in event.semantic.control_ids)
    if control.derived_from is not None and control.derived_from.kind == "grant":
        grant = ir.grants.get(control.derived_from)
        if grant is not None and grant.authorization == "approval_required":
            return _assess_approval(control, grant.id, grant.capability_id, trace, trace_complete)
    if control.name == "output_conformance":
        return _assess_output(control, trace, trace_complete)
    explicit = _explicit_result(relevant)
    if explicit is not None:
        status, reason = explicit
        return _result(control, status, reason, relevant)
    if control.requirement:
        evaluated = _evaluate_requirement(control.requirement, ir, trace)
        if evaluated is None:
            return _result(
                control,
                "unverified",
                "The control requirement is not supported by the deterministic assessor.",
                relevant,
            )
        if not trace_complete:
            return _result(
                control,
                "unverified",
                "Expected telemetry is incomplete, so the control result cannot be proven.",
                relevant,
            )
        passed, evidence = evaluated
        return _result(
            control,
            "passed" if passed else "violated",
            "The trace satisfies the declared requirement."
            if passed
            else "The trace violates the declared requirement.",
            evidence,
        )
    return _result(control, "unverified", "No assessable requirement or evidence was available.", relevant)


def _assess_approval(
    control: ControlIR,
    grant_id: SemanticId,
    capability_id: SemanticId,
    trace: NormalizedTrace,
    trace_complete: bool,
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
    if trace_complete:
        return _result(control, "passed", "The approval-gated capability was not invoked.", events)
    return _result(
        control,
        "unverified",
        "No capability invocation was observed and trace completeness is insufficient for a negative claim.",
        events,
    )


def _assess_output(control: ControlIR, trace: NormalizedTrace, trace_complete: bool) -> ControlResult:
    events = tuple(
        event
        for event in trace.events
        if event.semantic.agent_id == control.agent_id
        and event.event_type in {"output.accepted", "output.schema_failed"}
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
    if not invoked and trace_complete:
        return _result(control, "passed", "The agent was not invoked during this complete run.", events)
    reason = (
        "No output validation event was observed despite otherwise complete telemetry."
        if trace_complete
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


def _evaluate_requirement(
    requirement: str,
    ir: CanonicalIR,
    trace: NormalizedTrace,
) -> tuple[bool, tuple[TraceEvent, ...]] | None:
    clauses = tuple(item.strip() for item in requirement.split(" and "))
    if not clauses:
        return None
    all_evidence: list[TraceEvent] = []
    passed = True
    for clause in clauses:
        evaluated = _evaluate_clause(clause, ir, trace)
        if evaluated is None:
            return None
        clause_passed, evidence = evaluated
        passed = passed and clause_passed
        all_evidence.extend(evidence)
    unique = {event.event_id: event for event in all_evidence}
    return passed, tuple(unique[name] for name in sorted(unique))


def _evaluate_clause(
    clause: str,
    ir: CanonicalIR,
    trace: NormalizedTrace,
) -> tuple[bool, tuple[TraceEvent, ...]] | None:
    match = _CALL.fullmatch(clause)
    if match is None:
        return None
    operation = match.group("operation")
    arguments = tuple(_unquote(item.strip()) for item in match.group("arguments").split(",") if item.strip())
    if operation == "agent_called" and len(arguments) == 1:
        events = _agent_events(ir, trace, arguments[0])
        return bool(events), events
    if operation == "tool_called" and len(arguments) == 1:
        events = _capability_events(ir, trace, arguments[0])
        return bool(events), events
    if operation == "not_called" and len(arguments) == 1:
        events = _agent_events(ir, trace, arguments[0]) + _capability_events(ir, trace, arguments[0])
        return not events, events
    if operation == "called_before" and len(arguments) == 2:
        left = _agent_events(ir, trace, arguments[0])
        right = _agent_events(ir, trace, arguments[1])
        evidence = left + right
        ordered = bool(
            left
            and right
            and min(item.timestamp for item in left) < min(item.timestamp for item in right)
        )
        return ordered, evidence
    if operation == "approval_granted" and len(arguments) == 1:
        events = tuple(
            event
            for event in _capability_events(ir, trace, arguments[0], include_all=True)
            if event.event_type == "approval.completed" and event.data.get("approved") is True
        )
        return bool(events), events
    return None


def _agent_events(ir: CanonicalIR, trace: NormalizedTrace, name: str) -> tuple[TraceEvent, ...]:
    identifier = next((agent.id for agent in ir.agents.values() if agent.name == name), None)
    if identifier is None:
        return ()
    return tuple(
        event
        for event in trace.events
        if event.semantic.agent_id == identifier
        and event.event_type
        in {"agent.started", "agent.completed", "composition.started", "composition.completed", "handoff.completed"}
    )


def _capability_events(
    ir: CanonicalIR,
    trace: NormalizedTrace,
    name: str,
    *,
    include_all: bool = False,
) -> tuple[TraceEvent, ...]:
    identifier = next((item.id for item in ir.capabilities.values() if item.name == name), None)
    if identifier is None:
        return ()
    return tuple(
        event
        for event in trace.events
        if event.semantic.capability_id == identifier
        and (include_all or event.event_type in {"tool.started", "tool.completed", "datasource.resolved"})
    )


def _result(
    control: ControlIR,
    status: AssuranceStatus,
    reason: str,
    events: tuple[TraceEvent, ...],
) -> ControlResult:
    return ControlResult(
        control_id=str(control.id),
        status=status,
        reason=reason,
        assessment=control.assessment,
        assessor=_ASSESSOR,
        evidence_event_ids=tuple(event.event_id for event in events),
        evidence_refs=tuple(reference for event in events for reference in event.evidence_refs),
    )


def _select_run(trace: NormalizedTrace, run_id: str | None) -> NormalizedTrace:
    if run_id is not None:
        return trace.for_run(run_id)
    if len(trace.run_ids) != 1:
        raise ValueError("Trace contains multiple runs; pass run_id explicitly")
    return trace


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


__all__ = ["assess_controls"]
