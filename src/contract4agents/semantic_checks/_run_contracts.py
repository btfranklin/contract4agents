"""Run-contract semantic checks."""

from __future__ import annotations

from contract4agents.ast import RunContractDef
from contract4agents.diagnostics import Diagnostic
from contract4agents.expressions._grammar import parse_contract_expression
from contract4agents.expressions._model import ExpressionError
from contract4agents.run_contracts import RunStageDeclaration, parse_run_stage_declaration
from contract4agents.semantic_checks._expressions import check_trace_refs
from contract4agents.semantic_checks._index import ProjectIndex

RUN_CONTRACT_ATTRIBUTES = {"stages", "assertions"}
WORKFLOW_LIKE_ATTRIBUTES = {"branch", "branches", "loop", "loops", "retry", "retries", "checkpoint", "recovery"}


def check_run_contract(run_contract: RunContractDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_check_attributes(run_contract))
    stages = _parse_stages(run_contract, diagnostics)
    diagnostics.extend(_check_stage_refs(run_contract, stages, index))
    diagnostics.extend(_check_run_assertions(run_contract, stages, index))
    return diagnostics


def _check_attributes(run_contract: RunContractDef) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for key, value in run_contract.attributes.items():
        span = run_contract.attribute_spans.get(key, run_contract.span)
        if key not in RUN_CONTRACT_ATTRIBUTES:
            hint = "Run contracts verify host-owned workflow behavior; executable workflow control belongs in Python."
            if key not in WORKFLOW_LIKE_ATTRIBUTES:
                hint = "Accepted run_contract attributes are: `assertions`, `stages`."
            diagnostics.append(
                Diagnostic(
                    "SEM080",
                    f"Unknown run_contract attribute `{key}` on `{run_contract.name}`",
                    span=span,
                    hint=hint,
                )
            )
            continue
        if not isinstance(value, list):
            diagnostics.append(
                Diagnostic(
                    "SEM081",
                    f"Run contract attribute `{key}` on `{run_contract.name}` must be a list",
                    span=span,
                )
            )
    if "stages" not in run_contract.attributes:
        diagnostics.append(
            Diagnostic(
                "SEM082",
                f"Run contract `{run_contract.name}` must declare stages",
                span=run_contract.span,
            )
        )
    return diagnostics


def _parse_stages(run_contract: RunContractDef, diagnostics: list[Diagnostic]) -> list[RunStageDeclaration]:
    stages: list[RunStageDeclaration] = []
    seen: set[str] = set()
    span = run_contract.attribute_spans.get("stages", run_contract.span)
    for raw_stage in run_contract.stages:
        stage = parse_run_stage_declaration(raw_stage)
        if stage is None:
            diagnostics.append(
                Diagnostic(
                    "SEM083",
                    f"Malformed run_contract stage declaration `{raw_stage}` on `{run_contract.name}`",
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
                    f"Run contract `{run_contract.name}` declares stage `{stage.name}` more than once",
                    span=span,
                )
            )
        seen.add(stage.name)
        stages.append(stage)
    return stages


def _check_stage_refs(
    run_contract: RunContractDef,
    stages: list[RunStageDeclaration],
    index: ProjectIndex,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    span = run_contract.attribute_spans.get("stages", run_contract.span)
    for stage in stages:
        if stage.agent not in index.agent_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM084",
                    f"Run contract `{run_contract.name}` stage `{stage.name}` references unknown agent `{stage.agent}`",
                    span=span,
                )
            )
        if stage.output_type not in index.type_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM085",
                    f"Run contract `{run_contract.name}` stage `{stage.name}` references unknown output type "
                    f"`{stage.output_type}`",
                    span=span,
                )
            )
    return diagnostics


def _check_run_assertions(
    run_contract: RunContractDef,
    stages: list[RunStageDeclaration],
    index: ProjectIndex,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    staged_agents = {stage.agent for stage in stages}
    reachable_tools: set[str] = set()
    reachable_hosted_tools: set[str] = set()
    for agent in staged_agents:
        reachable_tools.update(index.reachable_tools(agent))
        reachable_hosted_tools.update(index.reachable_hosted_tools(agent))
    span = run_contract.attribute_spans.get("assertions", run_contract.span)
    for expression in run_contract.assertions:
        try:
            parsed_items = parse_contract_expression(expression)
        except ExpressionError as exc:
            diagnostics.append(Diagnostic("SEM052", str(exc), span=span))
            continue
        for parsed in parsed_items:
            if parsed.kind != "trace":
                diagnostics.append(
                    Diagnostic(
                        "SEM087",
                        f"Run contract `{run_contract.name}` assertion must be a trace expression",
                        span=span,
                    )
                )
                continue
            diagnostics.extend(
                check_trace_refs(
                    parsed,
                    index,
                    reachable_tools,
                    reachable_hosted_tools,
                    span,
                    agent_names=staged_agents,
                    datasource_targets=set(),
                )
            )
    return diagnostics


__all__ = ["check_run_contract"]
