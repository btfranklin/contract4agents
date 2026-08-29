"""Single-run operational-control assessment from normalized provider evidence."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from contract4agents.assurance._models import AssessorIdentity, AssuranceStatus
from contract4agents.expressions import ExpressionError, parse_operational_requirement
from contract4agents.ir import CanonicalIR, SemanticId
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing import (
    NormalizedTrace,
    ProviderOutcomeEvidence,
    ProviderUsageEvidence,
    TraceAttempt,
    TraceClosureEvidence,
    TraceEvidenceAssessment,
    assess_trace_evidence,
    validate_trace_conformance,
)

_ASSESSOR = AssessorIdentity("contract4agents", "1")
_METRICS = frozenset(
    {
        "duration",
        "provider_request_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "failed_provider_call_count",
        "attempt_count",
        "retry_count",
    }
)
_OPERATORS = frozenset({"<", "<=", ">", ">=", "==", "!="})


@dataclass(frozen=True)
class OperationalControlResult:
    """One three-valued assessment of a declared operational control."""

    operational_control_id: str
    status: AssuranceStatus
    reason: str
    assessment: str = "post_run"
    assessor: AssessorIdentity = _ASSESSOR
    metric: str | None = None
    actual: float | int | None = None
    target: float | int | None = None
    operator: str | None = None
    evidence_event_ids: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.operational_control_id, str) or not self.operational_control_id.strip():
            raise ValueError("operational_control_id must be a non-empty string")
        if self.status not in {"passed", "violated", "unverified"}:
            raise ValueError(f"Unsupported assurance status `{self.status}`")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Operational-control reason must be a non-empty string")
        if self.assessment not in {"post_run", "runtime", "advisory"}:
            raise ValueError(f"Unsupported operational assessment `{self.assessment}`")
        if self.metric is not None and self.metric not in _METRICS:
            raise ValueError(f"Unsupported operational metric `{self.metric}`")
        if self.operator is not None and self.operator not in _OPERATORS:
            raise ValueError(f"Unsupported operational operator `{self.operator}`")
        for name in ("actual", "target"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int | float):
                    raise TypeError(f"Operational {name} must be numeric")
                if value < 0 or not math.isfinite(value):
                    raise ValueError(f"Operational {name} must be finite and non-negative")
        object.__setattr__(self, "evidence_event_ids", _refs(self.evidence_event_ids, "evidence event ID"))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence reference"))

    @property
    def control_id(self) -> str:
        return self.operational_control_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "actual": self.actual,
            "assessment": self.assessment,
            "assessor": self.assessor.to_dict(),
            "evidence_event_ids": list(self.evidence_event_ids),
            "evidence_refs": list(self.evidence_refs),
            "metric": self.metric,
            "operational_control_id": self.operational_control_id,
            "operator": self.operator,
            "reason": self.reason,
            "status": self.status,
            "target": self.target,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)

    @classmethod
    def from_dict(cls, value: object) -> OperationalControlResult:
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise TypeError("Operational-control result must be an object")
        required = {
            "actual",
            "assessment",
            "assessor",
            "evidence_event_ids",
            "evidence_refs",
            "metric",
            "operational_control_id",
            "operator",
            "reason",
            "status",
            "target",
        }
        unknown = sorted(set(value) - required)
        missing = sorted(required - set(value))
        if missing or unknown:
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if unknown:
                detail.append(f"unknown {', '.join(unknown)}")
            raise ValueError("Invalid operational-control result: " + "; ".join(detail))
        assessor = value["assessor"]
        if not isinstance(assessor, dict) or set(assessor) != {"name", "version"}:
            raise ValueError("Operational-control result assessor must contain name and version")
        event_ids = value["evidence_event_ids"]
        refs = value["evidence_refs"]
        if not isinstance(event_ids, list) or not isinstance(refs, list):
            raise TypeError("Operational-control result evidence references must be arrays")
        return cls(
            operational_control_id=value["operational_control_id"],
            status=value["status"],
            reason=value["reason"],
            assessment=value["assessment"],
            assessor=AssessorIdentity(assessor["name"], assessor["version"]),
            metric=value["metric"],
            actual=value["actual"],
            target=value["target"],
            operator=value["operator"],
            evidence_event_ids=tuple(event_ids),
            evidence_refs=tuple(refs),
        )

    @classmethod
    def from_json(cls, source: str) -> OperationalControlResult:
        try:
            value = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid operational-control result JSON: {exc}") from exc
        return cls.from_dict(value)


def assess_operational_controls(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    trace: NormalizedTrace,
    *,
    closure: TraceClosureEvidence | None = None,
    run_id: str | None = None,
) -> tuple[OperationalControlResult, ...]:
    """Assess the supported single-run operational controls for one run."""

    selected = _select_run(trace, run_id)
    validate_trace_conformance(ir, plan, selected)
    trace_evidence = assess_trace_evidence(
        selected,
        plan.expected_event_types,
        closure=closure,
        run_id=selected.run_ids[0],
    )
    results: list[OperationalControlResult] = []
    for control_id, control in ir.operational_controls.items():
        mapping = plan.operational_controls.get(control_id)
        results.append(
            _assess_one(
                str(control_id),
                control.agent_id,
                control.requirement,
                control.window,
                mapping.outcome if mapping is not None else "unsupported",
                selected,
                trace_evidence,
                closure,
            )
        )
    return tuple(sorted(results, key=lambda item: item.operational_control_id))


def _assess_one(
    control_id: str,
    agent_id: SemanticId,
    requirement: str,
    window: str | None,
    mapping_outcome: str,
    trace: NormalizedTrace,
    trace_evidence: TraceEvidenceAssessment,
    closure: TraceClosureEvidence | None,
) -> OperationalControlResult:
    if window is not None or mapping_outcome in {"unsupported", "degraded"}:
        return _result(
            control_id,
            "unverified",
            "This operational control is unsupported without a single-run telemetry mapping.",
        )
    try:
        parsed = parse_operational_requirement(requirement)
    except ExpressionError:
        return _result(
            control_id,
            "unverified",
            "The operational expression is outside the supported single-run grammar.",
        )
    selected_attempts, selection_events, all_attempts = _attempt_scope(trace, agent_id)
    evidence_events: tuple[object, ...] = ()
    actual: float | int | None
    required_channel: str
    if parsed.metric == "duration":
        events = tuple(
            event
            for event in trace.events
            if event.semantic.agent_id == agent_id
            and event.event_type in {"agent.started", "agent.completed", "agent.failed"}
        )
        actual = _duration(events, selected_attempts)
        evidence_events = events
        required_channel = "agent"
    elif parsed.metric in {"provider_request_count", "input_tokens", "output_tokens", "total_tokens"}:
        events = tuple(
            event
            for event in trace.events
            if event.event_type == "provider.usage.reported"
            and event.semantic.agent_id == agent_id
        )
        usage = _usage(events, selected_attempts)
        if usage is None:
            return _result(
                control_id,
                "unverified",
                "Provider usage evidence is missing, partial, unavailable, or contradictory.",
                events=events,
                metric=parsed.metric,
                target=parsed.target,
                operator=parsed.operator,
            )
        actual = {
            "provider_request_count": usage["request_count"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
        }[parsed.metric]
        evidence_events = events
        required_channel = "provider_usage"
    elif parsed.metric == "failed_provider_call_count":
        events = tuple(
            event
            for event in trace.events
            if event.event_type == "provider.outcome.reported"
            and event.semantic.agent_id == agent_id
        )
        actual = _failed_provider_calls(events, selected_attempts)
        if actual is None:
            return _result(
                control_id,
                "unverified",
                "Provider outcome evidence is malformed, unavailable, inconclusive, or contradictory.",
                events=events,
                metric=parsed.metric,
                target=parsed.target,
                operator=parsed.operator,
            )
        evidence_events = events
        required_channel = "provider_outcome"
    else:
        events = tuple(
            event
            for event in trace.events
            if event.data.get("attempt") is not None
            and event.semantic.agent_id == agent_id
        )
        attempts = selected_attempts or all_attempts
        if not attempts:
            return _result(
                control_id,
                "unverified",
                "Attempt evidence is missing.",
                events=events,
                metric=parsed.metric,
                target=parsed.target,
                operator=parsed.operator,
            )
        actual = (
            len(attempts)
            if parsed.metric == "attempt_count"
            else sum(max(item.number - 1, 0) for item in attempts)
        )
        evidence_events = events + selection_events
        required_channel = "agent"
    if actual is None:
        return _result(
            control_id,
            "unverified",
            "The required metric is unavailable in provider evidence.",
            events=evidence_events,
            metric=parsed.metric,
            target=parsed.target,
            operator=parsed.operator,
        )
    satisfied = _compare(actual, parsed.operator, parsed.target)
    closed = _channel_closed(closure, required_channel)
    # A directly observed upper-bound breach is conclusive even if a later
    # callback was lost.  Passing and lower-bound claims require closure.
    upper_bound = parsed.operator in {"<", "<="}
    if not satisfied:
        status: AssuranceStatus = "violated" if (upper_bound or closed) else "unverified"
    else:
        status = "passed" if closed else "unverified"
    reason = (
        f"Observed {parsed.metric}={actual}; required {parsed.operator} {parsed.target}."
        if status != "unverified"
        else "The metric was observed, but closure is insufficient to prove the declared claim."
    )
    return _result(
        control_id,
        status,
        reason,
        events=evidence_events,
        metric=parsed.metric,
        actual=actual,
        target=parsed.target,
        operator=parsed.operator,
    )


def _usage(events: tuple[Any, ...], selected_attempts: tuple[TraceAttempt, ...]) -> dict[str, int | None] | None:
    selected_ids = {item.attempt_id for item in selected_attempts}
    aggregates: dict[str, ProviderUsageEvidence] = {}
    for event in events:
        if selected_ids and not _event_attempt_matches(event, selected_ids):
            continue
        payload = event.data.get("evidence")
        try:
            evidence = ProviderUsageEvidence.from_dict(payload)
        except (TypeError, ValueError):
            return None
        existing = aggregates.get(evidence.aggregation_identity)
        if existing is not None and existing != evidence:
            return None
        aggregates[evidence.aggregation_identity] = evidence
    if not aggregates or any(item.coverage != "complete" for item in aggregates.values()):
        return None
    values = {
        name: (
            sum(getattr(item, name) or 0 for item in aggregates.values())
            if all(getattr(item, name) is not None for item in aggregates.values())
            else None
        )
        for name in ("request_count", "input_tokens", "output_tokens", "total_tokens")
    }
    return values


def _failed_provider_calls(
    events: tuple[Any, ...], selected_attempts: tuple[TraceAttempt, ...]
) -> int | None:
    selected_ids = {item.attempt_id for item in selected_attempts}
    outcomes: dict[tuple[str | None, str | None, str], ProviderOutcomeEvidence] = {}
    for event in events:
        try:
            attempt = TraceAttempt.from_dict(event.data.get("attempt"))
        except (TypeError, ValueError):
            return None
        if selected_ids and attempt.attempt_id not in selected_ids:
            continue
        payload = event.data.get("evidence")
        try:
            evidence = ProviderOutcomeEvidence.from_dict(payload)
        except (TypeError, ValueError):
            return None
        if (
            evidence.agent_id != event.semantic.agent_id
            or evidence.attempt_id != attempt.attempt_id
            or (
                evidence.invocation_id is not None
                and evidence.invocation_id != attempt.invocation_id
            )
            or (
                evidence.attempt_number is not None
                and evidence.attempt_number != attempt.number
            )
            or not evidence.conclusive
        ):
            return None
        existing = outcomes.get(evidence.outcome_identity)
        if existing is not None and existing != evidence:
            return None
        outcomes[evidence.outcome_identity] = evidence
    return sum(
        evidence.outcome in {"failed", "refused"}
        for evidence in outcomes.values()
    )


def _attempt_scope(
    trace: NormalizedTrace,
    agent_id: SemanticId,
) -> tuple[tuple[TraceAttempt, ...], tuple[Any, ...], tuple[TraceAttempt, ...]]:
    selections = tuple(
        event
        for event in trace.events
        if event.event_type == "attempt.selected" and event.semantic.agent_id == agent_id
    )
    selected: list[TraceAttempt] = []
    for event in selections:
        try:
            selected.append(TraceAttempt.from_dict(event.data.get("attempt")))
        except (TypeError, ValueError):
            continue
    attempts: dict[str, TraceAttempt] = {}
    for event in trace.events:
        if event.semantic.agent_id != agent_id:
            continue
        value = event.data.get("attempt")
        if value is None:
            continue
        try:
            attempt = TraceAttempt.from_dict(value)
        except (TypeError, ValueError):
            continue
        attempts[attempt.attempt_id] = attempt
    selected_ids = {item.attempt_id for item in selected}
    selected_observed = tuple(item for item in attempts.values() if item.attempt_id in selected_ids)
    return tuple(sorted(selected_observed)), selections, tuple(sorted(attempts.values()))


def _event_attempt_matches(event: Any, attempt_ids: set[str]) -> bool:
    value = event.data.get("attempt")
    if value is None:
        return False
    try:
        return TraceAttempt.from_dict(value).attempt_id in attempt_ids
    except (TypeError, ValueError):
        return False


def _duration(events: tuple[Any, ...], selected_attempts: tuple[TraceAttempt, ...]) -> float | None:
    chosen = events
    if selected_attempts:
        ids = {item.attempt_id for item in selected_attempts}
        chosen = tuple(event for event in events if _event_attempt_matches(event, ids))
    if not chosen:
        return None
    timestamps = [float(event.timestamp) for event in chosen]
    return max(timestamps) - min(timestamps)


def _channel_closed(closure: TraceClosureEvidence | None, channel: str) -> bool:
    return closure is not None and closure.covers(channel)  # type: ignore[arg-type]


def _compare(actual: float | int, operator: str, target: float) -> bool:
    return {
        "<": actual < target,
        "<=": actual <= target,
        ">": actual > target,
        ">=": actual >= target,
        "==": actual == target,
        "!=": actual != target,
    }[operator]


def _result(
    control_id: str,
    status: AssuranceStatus,
    reason: str,
    *,
    events: tuple[Any, ...] = (),
    metric: str | None = None,
    actual: float | int | None = None,
    target: float | int | None = None,
    operator: str | None = None,
) -> OperationalControlResult:
    return OperationalControlResult(
        operational_control_id=control_id,
        status=status,
        reason=reason,
        metric=metric,
        actual=actual,
        target=target,
        operator=operator,
        evidence_event_ids=tuple(event.event_id for event in events),
        evidence_refs=tuple(reference for event in events for reference in event.evidence_refs),
    )


def _refs(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        normalized.add(value)
    return tuple(sorted(normalized))


def _select_run(trace: NormalizedTrace, run_id: str | None) -> NormalizedTrace:
    if run_id is not None:
        return trace.for_run(run_id)
    if len(trace.run_ids) != 1:
        raise ValueError("Trace contains multiple runs; pass run_id explicitly")
    return trace


__all__ = ["OperationalControlResult", "assess_operational_controls"]
