"""Public expression API for Contract4Agents checks."""

from __future__ import annotations

from contract4agents.expressions._grammar import (
    parse_contract_expression,
    parse_expectation,
    parse_semantic_expectation,
    parse_trace_conjunction,
)
from contract4agents.expressions._model import (
    ConditionalExpression,
    ConjunctiveExpression,
    ContractExpression,
    ExpressionError,
    ExpressionKind,
    ParsedExpression,
)
from contract4agents.expressions._refs import referenced_output_fields, referenced_trace_targets, referenced_type
from contract4agents.expressions._source_refs import ExpressionSourceReference, expression_source_references
from contract4agents.expressions._trace_ops import TraceOp

__all__ = [
    "ConditionalExpression",
    "ConjunctiveExpression",
    "ContractExpression",
    "ExpressionError",
    "ExpressionSourceReference",
    "ExpressionKind",
    "ParsedExpression",
    "TraceOp",
    "parse_contract_expression",
    "parse_expectation",
    "parse_semantic_expectation",
    "parse_trace_conjunction",
    "expression_source_references",
    "referenced_output_fields",
    "referenced_trace_targets",
    "referenced_type",
]
