"""Runtime evaluation for parsed Contract4Agents expressions."""

from __future__ import annotations

import operator
import re
from collections.abc import Mapping, Sequence, Set
from typing import Any

from contract4agents.expressions._model import ExpressionError, ParsedExpression
from contract4agents.expressions._trace_ops import TRACE_OPS, TraceTargetKind
from contract4agents.runtime import TraceEvent, TraceRecorder

_TARGET_FIELDS_BY_KIND: dict[TraceTargetKind, tuple[str, ...]] = {
    "any": ("agent", "tool", "datasource", "produces", "guardrail", "assertion", "stage"),
    "agent": ("agent",),
    "tool": ("tool",),
    "hosted_tool": ("tool",),
    "datasource": ("datasource", "produces"),
    "approval_tool": ("tool",),
    "guardrail": ("guardrail",),
    "text": (),
    "agent_tool": (),
}


def evaluate_output(parsed: ParsedExpression, output: dict[str, Any], schemas: dict[str, dict[str, Any]]) -> str | None:
    """Return a failure message for an output expression, or None when it passes."""
    if parsed.kind == "output_conforms":
        from jsonschema import validate

        assert parsed.type_name is not None
        schema = schemas.get(parsed.type_name)
        if schema is None:
            return f"Unknown output schema `{parsed.type_name}`"
        try:
            validate(output, schema)
        except Exception as exc:  # noqa: BLE001 - jsonschema exposes several validation exception types.
            return f"Output does not conform to {parsed.type_name}: {exc}"
        return None

    if parsed.kind == "output_compare":
        assert parsed.field is not None
        if parsed.field not in output:
            return f"Output missing field `{parsed.field}`"
        operation = operator.eq if parsed.operator == "==" else operator.ne
        if not operation(output.get(parsed.field), parsed.value):
            return f"Expected output.{parsed.field} {parsed.operator} {parsed.value!r}"
        return None

    if parsed.kind == "output_text":
        assert parsed.field is not None
        if parsed.field not in output:
            return f"Output missing field `{parsed.field}`"
        haystack = str(output.get(parsed.field, ""))
        found = str(parsed.value) in haystack
        if parsed.operator == "contains" and not found:
            return f"Expected output.{parsed.field} to contain {parsed.value}"
        if parsed.operator == "excludes" and found:
            return f"Expected output.{parsed.field} to exclude {parsed.value}"
        return None

    raise ExpressionError(f"Not an output expression: {parsed.expression}")


def evaluate_hidden_truth(parsed: ParsedExpression, output: dict[str, Any], hidden_truth: dict[str, Any]) -> str | None:
    if parsed.kind != "hidden_truth":
        raise ExpressionError(f"Not a hidden-truth expression: {parsed.expression}")
    assert parsed.field is not None
    rule = hidden_truth.get(parsed.field, "")
    text = " ".join(str(value) for value in output.values()).lower()
    if _hidden_truth_matches(rule, text):
        return None
    return f"Output did not discover {parsed.field} from hidden truth"


