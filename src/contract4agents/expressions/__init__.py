"""Public expression API for Contract4Agents checks."""

from __future__ import annotations

from contract4agents.expressions._eval import evaluate_hidden_truth, evaluate_output, evaluate_trace
from contract4agents.expressions._grammar import (
    parse_contract_expression,
    parse_expectation,
    parse_monitor_condition,
    parse_monitor_expectation,
)
from contract4agents.expressions._model import ExpressionError, ExpressionKind, ParsedExpression
from contract4agents.expressions._refs import referenced_output_fields, referenced_trace_targets, referenced_type
from contract4agents.expressions._trace_ops import TraceOp

__all__ = [
    "ExpressionError",
    "ExpressionKind",
    "ParsedExpression",
    "TraceOp",
    "evaluate_hidden_truth",
    "evaluate_output",
    "evaluate_trace",
    "parse_contract_expression",
    "parse_expectation",
    "parse_monitor_condition",
    "parse_monitor_expectation",
    "referenced_output_fields",
    "referenced_trace_targets",
    "referenced_type",
]
