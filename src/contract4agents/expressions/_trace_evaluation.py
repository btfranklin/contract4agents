"""Shared three-valued evaluation for normalized-trace expressions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from contract4agents.expressions._model import ParsedExpression
from contract4agents.expressions._trace_ops import TRACE_OPS, TraceTargetKind
from contract4agents.ir import CanonicalIR, SemanticId
from contract4agents.tracing import (
    NormalizedTrace,
    TraceCompletenessResult,
    TraceCoverageChannel,
    TraceEvent,
)

TraceExpressionStatus = Literal["passed", "violated", "unverified"]

_CALL_EVENT_TYPES = {
    "agent": frozenset({"agent.started", "agent.completed", "agent.failed"}),
    "approval": frozenset({"approval.requested", "approval.completed"}),
    "composition": frozenset({"composition.started", "composition.completed", "composition.failed"}),
    "datasource": frozenset({"datasource.resolved", "datasource.failed"}),
    "guardrail": frozenset({"guardrail.rejected"}),
    "handoff": frozenset({"handoff.started", "handoff.completed", "handoff.failed"}),
    "output": frozenset({"output.accepted", "output.schema_failed"}),
    "provider_response": frozenset({"provider.response.normalized", "provider.response_batch.normalized"}),
    "tool": frozenset({"tool.started", "tool.completed", "tool.failed"}),
}
_ALL_CHANNELS: tuple[TraceCoverageChannel, ...] = (
    "agent",
    "approval",
    "composition",
    "datasource",
    "guardrail",
    "handoff",
    "output",
    "provider_response",
    "tool",
)


@dataclass(frozen=True)
class TraceExpressionResult:
    status: TraceExpressionStatus
    reason: str
    events: tuple[TraceEvent, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def assess_trace_expression(
    parsed: ParsedExpression,
    ir: CanonicalIR,
    trace: NormalizedTrace,
    completeness: TraceCompletenessResult,
    *,
    stage_event_ids: dict[str, tuple[str, ...]] | None = None,
) -> TraceExpressionResult:
    """Evaluate one parsed trace expression without treating absence as evidence."""

    if parsed.kind != "trace" or parsed.trace_op is None:
        return TraceExpressionResult("unverified", f"Unsupported trace expression `{parsed.expression}`.")
    op = parsed.trace_op
    args = parsed.args
    target_kind = TRACE_OPS[op].target_kind
    stages = stage_event_ids or {}

    if op == "contains":
        events = tuple(
            event
            for event in trace.events
            if args[0] in json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
        )
        if events:
            return _result("passed", "Expected trace content was observed.", events)
        if _channels_complete(completeness, _ALL_CHANNELS):
            return _result("violated", "The complete trace did not contain the expected content.")
        return _result("unverified", "Trace closure is insufficient to prove content absence.")

    if op == "not_tool_called_by":
        agent_id = _agent_id(ir, args[0])
        capability_id = _capability_id(ir, args[1])
        if agent_id is None or capability_id is None:
            return _result("unverified", "The expression references an unknown agent or capability.")
        events = tuple(
            event
            for event in trace.events
            if event.event_type in _CALL_EVENT_TYPES["tool"]
            and event.semantic.agent_id == agent_id
            and event.semantic.capability_id == capability_id
        )
        return _absence_result(parsed.expression, events, completeness, ("tool",))

    if op in {"called_before", "called_after"}:
        left, left_channels = _target_events(ir, trace, args[0], target_kind, stages)
        right, right_channels = _target_events(ir, trace, args[1], target_kind, stages)
        events = _ordered_unique(trace, left + right)
        if left and right:
            left_index = min(trace.events.index(event) for event in left)
            right_index = min(trace.events.index(event) for event in right)
            passed = left_index < right_index if op == "called_before" else left_index > right_index
            return _result(
                "passed" if passed else "violated",
                "Trace ordering requirement passed." if passed else "Trace events occurred in the wrong order.",
                events,
            )
        if not left_channels or not right_channels:
            return _result("unverified", "The ordering expression references an unknown target.", events)
        channels = tuple(sorted(set(left_channels + right_channels)))
        if _channels_complete(completeness, channels):
            return _result("violated", "The closed trace did not contain both ordered targets.", events)
        return _result("unverified", "Trace closure is insufficient to assess ordering.", events)

    if op in {"approval_granted", "approval_denied"}:
        relevant, channels = _target_events(ir, trace, args[0], "approval_tool", stages)
        relevant = tuple(event for event in relevant if event.event_type == "approval.completed")
        expected_approval = op == "approval_granted"
        matching = tuple(event for event in relevant if event.data.get("approved") is expected_approval)
        if matching:
            return _result(
                "passed",
                f"The expected approval decision was {str(expected_approval).lower()}.",
                matching,
            )
        if relevant:
            return _result("violated", "The recorded approval decision was the opposite value.", relevant)
        if _channels_complete(completeness, channels):
            return _result("violated", "No approval decision was observed in the closed trace.")
        return _result("unverified", "Approval-channel closure is insufficient to assess the decision.")

    events, channels = _target_events(ir, trace, args[0], target_kind, stages)
    event_type = TRACE_OPS[op].event_type
    if event_type is not None and op not in {"tool_called", "agent_called"}:
        events = tuple(event for event in events if event.event_type == event_type)
    if op == "not_called":
        return _absence_result(parsed.expression, events, completeness, channels)
    if op in {"called_once", "called_times", "max_calls"}:
        events = _count_events(events)
        expected_count = 1 if op == "called_once" else int(args[1])
        count = len(events)
        if op == "max_calls":
            if count > expected_count:
                return _result("violated", f"Observed {count} calls; expected at most {expected_count}.", events)
            if _channels_complete(completeness, channels):
                return _result("passed", f"Observed {count} calls; expected at most {expected_count}.", events)
            return _result("unverified", "Trace closure is insufficient to prove the call upper bound.", events)
        if count > expected_count:
            return _result("violated", f"Observed {count} calls; expected exactly {expected_count}.", events)
        if _channels_complete(completeness, channels):
            return _result(
                "passed" if count == expected_count else "violated",
                f"Observed {count} calls; expected exactly {expected_count}.",
                events,
            )
        return _result("unverified", "Trace closure is insufficient to prove the exact call count.", events)
    return _presence_result(parsed.expression, events, completeness, channels)


def _presence_result(
    expression: str,
    events: tuple[TraceEvent, ...],
    completeness: TraceCompletenessResult,
    channels: tuple[TraceCoverageChannel, ...],
) -> TraceExpressionResult:
    if events:
        return _result("passed", "Expected trace evidence was observed.", events)
    if _channels_complete(completeness, channels):
        return _result("violated", f"The closed trace did not satisfy `{expression}`.")
    return _result("unverified", f"Trace closure is insufficient to assess `{expression}`.")


def _absence_result(
    expression: str,
    events: tuple[TraceEvent, ...],
    completeness: TraceCompletenessResult,
    channels: tuple[TraceCoverageChannel, ...],
) -> TraceExpressionResult:
    if events:
        return _result("violated", f"Forbidden trace evidence for `{expression}` was observed.", events)
    if _channels_complete(completeness, channels):
        return _result("passed", "Complete channel closure contains no forbidden trace evidence.")
    return _result("unverified", f"Trace closure is insufficient to prove `{expression}`.")


def _target_events(
    ir: CanonicalIR,
    trace: NormalizedTrace,
    target: str,
    target_kind: TraceTargetKind,
    stages: dict[str, tuple[str, ...]],
) -> tuple[tuple[TraceEvent, ...], tuple[TraceCoverageChannel, ...]]:
    clean = target.strip().strip('"')
    if target_kind == "approval_tool":
        capability_id = _capability_id(ir, clean)
        if capability_id is None:
            return (), ()
        return (
            tuple(
                event
                for event in trace.events
                if event.semantic.capability_id == capability_id
                and event.event_type in _CALL_EVENT_TYPES["approval"]
            ),
            ("approval",),
        )
    if target_kind == "guardrail":
        control_id = _control_id(ir, clean)
        if control_id is None:
            return (), ()
        return (
            tuple(
                event
                for event in trace.events
                if control_id in event.semantic.control_ids
                and event.event_type in _CALL_EVENT_TYPES["guardrail"]
            ),
            ("guardrail",),
        )
    if clean in stages and target_kind == "any":
        identifiers = set(stages[clean])
        return tuple(event for event in trace.events if event.event_id in identifiers), ("agent",)
    agent_id = _agent_id(ir, clean)
    if agent_id is not None and target_kind in {"agent", "any"}:
        return (
            tuple(
                event
                for event in trace.events
                if event.semantic.agent_id == agent_id and event.event_type in _CALL_EVENT_TYPES["agent"]
            ),
            ("agent",),
        )
    capability_id = _capability_id(ir, clean)
    if capability_id is not None and target_kind in {"tool", "datasource", "any"}:
        capability = ir.capabilities[capability_id]
        channel: TraceCoverageChannel = "tool" if capability.kind == "tool" else "datasource"
        return (
            tuple(
                event
                for event in trace.events
                if event.semantic.capability_id == capability_id
                and event.event_type in _CALL_EVENT_TYPES[channel]
            ),
            (channel,),
        )
    return (), ()


def _channels_complete(
    completeness: TraceCompletenessResult,
    channels: tuple[TraceCoverageChannel, ...],
) -> bool:
    return bool(channels) and all(completeness.complete_for(channel) for channel in channels)


def _count_events(events: tuple[TraceEvent, ...]) -> tuple[TraceEvent, ...]:
    """Count one call boundary per invocation when lifecycle pairs are present."""

    started = tuple(event for event in events if event.event_type.endswith(".started"))
    if started:
        return started
    terminal = tuple(
        event
        for event in events
        if event.event_type.endswith(".completed") or event.event_type.endswith(".failed")
    )
    return terminal or events


def _agent_id(ir: CanonicalIR, name: str) -> SemanticId | None:
    clean = name.strip().strip('"')
    return next((item.id for item in ir.agents.values() if clean in {item.name, str(item.id)}), None)


def _capability_id(ir: CanonicalIR, name: str) -> SemanticId | None:
    clean = name.strip().strip('"')
    return next((item.id for item in ir.capabilities.values() if clean in {item.name, str(item.id)}), None)


def _control_id(ir: CanonicalIR, name: str) -> SemanticId | None:
    clean = name.strip().strip('"')
    return next((item.id for item in ir.controls.values() if clean in {item.name, str(item.id)}), None)


def _ordered_unique(trace: NormalizedTrace, events: tuple[TraceEvent, ...]) -> tuple[TraceEvent, ...]:
    identifiers = {event.event_id for event in events}
    return tuple(event for event in trace.events if event.event_id in identifiers)


def _result(
    status: TraceExpressionStatus,
    reason: str,
    events: tuple[TraceEvent, ...] = (),
) -> TraceExpressionResult:
    return TraceExpressionResult(status, reason, events)


__all__ = ["TraceExpressionResult", "TraceExpressionStatus", "assess_trace_expression"]
