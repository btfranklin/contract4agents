"""Positioned semantic references in expression source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, cast

from contract4agents.expressions._trace_ops import TRACE_OPS, TraceOp, TraceTargetKind

ExpressionReferenceKind = Literal["agent", "any", "datasource", "tool", "type"]

_TRACE_CALL_RE = re.compile(r"trace\.(?P<op>[A-Za-z_][A-Za-z0-9_]*)\((?P<args>[^)]*)\)")
_FIXTURE_RE = re.compile(r"\b(?P<type>[A-Za-z_][A-Za-z0-9_]*)\.fixture\b")
_OUTPUT_CONFORMS_RE = re.compile(r"\boutput\s+conforms\s+(?P<type>[A-Za-z_][A-Za-z0-9_]*)")
_FORBID_TOOL_RE = re.compile(r"\bforbid\(tool\.(?P<tool>[A-Za-z_][A-Za-z0-9_.]*)")


@dataclass(frozen=True)
class ExpressionSourceReference:
    kind: ExpressionReferenceKind
    name: str
    start: int
    end: int


def expression_source_references(expression: str) -> tuple[ExpressionSourceReference, ...]:
    """Extract positioned names using the expression subsystem's vocabulary."""

    references: list[ExpressionSourceReference] = []
    references.extend(_named_matches(expression, _FIXTURE_RE, "type", "type"))
    references.extend(_named_matches(expression, _OUTPUT_CONFORMS_RE, "type", "type"))
    references.extend(_named_matches(expression, _FORBID_TOOL_RE, "tool", "tool"))
    for match in _TRACE_CALL_RE.finditer(expression):
        spec = TRACE_OPS.get(cast(TraceOp, match.group("op")))
        if spec is None:
            continue
        args = match.group("args")
        arg_base = match.start("args")
        for index, arg_match in enumerate(re.finditer(r"[A-Za-z_][A-Za-z0-9_.]*", args)):
            kind = _trace_reference_kind(spec.target_kind, index, spec.count_arg_index)
            if kind is None:
                continue
            references.append(
                ExpressionSourceReference(
                    kind,
                    arg_match.group(0),
                    arg_base + arg_match.start(),
                    arg_base + arg_match.end(),
                )
            )
    return tuple(references)


def _named_matches(
    expression: str,
    pattern: re.Pattern[str],
    group: str,
    kind: ExpressionReferenceKind,
) -> list[ExpressionSourceReference]:
    return [
        ExpressionSourceReference(kind, match.group(group), *match.span(group))
        for match in pattern.finditer(expression)
    ]


def _trace_reference_kind(
    target_kind: TraceTargetKind,
    index: int,
    count_arg_index: int | None,
) -> ExpressionReferenceKind | None:
    if count_arg_index == index or target_kind in {"text", "guardrail"}:
        return None
    if target_kind in {"tool", "approval_tool"}:
        return "tool"
    if target_kind == "agent" or target_kind == "agent_tool" and index == 0:
        return "agent"
    if target_kind == "datasource":
        return "datasource"
    if target_kind == "agent_tool":
        return "tool"
    return "any"


__all__ = ["ExpressionReferenceKind", "ExpressionSourceReference", "expression_source_references"]
