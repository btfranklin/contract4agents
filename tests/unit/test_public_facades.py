from __future__ import annotations

from contract4agents import expressions, runtime
from contract4agents.expressions import ExpressionError, ParsedExpression
from contract4agents.runtime import ContextValue, RuntimeContext, TraceEvent, TraceRecorder


def test_expression_facade_exports_canonical_public_surface() -> None:
    assert expressions.ExpressionError is ExpressionError
    assert expressions.ParsedExpression is ParsedExpression
    assert callable(expressions.parse_expectation)
    assert callable(expressions.evaluate_output)
    assert callable(expressions.referenced_trace_targets)


def test_runtime_facade_exports_canonical_public_surface() -> None:
    assert runtime.ContextValue is ContextValue
    assert runtime.RuntimeContext is RuntimeContext
    assert runtime.TraceEvent is TraceEvent
    assert runtime.TraceRecorder is TraceRecorder
    assert callable(runtime.datasource)
    assert callable(runtime.load_python_ref)
