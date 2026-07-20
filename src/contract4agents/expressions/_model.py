"""Core expression model types used by parser, eval, and assessment internals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from contract4agents.expressions._trace_ops import TraceOp

DataRelationOperator = Literal["subset_of", "contains_all", "equals_set", "intersects", "disjoint_from"]
ExpressionKind = Literal[
    "output_conforms",
    "output_compare",
    "output_text",
    "hidden_truth",
    "trace",
    "semantic",
    "data_relation",
]
ExpressionWrapper = Literal["expect", "require", "forbid"]


class ExpressionError(ValueError):
    """Raised when a Contract4Agents expression is outside the supported surface."""


@dataclass(frozen=True)
class ParsedExpression:
    kind: ExpressionKind
    expression: str
    type_name: str | None = None
    field: str | None = None
    operator: str | None = None
    value: Any = None
    trace_op: TraceOp | None = None
    args: tuple[str, ...] = ()
    left_ref: str | None = None
    right_ref: str | None = None
    wrapper: ExpressionWrapper | None = None
    approval_required: bool = False


@dataclass(frozen=True)
class ConditionalExpression:
    expression: str
    condition: ParsedExpression
    expectation: ParsedExpression


@dataclass(frozen=True)
class ConjunctiveExpression:
    expression: str
    clauses: tuple[ParsedExpression, ...]


ContractExpression = ParsedExpression | ConditionalExpression


__all__ = [
    "ConditionalExpression",
    "ConjunctiveExpression",
    "ContractExpression",
    "DataRelationOperator",
    "ExpressionError",
    "ExpressionKind",
    "ExpressionWrapper",
    "ParsedExpression",
]
