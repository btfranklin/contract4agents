from __future__ import annotations

import contract4agents
from contract4agents import adapters, compiler, expressions, guards, pydantic_interop, runtime
from contract4agents.adapters.openai import (
    OpenAIAdapterPlan,
    OpenAIAgentPlan,
    OpenAIToolRegistration,
    build_openai_agents_from_plan,
    build_openai_output_type_registry,
    plan_openai_agents_from_contracts,
    run_openai_agent_with_contract,
)
from contract4agents.assertions import (
    AssertionFailure,
    evaluate_agent_assertions,
    evaluate_run_assertions,
    evaluate_run_contract,
)
from contract4agents.compiler import AgentManifest, CompilerArtifacts, compile_project
from contract4agents.expressions import ExpressionError, ParsedExpression
from contract4agents.guards import build_guard_plan
from contract4agents.pydantic_interop import PydanticSchemaError, schema_from_pydantic_type
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


def test_compiler_facade_exports_canonical_public_surface() -> None:
    assert compiler.AgentManifest is AgentManifest
    assert compiler.CompilerArtifacts is CompilerArtifacts
    assert compiler.compile_project is compile_project
    assert callable(compiler.build_artifacts)
    assert callable(compiler.generated_docs)
    assert callable(compiler.write_artifacts)


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
    assert callable(evaluate_run_assertions)
    assert callable(evaluate_run_contract)
    assert contract4agents.evaluate_agent_assertions is evaluate_agent_assertions
    assert contract4agents.evaluate_run_assertions is evaluate_run_assertions
    assert contract4agents.evaluate_run_contract is evaluate_run_contract


def test_guard_facade_exports_canonical_public_surface() -> None:
    assert callable(build_guard_plan)
    assert guards.build_guard_plan is build_guard_plan
    assert contract4agents.build_guard_plan is build_guard_plan


def test_pydantic_interop_facade_exports_canonical_public_surface() -> None:
    assert pydantic_interop.PydanticSchemaError is PydanticSchemaError
    assert callable(pydantic_interop.is_python_import_ref)
    assert callable(pydantic_interop.python_type_ref_diagnostics)
    assert pydantic_interop.schema_from_pydantic_type is schema_from_pydantic_type


def test_openai_adapter_facade_exports_planning_surface() -> None:
    assert adapters.OpenAIAdapterPlan is OpenAIAdapterPlan
    assert adapters.OpenAIAgentPlan is OpenAIAgentPlan
    assert adapters.OpenAIToolRegistration is OpenAIToolRegistration
    assert adapters.plan_openai_agents_from_contracts is plan_openai_agents_from_contracts
    assert adapters.build_openai_agents_from_plan is build_openai_agents_from_plan
    assert adapters.build_openai_output_type_registry is build_openai_output_type_registry
    assert adapters.run_openai_agent_with_contract is run_openai_agent_with_contract
