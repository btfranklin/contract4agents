"""Deterministic `.eval` expectation assessment against normalized traces."""

from __future__ import annotations

import json
import operator
import re
from collections.abc import Mapping

from jsonschema import validate

from contract4agents.eval_campaigns._models import ExpectationResult
from contract4agents.expressions import ExpressionError, ParsedExpression, parse_expectation
from contract4agents.expressions._trace_ops import TRACE_OPS, TraceTargetKind
from contract4agents.tracing import NormalizedTrace, TraceCompletenessResult, TraceEvent

_TARGET_KINDS: dict[TraceTargetKind, tuple[str, ...]] = {
    "any": ("agent", "capability", "control", "grant"),
    "agent": ("agent",),
    "tool": ("capability",),
    "datasource": ("capability",),
    "approval_tool": ("capability",),
    "guardrail": ("control",),
    "text": (),
    "agent_tool": (),
}


def assess_expectation(
    expression: str,
    *,
    output: Mapping[str, object],
    trace: NormalizedTrace,
    trace_completeness: TraceCompletenessResult,
    schemas: Mapping[str, dict[str, object]],
    hidden_truth: Mapping[str, object],
) -> ExpectationResult:
    try:
        parsed = parse_expectation(expression)
    except ExpressionError as exc:
        return ExpectationResult(expression, "unverified", str(exc))
    if parsed.kind.startswith("output"):
        return _output_result(parsed, output, schemas)
    if parsed.kind == "hidden_truth":
        return _hidden_truth_result(parsed, output, hidden_truth)
    if parsed.kind == "trace":
        return _trace_result(parsed, trace, trace_completeness.complete)
    return ExpectationResult(expression, "unverified", f"Unsupported deterministic expectation: {expression}")


def _output_result(
    parsed: ParsedExpression,
    output: Mapping[str, object],
    schemas: Mapping[str, dict[str, object]],
) -> ExpectationResult:
    if parsed.kind == "output_conforms":
        assert parsed.type_name is not None
        schema = schemas.get(parsed.type_name)
        if schema is None:
            return ExpectationResult(parsed.expression, "unverified", f"Unknown output schema `{parsed.type_name}`")
        try:
            validate(_thaw(output), schema)
        except Exception as exc:  # noqa: BLE001 - jsonschema exposes multiple validation error types.
            return ExpectationResult(
                parsed.expression,
                "violated",
                f"Output does not conform to {parsed.type_name}: {exc}",
            )
        return ExpectationResult(parsed.expression, "passed", f"Output conforms to {parsed.type_name}.")

    assert parsed.field is not None
    if parsed.field not in output:
        return ExpectationResult(parsed.expression, "violated", f"Output is missing field `{parsed.field}`.")
    if parsed.kind == "output_compare":
        operation = operator.eq if parsed.operator == "==" else operator.ne
        passed = operation(output[parsed.field], parsed.value)
        reason = (
            "Output comparison passed."
            if passed
            else f"Expected output.{parsed.field} {parsed.operator} {parsed.value!r}."
        )
    else:
        haystack = str(output[parsed.field])
        found = str(parsed.value) in haystack
        passed = found if parsed.operator == "contains" else not found
        reason = (
            "Output text expectation passed."
            if passed
            else f"Expected output.{parsed.field} to {parsed.operator} {parsed.value}."
        )
    return ExpectationResult(parsed.expression, "passed" if passed else "violated", reason)


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(name): _thaw(child) for name, child in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(child) for child in value]
    return value


def _hidden_truth_result(
    parsed: ParsedExpression,
    output: Mapping[str, object],
    hidden_truth: Mapping[str, object],
) -> ExpectationResult:
    assert parsed.field is not None
    if parsed.field not in hidden_truth:
        return ExpectationResult(
            parsed.expression,
            "unverified",
            f"Hidden truth `{parsed.field}` was not supplied by the eval provider.",
        )
    text = " ".join(str(value) for value in output.values()).lower()
    passed = _hidden_truth_matches(hidden_truth[parsed.field], text)
    return ExpectationResult(
        parsed.expression,
        "passed" if passed else "violated",
        "Output discovered the hidden truth."
        if passed
        else f"Output did not discover hidden truth `{parsed.field}`.",
    )


