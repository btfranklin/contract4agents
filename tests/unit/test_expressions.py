from __future__ import annotations

import pytest

from contract4agents.expressions import ExpressionError, parse_trace_conjunction


def test_trace_conjunction_parses_one_or_more_trace_clauses() -> None:
    single = parse_trace_conjunction("trace.tool_called(status.publish)")
    combined = parse_trace_conjunction(
        "trace.tool_called(status.publish) and trace.not_called(secret.publish)"
    )

    assert [clause.trace_op for clause in single.clauses] == ["tool_called"]
    assert [clause.trace_op for clause in combined.clauses] == [
        "tool_called",
        "not_called",
    ]


@pytest.mark.parametrize(
    "expression",
    (
        "trace.tool_called(status.publish) and",
        "trace.tool_called(status.publish) or trace.not_called(secret.publish)",
        "trace.tool_called(status.publish) and output.status == ok",
    ),
)
def test_trace_conjunction_rejects_non_conjunctive_syntax(expression: str) -> None:
    with pytest.raises(ExpressionError):
        parse_trace_conjunction(expression)
