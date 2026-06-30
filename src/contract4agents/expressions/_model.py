"""Core expression model types used by parser, eval, and monitor internals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from contract4agents.expressions._trace_ops import TraceOp

ExpressionKind = Literal["output_conforms", "output_compare", "output_text", "hidden_truth", "trace"]


class ExpressionError(ValueError):
    """Raised when a Contract4Agents expression is not part of the supported V1 surface."""


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


__all__ = ["ExpressionError", "ExpressionKind", "ParsedExpression"]
