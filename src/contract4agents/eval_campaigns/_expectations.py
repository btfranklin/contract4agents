"""Deterministic `.eval` expectation assessment against normalized traces."""

from __future__ import annotations

import operator
import re
from collections.abc import Mapping

from jsonschema import validate

from contract4agents.eval_campaigns._models import ExpectationResult
from contract4agents.expressions import (
    ExpressionError,
    ParsedExpression,
    parse_expectation,
)
from contract4agents.expressions._trace_evaluation import assess_trace_expression
from contract4agents.ir import CanonicalIR
from contract4agents.tracing import NormalizedTrace, TraceEvidenceAssessment


def assess_expectation(
    expression: str,
    *,
    output: Mapping[str, object],
    trace: NormalizedTrace,
    trace_evidence: TraceEvidenceAssessment,
    ir: CanonicalIR,
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
        result = assess_trace_expression(parsed, ir=ir, trace=trace, trace_evidence=trace_evidence)
        return ExpectationResult(
            parsed.expression,
            result.status,
            result.reason,
            tuple(event.event_id for event in result.events),
        )
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
