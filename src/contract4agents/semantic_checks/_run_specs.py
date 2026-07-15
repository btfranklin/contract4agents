"""Run spec semantic checks."""

from __future__ import annotations

from contract4agents.ast import RunSpecDef, SourceSpan
from contract4agents.diagnostics import Diagnostic
from contract4agents.expressions._grammar import parse_contract_expression
from contract4agents.expressions._model import ConditionalExpression, ExpressionError, ParsedExpression
from contract4agents.run_specs import (
    RunSpecDerivedValueDeclaration,
    RunSpecStageDeclaration,
    normalize_derived_value_type,
    parse_run_spec_derived_value_declaration,
    parse_run_spec_stage_declaration,
)
from contract4agents.semantic_checks._expressions import check_trace_refs
from contract4agents.semantic_checks._index import ProjectIndex

RUN_SPEC_ATTRIBUTES = {"stages", "assertions", "derived_values"}
WORKFLOW_LIKE_ATTRIBUTES = {"branch", "branches", "loop", "loops", "retry", "retries", "checkpoint", "recovery"}


def check_run_spec(run_spec: RunSpecDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_check_attributes(run_spec))
    stages = _parse_stages(run_spec, diagnostics)
    derived_values = _parse_derived_values(run_spec, diagnostics)
    strict_derived_value_refs = isinstance(run_spec.attributes.get("derived_values"), list)
    diagnostics.extend(_check_stage_refs(run_spec, stages, index))
    diagnostics.extend(_check_run_assertions(run_spec, stages, derived_values, strict_derived_value_refs, index))
    return diagnostics


def _check_attributes(run_spec: RunSpecDef) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for key, value in run_spec.attributes.items():
        span = run_spec.attribute_spans.get(key, run_spec.span)
        if key not in RUN_SPEC_ATTRIBUTES:
            hint = "Run specs verify host-owned workflow behavior; executable workflow control belongs in Python."
            if key not in WORKFLOW_LIKE_ATTRIBUTES:
                hint = "Accepted run spec attributes are: `assertions`, `derived_values`, `stages`."
            diagnostics.append(
                Diagnostic(
                    "SEM080",
                    f"Unknown run spec attribute `{key}` on `{run_spec.name}`",
                    span=span,
                    hint=hint,
                )
            )
            continue
        if not isinstance(value, list):
            diagnostics.append(
                Diagnostic(
                    "SEM081",
                    f"Run spec attribute `{key}` on `{run_spec.name}` must be a list",
                    span=span,
                )
            )
    if "stages" not in run_spec.attributes:
        diagnostics.append(
            Diagnostic(
                "SEM082",
                f"Run spec `{run_spec.name}` must declare stages",
                span=run_spec.span,
            )
        )
    return diagnostics


def _parse_stages(run_spec: RunSpecDef, diagnostics: list[Diagnostic]) -> list[RunSpecStageDeclaration]:
    stages: list[RunSpecStageDeclaration] = []
    seen: set[str] = set()
    span = run_spec.attribute_spans.get("stages", run_spec.span)
    for raw_stage in run_spec.stages:
        stage = parse_run_spec_stage_declaration(raw_stage)
        if stage is None:
            diagnostics.append(
                Diagnostic(
                    "SEM083",
                    f"Malformed run spec stage declaration `{raw_stage}` on `{run_spec.name}`",
                    span=span,
                    hint=(
                        "Expected `stage_name: AgentName -> OutputType`, with optional `?` or `+` "
                        "after the stage name."
                    ),
                )
            )
            continue
        if stage.name in seen:
            diagnostics.append(
                Diagnostic(
                    "SEM086",
                    f"Run spec `{run_spec.name}` declares stage `{stage.name}` more than once",
                    span=span,
                )
            )
        seen.add(stage.name)
        stages.append(stage)
    return stages


