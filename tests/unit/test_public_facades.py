from __future__ import annotations

import contract4agents
from contract4agents import expressions, runtime
from contract4agents.assertions import AssertionFailure, evaluate_agent_assertions, evaluate_run_contract
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


def test_assertion_facade_exports_canonical_public_surface() -> None:
    assert AssertionFailure("kind", "assertion", "message").kind == "kind"
    assert callable(evaluate_agent_assertions)
    assert callable(evaluate_run_contract)
    assert contract4agents.evaluate_agent_assertions is evaluate_agent_assertions
    assert contract4agents.evaluate_run_contract is evaluate_run_contract
