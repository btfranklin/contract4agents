"""Semantic reference extraction for parsed Contract4Agents expressions."""

from __future__ import annotations

from contract4agents.expressions._model import ParsedExpression
from contract4agents.expressions._trace_ops import TraceOp


def referenced_output_fields(parsed: ParsedExpression) -> set[str]:
    return {parsed.field} if parsed.field and parsed.kind in {"output_compare", "output_text"} else set()


def referenced_type(parsed: ParsedExpression) -> str | None:
    return parsed.type_name if parsed.kind == "output_conforms" else None


def referenced_trace_targets(parsed: ParsedExpression) -> tuple[TraceOp | None, tuple[str, ...]]:
    return parsed.trace_op, parsed.args if parsed.kind == "trace" else ()