def evaluate_trace(parsed: ParsedExpression, trace: TraceRecorder) -> str | None:
    if parsed.kind != "trace" or parsed.trace_op is None:
        raise ExpressionError(f"Not a trace expression: {parsed.expression}")
    op = parsed.trace_op
    args = parsed.args
    target_kind = TRACE_OPS[op].target_kind

    if op == "not_called":
        if _trace_contains(trace, args[0], None, target_kind):
            return f"Expected trace not to include {args[0]}"
        return None
    if op == "called_once":
        count = _trace_count(trace, args[0], None, target_kind)
        return None if count == 1 else f"Expected trace to include {args[0]} exactly once, found {count}"
    if op == "called_times":
        expected = int(args[1])
        count = _trace_count(trace, args[0], None, target_kind)
        return None if count == expected else f"Expected trace to include {args[0]} {expected} times, found {count}"
    if op == "max_calls":
        maximum = int(args[1])
        count = _trace_count(trace, args[0], None, target_kind)
        if count <= maximum:
            return None
        return f"Expected trace to include {args[0]} at most {maximum} times, found {count}"
    if op == "not_tool_called_by":
        agent, tool = args
        if _tool_called_by(trace, agent, tool):
            return f"Expected trace not to include {tool} called by {agent}"
        return None
    if op in {"called_before", "called_after"}:
        left, right = args
        left_index = _first_trace_index(trace, left, None, target_kind)
        right_index = _first_trace_index(trace, right, None, target_kind)
        if left_index is None or right_index is None:
            return f"Expected trace to include both {left} and {right}"
        ok = left_index < right_index if op == "called_before" else left_index > right_index
        word = "before" if op == "called_before" else "after"
        return None if ok else f"Expected trace to include {left} {word} {right}"
    if op == "approval_granted":
        return None if _approval_matches(trace, args[0], True) else f"Expected approval granted for {args[0]}"
    if op == "approval_denied":
        return None if _approval_matches(trace, args[0], False) else f"Expected approval denied for {args[0]}"
    if op == "contains":
        if any(args[0] in str(event.data) for event in trace.events):
            return None
        return f"Expected trace to contain {args[0]}"

    event_type = TRACE_OPS[op].event_type
    return None if _trace_contains(trace, args[0], event_type, target_kind) else f"Expected trace to include {args[0]}"


def evaluate_data_relation(parsed: ParsedExpression, derived_values: Mapping[str, Any] | None) -> str | None:
    """Return a failure message for a derived-value relation, or None when it passes."""
    if parsed.kind != "data_relation" or not parsed.operator or not parsed.left_ref or not parsed.right_ref:
        raise ExpressionError(f"Not a data relation expression: {parsed.expression}")
    if derived_values is None:
        return "No derived values supplied for data relation assertions"

    left = _resolve_derived_set(parsed.left_ref, derived_values)
    if isinstance(left, str):
        return left
    right = _resolve_derived_set(parsed.right_ref, derived_values)
    if isinstance(right, str):
        return right

    if parsed.operator == "subset_of":
        missing = left - right
        if missing:
            return (
                f"value.{parsed.left_ref} is not a subset of value.{parsed.right_ref}: "
                f"missing {_format_items(missing)}"
            )
        return None
    if parsed.operator == "contains_all":
        missing = right - left
        if missing:
            return (
                f"value.{parsed.left_ref} does not contain all values from value.{parsed.right_ref}: "
                f"missing {_format_items(missing)}"
            )
        return None
    if parsed.operator == "equals_set":
        if left == right:
            return None
        missing = right - left
        extra = left - right
        parts: list[str] = []
        if missing:
            parts.append(f"missing {_format_items(missing)}")
        if extra:
            parts.append(f"extra {_format_items(extra)}")
        return f"value.{parsed.left_ref} does not equal value.{parsed.right_ref}: {', '.join(parts)}"
    if parsed.operator == "intersects":
        if left & right:
            return None
        return f"value.{parsed.left_ref} does not intersect value.{parsed.right_ref}"
    if parsed.operator == "disjoint_from":
        overlap = left & right
        if not overlap:
            return None
        return (
            f"value.{parsed.left_ref} is not disjoint from value.{parsed.right_ref}: "
            f"overlap {_format_items(overlap)}"
        )
    return f"Unsupported data relation operator `{parsed.operator}`"


def evaluate_parsed_expression(
    parsed: ParsedExpression,
    *,
    output: dict[str, Any],
    schemas: dict[str, dict[str, Any]],
    trace: TraceRecorder,
    hidden_truth: dict[str, Any],
) -> tuple[str, str] | None:
    """Return `(failure_kind, message)` for a parsed expression, or None when it passes."""
    if parsed.kind.startswith("output"):
        failure = evaluate_output(parsed, output, schemas)
        return ("output", failure) if failure else None
    if parsed.kind == "trace":
        failure = evaluate_trace(parsed, trace)
        return ("trace", failure) if failure else None
    if parsed.kind == "hidden_truth":
        failure = evaluate_hidden_truth(parsed, output, hidden_truth)
        return ("hidden_truth", failure) if failure else None
    return ("unsupported", f"Unsupported expression: {parsed.expression}")


