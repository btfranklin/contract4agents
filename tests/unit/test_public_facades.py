from __future__ import annotations

import contract4agents
from contract4agents import expressions, guards, runtime
from contract4agents.assertions import AssertionFailure, evaluate_agent_assertions, evaluate_run_contract
from contract4agents.expressions import ExpressionError, ParsedExpression
from contract4agents.guards import build_guard_plan
from contract4agents.runtime import (
    ContextValue,
    RuntimeContext,
    TraceDiagnostic,
    TraceEvent,
    TraceLoadResult,
    TraceRecorder,
)


def test_expression_facade_exports_canonical_public_surface() -> None:
    assert expressions.ExpressionError is ExpressionError
    assert expressions.ParsedExpression is ParsedExpression
    assert callable(expressions.parse_expectation)
    assert callable(expressions.evaluate_output)
    assert callable(expressions.referenced_trace_targets)


def test_runtime_facade_exports_canonical_public_surface() -> None:
    assert runtime.ContextValue is ContextValue
    assert runtime.RuntimeContext is RuntimeContext
    assert runtime.TraceDiagnostic is TraceDiagnostic
    assert runtime.TraceEvent is TraceEvent
    assert runtime.TraceLoadResult is TraceLoadResult
    assert runtime.TraceRecorder is TraceRecorder
    assert callable(runtime.datasource)
    assert callable(runtime.load_trace_jsonl)
    assert callable(runtime.load_trace_jsonl_with_diagnostics)
    assert callable(runtime.load_python_ref)


def test_assertion_facade_exports_canonical_public_surface() -> None:
    assert AssertionFailure("kind", "assertion", "message").kind == "kind"
    assert callable(evaluate_agent_assertions)
    assert callable(evaluate_run_contract)
    assert contract4agents.evaluate_agent_assertions is evaluate_agent_assertions
    assert contract4agents.evaluate_run_contract is evaluate_run_contract


def test_guard_facade_exports_canonical_public_surface() -> None:
    assert callable(build_guard_plan)
    assert guards.build_guard_plan is build_guard_plan
    assert contract4agents.build_guard_plan is build_guard_plan
