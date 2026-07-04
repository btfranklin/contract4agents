"""Host-callable assertion evaluation for compiled Contract4Agents artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from contract4agents.compiler import AgentManifest, CompilerArtifacts
from contract4agents.compiler._types import RunSpecArtifact, RunSpecDerivedValue, RunSpecStage
from contract4agents.expressions._eval import evaluate_data_relation, evaluate_parsed_expression, evaluate_trace
from contract4agents.expressions._grammar import parse_contract_expression
from contract4agents.expressions._model import ConditionalExpression, ExpressionError, ParsedExpression
from contract4agents.run_specs import derived_value_collection_member_type, normalize_derived_value_type
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
class RunSpecStageCheck:
    stage: str
    status: AssertionStatus
    failures: list[AssertionFailure] = field(default_factory=list)


@dataclass(frozen=True)
class RunSpecEvaluationResult:
    run_spec: str
    passed: bool
    stages: list[RunSpecStageCheck] = field(default_factory=list)
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
    Use `evaluate_run_spec(...)` for stage-output and trace expectations
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


def evaluate_run_spec(
    *,
    contract: CompilerArtifacts,
    run_spec: str,
    trace: TraceRecorder,
    stage_outputs: Mapping[str, Any],
    derived_values: Mapping[str, Any] | None = None,
    run_id: str | None = None,
) -> RunSpecEvaluationResult:
    """Evaluate a compiled run spec against host-emitted trace and stage outputs."""
    artifact = _run_spec_artifact(contract, run_spec)
    if artifact is None:
        failure = AssertionFailure(
            "contract",
            "__run_spec__",
            f"Compiled contract does not contain run spec `{run_spec}`",
        )
        return RunSpecEvaluationResult(run_spec, False, failures=[failure])

    scoped_trace = scope_trace(trace, run_id=run_id)
    stage_checks = [
        _evaluate_run_spec_stage(stage, stage_outputs, contract["schemas"]) for stage in artifact["stages"]
    ]
    derived_value_failures = _evaluate_run_spec_derived_values(artifact, derived_values)
    assertion_checks = [
        _evaluate_run_spec_assertion(assertion, scoped_trace, derived_values) for assertion in artifact["assertions"]
    ]
    failures = [
        failure
        for stage in stage_checks
        for failure in stage.failures
    ]
    failures.extend(derived_value_failures)
    failures.extend(check.failure for check in assertion_checks if check.failure is not None)
    return RunSpecEvaluationResult(
        run_spec,
        not failures,
        stage_checks,
        assertion_checks,
        failures,
    )


def _evaluate_run_spec_derived_values(
    artifact: RunSpecArtifact,
    derived_values: Mapping[str, Any] | None,
) -> list[AssertionFailure]:
    declarations = artifact.get("derived_values", [])
    if not declarations:
        return []
    if derived_values is None:
        return [
            AssertionFailure(
                "derived_value",
                "__derived_values__",
                f"Run spec `{artifact['name']}` declares derived values but no derived_values mapping was supplied",
            )
        ]

    failures: list[AssertionFailure] = []
    for declaration in declarations:
        name = declaration["name"]
        if name not in derived_values:
            failures.append(
                AssertionFailure(
                    "derived_value",
                    "__derived_values__",
                    f"No derived value supplied for `value.{name}` declared as `{declaration['type']}`",
                )
            )
            continue
        type_failure = _derived_value_type_failure(declaration, derived_values[name])
        if type_failure is not None:
            failures.append(AssertionFailure("derived_value", "__derived_values__", type_failure))
    return failures


def _derived_value_type_failure(declaration: RunSpecDerivedValue, value: Any) -> str | None:
    name = declaration["name"]
    type_name = declaration["type"]
    normalized_type = normalize_derived_value_type(type_name)
    if normalized_type is None:
        return f"Run spec derived value `value.{name}` has unsupported compiled type `{type_name}`"

    member_type = derived_value_collection_member_type(normalized_type)
    if member_type is None:
        if _matches_derived_scalar_type(value, normalized_type):
            return None
        return (
            f"Derived value `value.{name}` declared as `{normalized_type}` must be {normalized_type}, "
            f"found {_derived_value_runtime_type(value)}"
        )

    if not _is_derived_value_sequence(value):
        return (
            f"Derived value `value.{name}` declared as `{normalized_type}` must be a sequence of "
            f"{member_type} values"
        )
    for index, item in enumerate(value):
        if not _matches_derived_scalar_type(item, member_type):
            return (
                f"Derived value `value.{name}` item at index {index} must be {member_type}, "
                f"found {_derived_value_runtime_type(item)}"
            )
    return None


def _is_derived_value_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray | Mapping)


def _matches_derived_scalar_type(value: Any, type_name: str) -> bool:
    if type_name == "str":
        return isinstance(value, str)
    if type_name == "bool":
        return type(value) is bool
    if type_name == "int":
        return type(value) is int
    if type_name == "float":
        return type(value) is float
    return False


def _derived_value_runtime_type(value: Any) -> str:
    if isinstance(value, Mapping):
        return "mapping"
    if _is_derived_value_sequence(value):
        return "sequence"
    if isinstance(value, str):
        return "str"
    if type(value) is bool:
        return "bool"
    if type(value) is int:
        return "int"
    if type(value) is float:
        return "float"
    if value is None:
        return "null"
    return type(value).__name__


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

    failures: list[AssertionFailure] = []
    for parsed in parsed_items:
        check = (
            _evaluate_conditional(agent, assertion, parsed, output, trace, schemas, hidden_truth)
            if isinstance(parsed, ConditionalExpression)
            else _evaluate_parsed(agent, assertion, parsed, output, trace, schemas, hidden_truth)
        )
        if check.status == "skipped":
            return check
        if check.failure is not None:
            failures.append(check.failure)
    if failures:
        return AssertionCheck(assertion, "failed", failures[0])
    return AssertionCheck(assertion, "passed")


def _evaluate_run_spec_stage(
    stage: RunSpecStage,
    stage_outputs: Mapping[str, Any],
    schemas: Mapping[str, dict[str, Any]],
) -> RunSpecStageCheck:
    stage_name = stage["name"]
    if stage_name not in stage_outputs:
        if stage["cardinality"] == "optional":
            return RunSpecStageCheck(stage_name, "skipped")
        failure = AssertionFailure(
            "missing_stage_output",
            "__stage_output__",
            f"No output supplied for run spec stage `{stage_name}`",
        )
        return RunSpecStageCheck(stage_name, "failed", [failure])

    value = stage_outputs[stage_name]
    if stage["cardinality"] == "many":
        if not _is_output_sequence(value) or not value:
            cardinality_failure = AssertionFailure(
                "malformed_stage_output",
                "__stage_output__",
                f"Stage `{stage_name}` expects one or more outputs",
            )
            return RunSpecStageCheck(stage_name, "failed", [cardinality_failure])
        schema_failures: list[AssertionFailure] = []
        for item in value:
            schema_failure = _stage_schema_failure(stage_name, stage["output_type"], item, schemas)
            if schema_failure is not None:
                schema_failures.append(schema_failure)
        return RunSpecStageCheck(stage_name, "failed" if schema_failures else "passed", schema_failures)

    schema_failure = _stage_schema_failure(stage_name, stage["output_type"], value, schemas)
    return RunSpecStageCheck(
        stage_name,
        "failed" if schema_failure else "passed",
        [schema_failure] if schema_failure else [],
    )


def _evaluate_run_spec_assertion(
    assertion: str,
    trace: TraceRecorder,
    derived_values: Mapping[str, Any] | None,
) -> AssertionCheck:
    try:
        parsed_items = parse_contract_expression(assertion)
    except ExpressionError as exc:
        return AssertionCheck(assertion, "failed", AssertionFailure("unsupported", assertion, str(exc)))

    failures: list[AssertionFailure] = []
    for parsed in parsed_items:
        if isinstance(parsed, ConditionalExpression):
            check = _evaluate_run_spec_conditional(assertion, parsed, trace, derived_values)
            if check.status == "skipped":
                return check
            if check.failure is not None:
                failures.append(check.failure)
            continue
        if parsed.kind == "trace":
            trace_failure = evaluate_trace(parsed, trace)
            if trace_failure:
                failures.append(AssertionFailure("trace", assertion, trace_failure))
            continue
        if parsed.kind == "data_relation":
            data_failure = evaluate_data_relation(parsed, derived_values)
            if data_failure:
                failures.append(AssertionFailure("data_relation", assertion, data_failure))
            continue
        failures.append(AssertionFailure("unsupported", assertion, f"Unsupported run spec assertion: {assertion}"))
    if failures:
        return AssertionCheck(assertion, "failed", failures[0])
    return AssertionCheck(assertion, "passed")


def _evaluate_conditional(
    agent: str,
    assertion: str,
    parsed: ConditionalExpression,
    output: dict[str, Any],
    trace: TraceRecorder,
    schemas: Mapping[str, dict[str, Any]],
    hidden_truth: Mapping[str, Any],
) -> AssertionCheck:
    if parsed.condition.kind != "trace":
        return _failed(agent, assertion, "unsupported", f"Unsupported conditional assertion: {assertion}")
    condition_failure = evaluate_trace(parsed.condition, trace)
    if condition_failure:
        return AssertionCheck(assertion, "skipped")
    return _evaluate_parsed(agent, assertion, parsed.expectation, output, trace, schemas, hidden_truth)


def _evaluate_run_spec_conditional(
    assertion: str,
    parsed: ConditionalExpression,
    trace: TraceRecorder,
    derived_values: Mapping[str, Any] | None,
) -> AssertionCheck:
    if parsed.condition.kind != "trace" or parsed.expectation.kind not in {"trace", "data_relation"}:
        failure = AssertionFailure("unsupported", assertion, f"Unsupported run spec assertion: {assertion}")
        return AssertionCheck(assertion, "failed", failure)
    condition_failure = evaluate_trace(parsed.condition, trace)
    if condition_failure:
        return AssertionCheck(assertion, "skipped")
    if parsed.expectation.kind == "trace":
        trace_failure = evaluate_trace(parsed.expectation, trace)
        if trace_failure:
            return AssertionCheck(assertion, "failed", AssertionFailure("trace", assertion, trace_failure))
    else:
        data_failure = evaluate_data_relation(parsed.expectation, derived_values)
        if data_failure:
            return AssertionCheck(assertion, "failed", AssertionFailure("data_relation", assertion, data_failure))
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


def _run_spec_artifact(
    contract: CompilerArtifacts,
    run_spec: str,
) -> RunSpecArtifact | None:
    return next((item for item in contract["run_specs"] if item["name"] == run_spec), None)


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
    "RunSpecEvaluationResult",
    "RunSpecStageCheck",
    "RunEvaluationResult",
    "evaluate_agent_assertions",
    "evaluate_run_spec",
    "evaluate_run_assertions",
]
