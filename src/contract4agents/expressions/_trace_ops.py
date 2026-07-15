"""Trace spy metadata shared by parsing, semantics, and evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeGuard

TraceOp = Literal[
    "called",
    "not_called",
    "called_once",
    "called_times",
    "called_before",
    "called_after",
    "max_calls",
    "not_tool_called_by",
    "tool_called",
    "agent_called",
    "datasource_resolved",
    "approval_requested",
    "approval_granted",
    "approval_denied",
    "guardrail_rejected",
    "contains",
]
TraceTargetKind = Literal[
    "any",
    "agent",
    "tool",
    "datasource",
    "approval_tool",
    "guardrail",
    "text",
    "agent_tool",
]


@dataclass(frozen=True)
class TraceOpSpec:
    arity: int
    target_kind: TraceTargetKind
    event_type: str | None = None
    count_arg_index: int | None = None


TRACE_OPS: dict[TraceOp, TraceOpSpec] = {
    "called": TraceOpSpec(1, "any"),
    "not_called": TraceOpSpec(1, "any"),
    "called_once": TraceOpSpec(1, "any"),
    "called_times": TraceOpSpec(2, "any", count_arg_index=1),
    "called_before": TraceOpSpec(2, "any"),
    "called_after": TraceOpSpec(2, "any"),
    "max_calls": TraceOpSpec(2, "any", count_arg_index=1),
    "not_tool_called_by": TraceOpSpec(2, "agent_tool"),
    "tool_called": TraceOpSpec(1, "tool", "tool.completed"),
    "agent_called": TraceOpSpec(1, "agent", "agent.completed"),
    "datasource_resolved": TraceOpSpec(1, "datasource", "datasource.resolved"),
    "approval_requested": TraceOpSpec(1, "approval_tool", "approval.requested"),
    "approval_granted": TraceOpSpec(1, "approval_tool"),
    "approval_denied": TraceOpSpec(1, "approval_tool"),
    "guardrail_rejected": TraceOpSpec(1, "guardrail", "guardrail.rejected"),
    "contains": TraceOpSpec(1, "text"),
}


def is_trace_op(value: str) -> TypeGuard[TraceOp]:
    return value in TRACE_OPS
