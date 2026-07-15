from __future__ import annotations

import contract4agents
from contract4agents import compiler, expressions, runtime, tracing
from contract4agents.compiler import CompilerArtifacts, compile_project
from contract4agents.expressions import ConditionalExpression, ExpressionError, ParsedExpression
from contract4agents.ir import CanonicalIR
from contract4agents.runtime import InProcessEnvironment
from contract4agents.tracing import NormalizedTrace, TraceEvent


def test_expression_facade_exports_canonical_public_surface() -> None:
    assert expressions.ExpressionError is ExpressionError
    assert expressions.ParsedExpression is ParsedExpression
    assert expressions.ConditionalExpression is ConditionalExpression
    assert callable(expressions.parse_expectation)
    assert callable(expressions.referenced_trace_targets)


def test_compiler_facade_exports_only_the_canonical_compiler_surface() -> None:
    assert compiler.CompilerArtifacts is CompilerArtifacts
    assert compiler.compile_project is compile_project
    assert callable(compiler.build_artifacts)
    assert callable(compiler.write_artifacts)
    assert not hasattr(compiler, "AgentManifest")
    assert not hasattr(compiler, "monitor_pack")
    assert not hasattr(compiler, "build_type_artifacts")


def test_compiler_artifacts_are_ir_owned() -> None:
    assert "ir" in CompilerArtifacts.__dataclass_fields__
    assert CompilerArtifacts.__dataclass_fields__["ir"].type in {"CanonicalIR", CanonicalIR}


def test_runtime_and_tracing_facades_have_distinct_responsibilities() -> None:
    assert runtime.InProcessEnvironment is InProcessEnvironment
    assert tracing.TraceEvent is TraceEvent
    assert tracing.NormalizedTrace is NormalizedTrace
    assert callable(tracing.load_trace_jsonl)


def test_root_facade_omits_removed_v1_apis() -> None:
    assert contract4agents.compile_project is compile_project
    assert not hasattr(contract4agents, "build_guard_plan")
    assert not hasattr(contract4agents, "evaluate_agent_assertions")
