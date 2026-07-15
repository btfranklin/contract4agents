"""Public expression API for Contract4Agents checks."""

from __future__ import annotations

from contract4agents.expressions._grammar import (
    parse_contract_expression,
    parse_expectation,
    parse_semantic_expectation,
)
from contract4agents.expressions._model import (
    ConditionalExpression,
    ContractExpression,
    ExpressionError,
    ExpressionKind,
    ParsedExpression,
)
from contract4agents.expressions._refs import referenced_output_fields, referenced_trace_targets, referenced_type
from contract4agents.expressions._trace_ops import TraceOp

__all__ = [
    "ExpressionError",
    "ExpressionKind",
    "ConditionalExpression",
    "ContractExpression",
    "ParsedExpression",
    "TraceOp",
    "parse_contract_expression",
    "parse_expectation",
    "parse_semantic_expectation",
    "referenced_output_fields",
    "referenced_trace_targets",
    "referenced_type",
]