def _parse_derived_values(
    run_spec: RunSpecDef,
    diagnostics: list[Diagnostic],
) -> list[RunSpecDerivedValueDeclaration]:
    declarations: list[RunSpecDerivedValueDeclaration] = []
    seen: set[str] = set()
    value = run_spec.attributes.get("derived_values", [])
    if not isinstance(value, list):
        return declarations
    span = run_spec.attribute_spans.get("derived_values", run_spec.span)
    for raw_value in value:
        raw_declaration = raw_value if isinstance(raw_value, str) else str(raw_value)
        declaration = parse_run_spec_derived_value_declaration(raw_declaration)
        if declaration is None:
            diagnostics.append(
                Diagnostic(
                    "SEM088",
                    f"Malformed run spec derived value declaration `{raw_declaration}` on `{run_spec.name}`",
                    span=span,
                    hint=(
                        "Expected `name: string`, `name: integer`, `name: float`, `name: boolean`, "
                        "or a `list[...]` collection."
                    ),
                )
            )
            continue
        if declaration.name in seen:
            diagnostics.append(
                Diagnostic(
                    "SEM089",
                    f"Run spec `{run_spec.name}` declares derived value `{declaration.name}` more than once",
                    span=span,
                )
            )
        seen.add(declaration.name)
        if normalize_derived_value_type(declaration.type_name) is None:
            diagnostics.append(
                Diagnostic(
                    "SEM090",
                    f"Run spec `{run_spec.name}` derived value `{declaration.name}` uses unsupported type "
                    f"`{declaration.type_name}`",
                    span=span,
                    hint=(
                        "Supported derived value types are `string`, `integer`, `float`, `boolean`, "
                        "and their `list[...]` forms."
                    ),
                )
            )
        declarations.append(declaration)
    return declarations


def _check_stage_refs(
    run_spec: RunSpecDef,
    stages: list[RunSpecStageDeclaration],
    index: ProjectIndex,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    span = run_spec.attribute_spans.get("stages", run_spec.span)
    for stage in stages:
        if stage.agent not in index.agent_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM084",
                    f"Run spec `{run_spec.name}` stage `{stage.name}` references unknown agent `{stage.agent}`",
                    span=span,
                )
            )
        if stage.output_type not in index.type_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM085",
                    f"Run spec `{run_spec.name}` stage `{stage.name}` references unknown output type "
                    f"`{stage.output_type}`",
                    span=span,
                )
            )
    return diagnostics


def _check_run_assertions(
    run_spec: RunSpecDef,
    stages: list[RunSpecStageDeclaration],
    derived_values: list[RunSpecDerivedValueDeclaration],
    strict_derived_value_refs: bool,
    index: ProjectIndex,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    staged_agents = {stage.agent for stage in stages}
    stage_names = {stage.name for stage in stages}
    reachable_tools: set[str] = set()
    for agent in staged_agents:
        reachable_tools.update(index.reachable_tools(agent))
    span = run_spec.attribute_spans.get("assertions", run_spec.span)
    declared_value_names = {value.name for value in derived_values}
    for expression in run_spec.assertions:
        try:
            parsed_items = parse_contract_expression(expression)
        except ExpressionError as exc:
            diagnostics.append(Diagnostic("SEM052", str(exc), span=span))
            continue
        for parsed in _iter_run_spec_assertion_items(parsed_items):
            if parsed.kind == "data_relation":
                if strict_derived_value_refs:
                    diagnostics.extend(_check_data_relation_refs(run_spec, parsed, declared_value_names, span))
                continue
            if parsed.kind != "trace":
                diagnostics.append(
                    Diagnostic(
                        "SEM087",
                        f"Run spec `{run_spec.name}` assertion must be a trace or data relation expression",
                        span=span,
                    )
                )
                continue
            diagnostics.extend(
                check_trace_refs(
                    parsed,
                    index,
                    reachable_tools,
                    span,
                    agent_names=staged_agents,
                    datasource_targets=set(),
                    stage_targets=stage_names,
                )
            )
    return diagnostics


def _check_data_relation_refs(
    run_spec: RunSpecDef,
    parsed: ParsedExpression,
    declared_value_names: set[str],
    span: SourceSpan,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for ref in [parsed.left_ref, parsed.right_ref]:
        if ref and ref not in declared_value_names:
            diagnostics.append(
                Diagnostic(
                    "SEM091",
                    f"Run spec `{run_spec.name}` assertion references undeclared derived value `value.{ref}`",
                    span=span,
                    hint=(
                        "Declare the value in `derived_values` or remove the `derived_values` block "
                        "to keep runtime-only names."
                    ),
                )
            )
    return diagnostics


def _iter_run_spec_assertion_items(items: list[ParsedExpression | ConditionalExpression]) -> list[ParsedExpression]:
    parsed: list[ParsedExpression] = []
    for item in items:
        if isinstance(item, ConditionalExpression):
            parsed.extend([item.condition, item.expectation])
        else:
            parsed.append(item)
    return parsed


__all__ = ["check_run_spec"]