def _resolve_derived_set(ref: str, derived_values: Mapping[str, Any]) -> set[Any] | str:
    if ref not in derived_values:
        return f"Unknown derived value `value.{ref}`"
    return _coerce_scalar_set(ref, derived_values[ref])


def _coerce_scalar_set(ref: str, value: Any) -> set[Any] | str:
    if _is_scalar_relation_item(value):
        return {value}
    if isinstance(value, Mapping):
        return f"Derived value `value.{ref}` must be a scalar or sequence of scalars"
    if isinstance(value, bytes | bytearray):
        return f"Derived value `value.{ref}` must be a supported scalar or sequence of scalars"
    if isinstance(value, Sequence | Set):
        items: set[Any] = set()
        for index, item in enumerate(value):
            if not _is_scalar_relation_item(item):
                return f"Derived value `value.{ref}` contains non-scalar item at index {index}: {item!r}"
            items.add(item)
        return items
    return f"Derived value `value.{ref}` must be a scalar or sequence of scalars"


def _is_scalar_relation_item(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _format_items(items: set[Any], *, limit: int = 10) -> str:
    ordered = sorted(items, key=repr)
    shown = ", ".join(str(item) for item in ordered[:limit])
    remaining = len(ordered) - limit
    return f"{shown}, and {remaining} more" if remaining > 0 else shown


def _hidden_truth_matches(rule: Any, text: str) -> bool:
    if isinstance(rule, dict):
        if "contains_all" in rule:
            return all(str(term).lower() in text for term in rule["contains_all"])
        if "contains_any" in rule:
            return any(str(term).lower() in text for term in rule["contains_any"])
    if isinstance(rule, list):
        return all(str(term).lower() in text for term in rule)
    truth = str(rule).lower()
    words = [word for word in re.findall(r"[a-z0-9_]+", truth) if len(word) > 3]
    return bool(words) and sum(word in text for word in words) >= max(1, len(words) // 3)


def _trace_contains(
    trace: TraceRecorder,
    target: str,
    event_type: str | None,
    target_kind: TraceTargetKind,
) -> bool:
    return _first_trace_index(trace, target, event_type, target_kind) is not None


def _trace_count(
    trace: TraceRecorder,
    target: str,
    event_type: str | None,
    target_kind: TraceTargetKind,
) -> int:
    return sum(1 for event in trace.events if _event_matches(event, target, event_type, target_kind))


def _first_trace_index(
    trace: TraceRecorder,
    target: str,
    event_type: str | None,
    target_kind: TraceTargetKind,
) -> int | None:
    for index, event in enumerate(trace.events):
        if _event_matches(event, target, event_type, target_kind):
            return index
    return None


def _event_matches(
    event: TraceEvent,
    target: str,
    event_type: str | None,
    target_kind: TraceTargetKind,
) -> bool:
    if event_type and event.type != event_type:
        return False
    return _target_in_event_fields(target, event.data, _TARGET_FIELDS_BY_KIND[target_kind])


def _target_in_event_fields(target: str, data: dict[str, Any], fields: tuple[str, ...]) -> bool:
    clean_target = target.strip().strip('"')
    return clean_target in {str(data[field]) for field in fields if field in data}


def _approval_matches(trace: TraceRecorder, target: str, approved: bool) -> bool:
    return any(
        event.type == "approval.completed"
        and event.data.get("approved") is approved
        and _target_in_event_fields(target, event.data, _TARGET_FIELDS_BY_KIND["approval_tool"])
        for event in trace.events
    )


def _tool_called_by(trace: TraceRecorder, agent: str, tool: str) -> bool:
    clean_agent = agent.strip().strip('"')
    clean_tool = tool.strip().strip('"')
    return any(
        event.type in {"tool.completed", "hosted_tool.completed"}
        and event.data.get("agent") == clean_agent
        and event.data.get("tool") == clean_tool
        for event in trace.events
    )
