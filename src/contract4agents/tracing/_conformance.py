"""Conformance checks joining normalized evidence to canonical intent and plan."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.ir import CanonicalIR, SemanticId, contract_digest
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing._models import NormalizedTrace, TraceEvent


@dataclass(frozen=True)
class TraceConformanceIssue:
    """One precise reason normalized evidence cannot be trusted for assurance."""

    code: str
    message: str
    event_id: str | None = None
    semantic_id: SemanticId | None = None


class TraceConformanceError(ValueError):
    """Raised when normalized evidence contradicts the assessed IR or plan."""

    def __init__(self, issues: tuple[TraceConformanceIssue, ...]) -> None:
        if not issues:
            raise ValueError("TraceConformanceError requires at least one issue")
        self.issues = issues
        details = "; ".join(
            f"{issue.code}{f' ({issue.event_id})' if issue.event_id else ''}: {issue.message}"
            for issue in issues
        )
        super().__init__(f"Normalized trace does not conform: {details}")


def validate_trace_conformance(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    trace: NormalizedTrace,
) -> None:
    """Reject trace evidence that cannot be joined exactly to the IR and plan."""

    issues: list[TraceConformanceIssue] = []
    expected_contract_digest = contract_digest(ir)
    if plan.contract_digest != expected_contract_digest:
        issues.append(
            TraceConformanceIssue(
                "TRC001",
                "Materialization plan contract digest does not match the canonical IR.",
            )
        )
    expected_plan_digest = plan.plan_digest
    for event in trace.events:
        if event.context.contract_digest != expected_contract_digest:
            issues.append(
                TraceConformanceIssue(
                    "TRC002",
                    "Event contract digest does not match the canonical IR.",
                    event.event_id,
                )
            )
        if event.context.plan_digest != expected_plan_digest:
            issues.append(
                TraceConformanceIssue(
                    "TRC003",
                    "Event plan digest does not match the assessed materialization plan.",
                    event.event_id,
                )
            )
        if event.event_type == "capability.undeclared":
            issues.append(
                TraceConformanceIssue(
                    "TRC004",
                    "Provider behavior used a capability that was not uniquely enabled by the plan.",
                    event.event_id,
                    event.semantic.agent_id,
                )
            )
        if event.event_type.startswith("tool."):
            _validate_tool_event(event, ir, plan, issues)
    if issues:
        raise TraceConformanceError(tuple(issues))


def _validate_tool_event(
    event: TraceEvent,
    ir: CanonicalIR,
    plan: MaterializationPlan,
    issues: list[TraceConformanceIssue],
) -> None:
    agent_id = event.semantic.agent_id
    capability_id = event.semantic.capability_id
    grant_id = event.semantic.grant_id
    if agent_id is None or capability_id is None or grant_id is None:
        issues.append(
            TraceConformanceIssue(
                "TRC005",
                "Tool evidence requires agent, capability, and grant semantic identities.",
                event.event_id,
            )
        )
        return
    if agent_id not in ir.agents or agent_id not in plan.agents:
        issues.append(
            TraceConformanceIssue("TRC006", "Tool evidence names an unknown agent.", event.event_id, agent_id)
        )
    capability = ir.capabilities.get(capability_id)
    if capability is None or capability.kind != "tool" or capability_id not in plan.bindings:
        issues.append(
            TraceConformanceIssue(
                "TRC007",
                "Tool evidence names an unknown or unplanned tool capability.",
                event.event_id,
                capability_id,
            )
        )
    planned_grant = plan.grants.get(grant_id)
    canonical_grant = ir.grants.get(grant_id)
    if planned_grant is None or canonical_grant is None:
        issues.append(
            TraceConformanceIssue(
                "TRC008",
                "Tool evidence names an unknown or unplanned grant.",
                event.event_id,
                grant_id,
            )
        )
        return
    if planned_grant.availability != "enabled":
        issues.append(
            TraceConformanceIssue(
                "TRC009",
                "Tool evidence names a grant that is not enabled.",
                event.event_id,
                grant_id,
            )
        )
    if (
        planned_grant.agent_id != agent_id
        or planned_grant.capability_id != capability_id
        or canonical_grant.agent_id != agent_id
        or canonical_grant.capability_id != capability_id
    ):
        issues.append(
            TraceConformanceIssue(
                "TRC010",
                "Tool evidence identities do not match the canonical and planned grant.",
                event.event_id,
                grant_id,
            )
        )


__all__ = ["TraceConformanceError", "TraceConformanceIssue", "validate_trace_conformance"]
