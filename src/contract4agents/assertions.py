"""Host-callable assertion evaluation for compiled Contract4Agents artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from contract4agents.compiler import AgentManifest, CompilerArtifacts
from contract4agents.compiler._types import RunContractArtifact, RunContractStage
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


@dataclass(frozen=True)
class RunContractStageCheck:
    stage: str
    status: AssertionStatus
    failures: list[AssertionFailure] = field(default_factory=list)


@dataclass(frozen=True)
class RunContractEvaluationResult:
    run_contract: str
    passed: bool
    stages: list[RunContractStageCheck] = field(default_factory=list)
    assertions: list[AssertionCheck] = field(default_factory=list)
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

    The `context` parameter is reserved for host-provided assertion context.
    Use `evaluate_run_contract(...)` for stage-output and trace expectations
    across a host-owned multi-agent run.
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


def evaluate_run_contract(
    *,
    contract: CompilerArtifacts,
    run_contract: str,
    trace: TraceRecorder,
    stage_outputs: Mapping[str, Any],
    run_id: str | None = None,
) -> RunContractEvaluationResult:
    """Evaluate a compiled run contract against host-emitted trace and stage outputs."""
    artifact = _run_contract_artifact(contract, run_contract)
    if artifact is None:
        failure = AssertionFailure(
            "contract",
            "__run_contract__",
            f"Compiled contract does not contain run_contract `{run_contract}`",
        )
        return RunContractEvaluationResult(run_contract, False, failures=[failure])

    scoped_trace = scope_trace(trace, run_id=run_id)
    stage_checks = [
        _evaluate_run_contract_stage(stage, stage_outputs, contract["schemas"]) for stage in artifact["stages"]
    ]
    assertion_checks = [
        _evaluate_run_contract_assertion(assertion, scoped_trace) for assertion in artifact["assertions"]
    ]
    failures = [
        failure
        for stage in stage_checks
        for failure in stage.failures
    ]
    failures.extend(check.failure for check in assertion_checks if check.failure is not None)
    return RunContractEvaluationResult(
        run_contract,
        not failures,
        stage_checks,
        assertion_checks,
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


def _evaluate_run_contract_stage(
    stage: RunContractStage,
    stage_outputs: Mapping[str, Any],
    schemas: Mapping[str, dict[str, Any]],
) -> RunContractStageCheck:
    stage_name = stage["name"]
    if stage_name not in stage_outputs:
        if stage["cardinality"] == "optional":
            return RunContractStageCheck(stage_name, "skipped")
        failure = AssertionFailure(
            "missing_stage_output",
            "__stage_output__",
            f"No output supplied for run_contract stage `{stage_name}`",
        )
        return RunContractStageCheck(stage_name, "failed", [failure])

    value = stage_outputs[stage_name]
    if stage["cardinality"] == "many":
        if not _is_output_sequence(value) or not value:
            cardinality_failure = AssertionFailure(
                "malformed_stage_output",
                "__stage_output__",
                f"Stage `{stage_name}` expects one or more outputs",
            )
            return RunContractStageCheck(stage_name, "failed", [cardinality_failure])
        schema_failures: list[AssertionFailure] = []
        for item in value:
            schema_failure = _stage_schema_failure(stage_name, stage["output_type"], item, schemas)
            if schema_failure is not None:
                schema_failures.append(schema_failure)
        return RunContractStageCheck(stage_name, "failed" if schema_failures else "passed", schema_failures)

    schema_failure = _stage_schema_failure(stage_name, stage["output_type"], value, schemas)
    return RunContractStageCheck(
        stage_name,
        "failed" if schema_failure else "passed",
        [schema_failure] if schema_failure else [],
    )


def _evaluate_run_contract_assertion(assertion: str, trace: TraceRecorder) -> AssertionCheck:
    try:
        parsed_items = parse_contract_expression(assertion)
    except ExpressionError as exc:
        return AssertionCheck(assertion, "failed", AssertionFailure("unsupported", assertion, str(exc)))

    if assertion.strip().startswith("when"):
        if len(parsed_items) != 2 or any(parsed.kind != "trace" for parsed in parsed_items):
            failure = AssertionFailure("unsupported", assertion, f"Unsupported run_contract assertion: {assertion}")
            return AssertionCheck(assertion, "failed", failure)
        condition_failure = evaluate_trace(parsed_items[0], trace)
        if condition_failure:
            return AssertionCheck(assertion, "skipped")
        trace_failure = evaluate_trace(parsed_items[1], trace)
        if trace_failure:
            return AssertionCheck(assertion, "failed", AssertionFailure("trace", assertion, trace_failure))
        return AssertionCheck(assertion, "passed")

    failures: list[AssertionFailure] = []
    for parsed in parsed_items:
        if parsed.kind != "trace":
            failures.append(
                AssertionFailure("unsupported", assertion, f"Unsupported run_contract assertion: {assertion}")
            )
            continue
        trace_failure = evaluate_trace(parsed, trace)
        if trace_failure:
            failures.append(AssertionFailure("trace", assertion, trace_failure))
    if failures:
        return AssertionCheck(assertion, "failed", failures[0])
    return AssertionCheck(assertion, "passed")


def _stage_schema_failure(
    stage_name: str,
    output_type: str,
    value: Any,
    schemas: Mapping[str, dict[str, Any]],
) -> AssertionFailure | None:
    schema = schemas.get(output_type)
    if schema is None:
        return AssertionFailure("contract", "__stage_output__", f"Unknown output schema `{output_type}`")
    try:
        from jsonschema import validate

        validate(value, schema)
    except Exception as exc:  # noqa: BLE001 - jsonschema exposes several validation exception types.
        return AssertionFailure(
            "stage_schema",
            "__stage_output__",
            f"Stage `{stage_name}` output does not conform to {output_type}: {exc}",
        )
    return None


def _is_output_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _run_contract_artifact(
    contract: CompilerArtifacts,
    run_contract: str,
) -> RunContractArtifact | None:
    return next((item for item in contract["run_contracts"] if item["name"] == run_contract), None)


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
    "RunContractEvaluationResult",
    "RunContractStageCheck",
    "RunEvaluationResult",
    "evaluate_agent_assertions",
    "evaluate_run_contract",
    "evaluate_run_assertions",
]