def _trace_result(
    parsed: ParsedExpression,
    trace: NormalizedTrace,
    trace_complete: bool,
) -> ExpectationResult:
    assert parsed.trace_op is not None
    operation = parsed.trace_op
    args = parsed.args
    target_kind = TRACE_OPS[operation].target_kind

    if operation == "contains":
        events = tuple(
            event
            for event in trace.events
            if args[0] in json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
        )
        return _presence_result(parsed.expression, events, trace_complete)
    if operation == "not_tool_called_by":
        events = tuple(
            event
            for event in trace.events
            if event.event_type == "tool.completed"
            and _matches(event, args[0], "agent")
            and _matches(event, args[1], "capability")
        )
        return _absence_result(parsed.expression, events, trace_complete)
    if operation in {"called_before", "called_after"}:
        left = _events(trace, args[0], None, target_kind)
        right = _events(trace, args[1], None, target_kind)
        evidence = left + right
        if not left or not right:
            return _missing_trace_result(parsed.expression, evidence, trace_complete)
        left_index = trace.events.index(left[0])
        right_index = trace.events.index(right[0])
        passed = left_index < right_index if operation == "called_before" else left_index > right_index
        return ExpectationResult(
            parsed.expression,
            "passed" if passed else "violated",
            "Trace ordering expectation passed." if passed else "Trace events occurred in the wrong order.",
            tuple(event.event_id for event in evidence),
        )
    if operation in {"approval_granted", "approval_denied"}:
        approval_expected = operation == "approval_granted"
        relevant = _events(trace, args[0], "approval.completed", target_kind)
        matching = tuple(event for event in relevant if event.data.get("approved") is approval_expected)
        if matching:
            return _presence_result(parsed.expression, matching, trace_complete)
        if relevant:
            return ExpectationResult(
                parsed.expression,
                "violated",
                f"The recorded approval decision was not {str(approval_expected).lower()}.",
                tuple(event.event_id for event in relevant),
            )
        return _missing_trace_result(parsed.expression, (), trace_complete)

    event_type = TRACE_OPS[operation].event_type
    events = _events(trace, args[0], event_type, target_kind)
    if operation == "not_called":
        return _absence_result(parsed.expression, events, trace_complete)
    if operation in {"called_once", "called_times", "max_calls"}:
        expected_count = 1 if operation == "called_once" else int(args[1])
        count = len(events)
        if operation == "max_calls":
            if count > expected_count:
                return _count_result(parsed.expression, events, False, count, f"at most {expected_count}")
            if not trace_complete:
                return _missing_trace_result(parsed.expression, events, False)
            return _count_result(parsed.expression, events, True, count, f"at most {expected_count}")
        if count == expected_count:
            return _count_result(parsed.expression, events, True, count, str(expected_count))
        if count > expected_count or trace_complete:
            return _count_result(parsed.expression, events, False, count, str(expected_count))
        return _missing_trace_result(parsed.expression, events, False)
    return _presence_result(parsed.expression, events, trace_complete)


def _events(
    trace: NormalizedTrace,
    target: str,
    event_type: str | None,
    target_kind: TraceTargetKind,
) -> tuple[TraceEvent, ...]:
    return tuple(
        event
        for event in trace.events
        if (event_type is None or event.event_type == event_type)
        and any(_matches(event, target, kind) for kind in _TARGET_KINDS[target_kind])
    )


def _matches(event: TraceEvent, target: str, kind: str) -> bool:
    clean = target.strip().strip('"')
    identifier = {
        "agent": event.semantic.agent_id,
        "capability": event.semantic.capability_id,
        "control": event.semantic.control_ids[0] if event.semantic.control_ids else None,
        "grant": event.semantic.grant_id,
    }[kind]
    if identifier is not None and clean in {str(identifier), identifier.parts[-1]}:
        return True
    fallback_fields = {
        "agent": ("agent",),
        "capability": ("tool", "capability", "datasource", "produces"),
        "control": ("control", "guardrail"),
        "grant": ("grant",),
    }[kind]
    return clean in {str(event.data.get(field)) for field in fallback_fields if field in event.data}


def _presence_result(expression: str, events: tuple[TraceEvent, ...], trace_complete: bool) -> ExpectationResult:
    if events:
        return ExpectationResult(
            expression,
            "passed",
            "Expected trace evidence was observed.",
            tuple(event.event_id for event in events),
        )
    return _missing_trace_result(expression, events, trace_complete)


def _absence_result(expression: str, events: tuple[TraceEvent, ...], trace_complete: bool) -> ExpectationResult:
    if events:
        return ExpectationResult(
            expression,
            "violated",
            "Forbidden trace evidence was observed.",
            tuple(event.event_id for event in events),
        )
    if not trace_complete:
        return _missing_trace_result(expression, events, False)
    return ExpectationResult(expression, "passed", "Complete telemetry contains no forbidden trace evidence.")


def _missing_trace_result(
    expression: str,
    events: tuple[TraceEvent, ...],
    trace_complete: bool,
) -> ExpectationResult:
    return ExpectationResult(
        expression,
        "violated" if trace_complete else "unverified",
        "Expected trace evidence was not observed."
        if trace_complete
        else "Trace evidence is incomplete, so absence cannot prove this expectation.",
        tuple(event.event_id for event in events),
    )


def _count_result(
    expression: str,
    events: tuple[TraceEvent, ...],
    passed: bool,
    count: int,
    expected: str,
) -> ExpectationResult:
    return ExpectationResult(
        expression,
        "passed" if passed else "violated",
        f"Observed {count} matching trace event(s); expected {expected}.",
        tuple(event.event_id for event in events),
    )


def _hidden_truth_matches(rule: object, text: str) -> bool:
    if isinstance(rule, Mapping):
        if "contains_all" in rule:
            values = rule["contains_all"]
            return isinstance(values, list | tuple) and all(str(term).lower() in text for term in values)
        if "contains_any" in rule:
            values = rule["contains_any"]
            return isinstance(values, list | tuple) and any(str(term).lower() in text for term in values)
    if isinstance(rule, list | tuple):
        return all(str(term).lower() in text for term in rule)
    words = [word for word in re.findall(r"[a-z0-9_]+", str(rule).lower()) if len(word) > 3]
    return bool(words) and sum(word in text for word in words) >= max(1, len(words) // 3)


__all__ = ["assess_expectation"]
