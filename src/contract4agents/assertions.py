"""Host-callable assertion evaluation for compiled Contract4Agents artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from contract4agents.compiler import AgentManifest, CompilerArtifacts
from contract4agents.expressions._eval import evaluate_parsed_expression, evaluate_trace
from contract4agents.expressions._grammar import parse_contract_expression
from contract4agents.expressions._model import ExpressionError, ParsedExpression
from contract4agents.runtime import TraceRecorder, scope_trace

AssertionStatus = Literal["passed", "failed", "skipped"]


@dataclass(frozen=True)
class AssertionFailure:
    kind: str
    assertion: str
    message: str
    agent: str | None = None


@dataclass(frozen=True)
class AssertionCheck:
    assertion: str
    status: AssertionStatus
    failure: AssertionFailure | None = None


@dataclass(frozen=True)
class AgentAssertionResult:
    agent: str
    passed: bool
    checks: list[AssertionCheck] = field(default_factory=list)


@dataclass(frozen=True)
class RunEvaluationResult:
    passed: bool
    agents: list[AgentAssertionResult] = field(default_factory=list)
    failures: list[AssertionFailure] = field(default_factory=list)


def evaluate_agent_assertions(
    *,
    manifest: AgentManifest,
    output: Any,
    trace: TraceRecorder,
    schemas: Mapping[str, dict[str, Any]],
    hidden_truth: Mapping[str, Any] | None = None,
    run_id: str | None = None,
) -> AgentAssertionResult:
    """Evaluate one agent manifest's compiled assertions against output and trace data."""
    agent = str(manifest["agent"])
    if not isinstance(output, dict):
        failure = AssertionFailure(
            "malformed_input",
            "__output__",
            f"Output for `{agent}` must be a dict-like object",
            agent=agent,
        )
        return AgentAssertionResult(agent, False, [AssertionCheck("__output__", "failed", failure)])

    scoped_trace = scope_trace(trace, run_id=run_id, agent=agent)
    checks = [
        _evaluate_assertion(agent, assertion, output, scoped_trace, schemas, hidden_truth or {})
        for assertion in manifest.get("assertions", [])
    ]
    return AgentAssertionResult(agent, all(check.status != "failed" for check in checks), checks)


def evaluate_run_assertions(
    *,
    contract: CompilerArtifacts,
    trace: TraceRecorder,
    outputs: Mapping[str, Any],
    target_agents: Sequence[str] | None = None,
    context: Mapping[str, Any] | None = None,
    hidden_truth: Mapping[str, Any] | None = None,
    run_id: str | None = None,
) -> RunEvaluationResult:
    """Evaluate compiled agent assertions for host-provided outputs and trace events.

    The `context` parameter is reserved for run-contract evaluation work; this first
    host-callable API intentionally verifies completed runs without owning workflow
    control flow.
    """
    _ = context
    manifests = contract["manifests"]
    selected_agents = list(target_agents) if target_agents is not None else list(outputs)
    scoped_trace = scope_trace(trace, run_id=run_id)
    agent_results: list[AgentAssertionResult] = []
    failures: list[AssertionFailure] = []

    for agent in selected_agents:
        manifest = manifests.get(agent)
        if manifest is None:
            failure = AssertionFailure(
                "contract",
                "__manifest__",
                f"Compiled contract does not contain manifest for `{agent}`",
                agent=agent,
            )
            failures.append(failure)
            agent_results.append(
                AgentAssertionResult(agent, False, [AssertionCheck("__manifest__", "failed", failure)])
            )
            continue
        if agent not in outputs:
            failure = AssertionFailure(
                "missing_output",
                "__output__",
                f"No output supplied for `{agent}`",
                agent=agent,
            )
            failures.append(failure)
            agent_results.append(AgentAssertionResult(agent, False, [AssertionCheck("__output__", "failed", failure)]))
            continue
        result = evaluate_agent_assertions(
            manifest=manifest,
            output=outputs[agent],
            trace=scoped_trace,
            schemas=contract["schemas"],
            hidden_truth=hidden_truth,
        )
        agent_results.append(result)
        failures.extend(check.failure for check in result.checks if check.failure is not None)

    return RunEvaluationResult(
        all(agent.passed for agent in agent_results) and not failures,
        agent_results,
        failures,
    )


def _evaluate_assertion(
    agent: str,
    assertion: str,
    output: dict[str, Any],
    trace: TraceRecorder,
    schemas: Mapping[str, dict[str, Any]],
    hidden_truth: Mapping[str, Any],
) -> AssertionCheck:
    try:
        parsed_items = parse_contract_expression(assertion)
    except ExpressionError as exc:
        return _failed(agent, assertion, "unsupported", str(exc))

    if assertion.strip().startswith("when"):
        if len(parsed_items) != 2 or parsed_items[0].kind != "trace":
            return _failed(agent, assertion, "unsupported", f"Unsupported conditional assertion: {assertion}")
        condition_failure = evaluate_trace(parsed_items[0], trace)
        if condition_failure:
            return AssertionCheck(assertion, "skipped")
        return _evaluate_parsed(agent, assertion, parsed_items[1], output, trace, schemas, hidden_truth)

    failures: list[AssertionFailure] = []
    for parsed in parsed_items:
        check = _evaluate_parsed(agent, assertion, parsed, output, trace, schemas, hidden_truth)
        if check.failure is not None:
            failures.append(check.failure)
    if failures:
        return AssertionCheck(assertion, "failed", failures[0])
    return AssertionCheck(assertion, "passed")


def _evaluate_parsed(
    agent: str,
    assertion: str,
    parsed: ParsedExpression,
    output: dict[str, Any],
    trace: TraceRecorder,
    schemas: Mapping[str, dict[str, Any]],
    hidden_truth: Mapping[str, Any],
) -> AssertionCheck:
    failure = evaluate_parsed_expression(
        parsed,
        output=output,
        schemas=dict(schemas),
        trace=trace,
        hidden_truth=dict(hidden_truth),
    )
    return _failed(agent, assertion, failure[0], failure[1]) if failure else AssertionCheck(assertion, "passed")


def _failed(agent: str, assertion: str, kind: str, message: str) -> AssertionCheck:
    return AssertionCheck(assertion, "failed", AssertionFailure(kind, assertion, message, agent=agent))


__all__ = [
    "AgentAssertionResult",
    "AssertionCheck",
    "AssertionFailure",
    "AssertionStatus",
    "RunEvaluationResult",
    "evaluate_agent_assertions",
    "evaluate_run_assertions",
]
