"""Eval runner primitives for output checks, trace spies, and semantic checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from contract4agents.expressions._eval import evaluate_hidden_truth, evaluate_output, evaluate_trace
from contract4agents.expressions._grammar import parse_expectation, parse_semantic_expectation
from contract4agents.expressions._model import ExpressionError
from contract4agents.runtime import TraceRecorder, scope_trace


class SemanticJudge(Protocol):
    async def judge(self, *, output: dict[str, Any], criterion: str) -> bool: ...


@dataclass
class EvalFailure:
    kind: str
    message: str


@dataclass
class EvalResult:
    name: str
    passed: bool
    failures: list[EvalFailure] = field(default_factory=list)
    skipped_semantic: list[str] = field(default_factory=list)


class EvalRunner:
    def __init__(self, schemas: dict[str, dict[str, Any]], judge: SemanticJudge | None = None) -> None:
        self.schemas = schemas
        self.judge = judge

    async def evaluate(
        self,
        *,
        name: str,
        output: dict[str, Any],
        output_type: str,
        trace: TraceRecorder,
        expectations: list[str],
        semantic_expectations: list[str] | None = None,
        hidden_truth: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> EvalResult:
        result = EvalResult(name, True)
        scoped_trace = scope_trace(trace, run_id=run_id)
        for expectation in expectations:
            failure = self._check_expectation(expectation, output, output_type, scoped_trace, hidden_truth or {})
            if failure:
                result.failures.append(failure)
        for criterion in semantic_expectations or []:
            try:
                parsed_criterion = parse_semantic_expectation(criterion)
            except ExpressionError as exc:
                result.failures.append(EvalFailure("unsupported", str(exc)))
                continue
            if not self.judge:
                result.skipped_semantic.append(criterion)
                continue
            ok = await self.judge.judge(output=output, criterion=str(parsed_criterion.value))
            if not ok:
                result.failures.append(EvalFailure("semantic", f"Semantic expectation failed: {criterion}"))
        result.passed = not result.failures
        return result

    def _check_expectation(
        self,
        expression: str,
        output: dict[str, Any],
        output_type: str,
        trace: TraceRecorder,
        hidden_truth: dict[str, Any],
    ) -> EvalFailure | None:
        try:
            parsed = parse_expectation(expression)
        except ExpressionError as exc:
            return EvalFailure("unsupported", str(exc))
        if parsed.kind.startswith("output"):
            failure = evaluate_output(parsed, output, self.schemas)
            return EvalFailure("output", failure) if failure else None
        if parsed.kind == "trace":
            failure = evaluate_trace(parsed, trace)
            return EvalFailure("trace", failure) if failure else None
        if parsed.kind == "hidden_truth":
            failure = evaluate_hidden_truth(parsed, output, hidden_truth)
            return EvalFailure("hidden_truth", failure) if failure else None
        return EvalFailure("unsupported", f"Unsupported expectation: {expression}")
