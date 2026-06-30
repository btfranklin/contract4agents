from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from contract4agents.ast import (
    AgentDef,
    ContractModule,
    ContractProject,
    DatasourceDef,
    EvalCase,
    FieldDef,
    MonitorDef,
    SourceSpan,
    TypeDef,
    UseDecl,
)
from contract4agents.expressions import (
    ExpressionError,
    ParsedExpression,
    evaluate_hidden_truth,
    evaluate_output,
    evaluate_trace,
    parse_contract_expression,
    parse_expectation,
    parse_monitor_condition,
    parse_monitor_expectation,
    referenced_output_fields,
    referenced_trace_targets,
    referenced_type,
)
from contract4agents.parser import parse_file, parse_project
from contract4agents.semantics import analyze_project

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "ast"
PARSER_GOLDEN = ROOT / "tests" / "fixtures" / "contract_projects" / "parser-golden"

GOLDEN_CASES = (
    ("incident-command", ROOT / "examples" / "incident-command", "incident-command.json"),
    ("ops-desk-lab", ROOT / "tests" / "fixtures" / "contract_projects" / "ops-desk-lab", "ops-desk-lab.json"),
    ("parser-surface-lab", PARSER_GOLDEN / "parser-surface-lab", "parser-surface-lab.json"),
    ("expression-surface-lab", PARSER_GOLDEN / "expression-surface-lab", "expression-surface-lab.json"),
    ("datasource-graph-lab", PARSER_GOLDEN / "datasource-graph-lab", "datasource-graph-lab.json"),
    ("duplicate-visibility-lab", PARSER_GOLDEN / "duplicate-visibility-lab", "duplicate-visibility-lab.json"),
    ("large-team-lab", PARSER_GOLDEN / "large-team-lab", "large-team-lab.json"),
)

SEMANTICALLY_VALID_PARSER_GOLDENS = (
    ("parser-surface-lab", PARSER_GOLDEN / "parser-surface-lab"),
    ("expression-surface-lab", PARSER_GOLDEN / "expression-surface-lab"),
    ("datasource-graph-lab", PARSER_GOLDEN / "datasource-graph-lab"),
    ("large-team-lab", PARSER_GOLDEN / "large-team-lab"),
)


@pytest.mark.parametrize(
    ("project_root", "golden_name"),
    [(project_root, golden_name) for _name, project_root, golden_name in GOLDEN_CASES],
    ids=[name for name, _project_root, _golden_name in GOLDEN_CASES],
)
def test_project_ast_matches_golden(project_root: Path, golden_name: str) -> None:
    assert _project_snapshot(parse_project(project_root)) == _load_golden(golden_name)


@pytest.mark.parametrize(
    "project_root",
    [project_root for _name, project_root in SEMANTICALLY_VALID_PARSER_GOLDENS],
    ids=[name for name, _project_root in SEMANTICALLY_VALID_PARSER_GOLDENS],
)
def test_parser_golden_projects_are_semantically_valid(project_root: Path) -> None:
    result = analyze_project(parse_project(project_root))

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


def test_duplicate_visibility_lab_reports_only_top_level_duplicates() -> None:
    result = analyze_project(parse_project(PARSER_GOLDEN / "duplicate-visibility-lab"))

    assert [(diagnostic.code, diagnostic.message) for diagnostic in result.diagnostics] == [
        ("SEM000", "Duplicate type declaration `DuplicateType`"),
        ("SEM000", "Duplicate agent declaration `DuplicateAgent`"),
        ("SEM000", "Duplicate datasource declaration `DuplicateSource`"),
    ]


def test_public_parser_and_expression_imports_stay_stable() -> None:
    assert callable(parse_file)
    assert callable(parse_project)
    assert ParsedExpression
    assert ExpressionError
    assert callable(parse_expectation)
    assert callable(parse_monitor_condition)
    assert callable(parse_monitor_expectation)
    assert callable(parse_contract_expression)
    assert callable(evaluate_output)
    assert callable(evaluate_hidden_truth)
    assert callable(evaluate_trace)
    assert callable(referenced_output_fields)
    assert callable(referenced_type)
    assert callable(referenced_trace_targets)


def _load_golden(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN / name).read_text())


def _project_snapshot(project: ContractProject) -> dict[str, Any]:
    return {
        "root": _relative_path(project.root),
        "modules": [_module_snapshot(module) for module in project.modules],
    }


def _module_snapshot(module: ContractModule) -> dict[str, Any]:
    return {
        "path": _relative_path(module.path),
        "types": [_type_snapshot(item) for item in module.types],
        "datasources": [_datasource_snapshot(item) for item in module.datasources],
        "agents": [_agent_snapshot(item) for item in module.agents],
        "evals": [_eval_snapshot(item) for item in module.evals],
        "monitors": [_monitor_snapshot(item) for item in module.monitors],
    }


def _type_snapshot(type_def: TypeDef) -> dict[str, Any]:
    return {
        "name": type_def.name,
        "fields": [_field_snapshot(field) for field in type_def.fields],
        "span": _span_snapshot(type_def.span),
    }


def _field_snapshot(field: FieldDef) -> dict[str, Any]:
    return {
        "name": field.name,
        "type_name": field.type_name,
        "nullable": field.nullable,
        "default": field.default,
        "span": _span_snapshot(field.span),
    }


def _datasource_snapshot(datasource: DatasourceDef) -> dict[str, Any]:
    return {
        "name": datasource.name,
        "python": datasource.python,
        "requires": datasource.requires,
        "produces": datasource.produces,
        "render": datasource.render,
        "cache": datasource.cache,
        "span": _span_snapshot(datasource.span),
    }


def _agent_snapshot(agent: AgentDef) -> dict[str, Any]:
    return {
        "name": agent.name,
        "parameters": [_field_snapshot(field) for field in agent.parameters],
        "return_type": agent.return_type,
        "uses": [_use_snapshot(use) for use in agent.uses],
        "attributes": _json_value(agent.attributes),
        "span": _span_snapshot(agent.span),
    }


def _use_snapshot(use: UseDecl) -> dict[str, Any]:
    return {
        "kind": use.kind,
        "name": use.name,
        "source": use.source,
        "permission": use.permission,
        "span": _span_snapshot(use.span),
    }


def _eval_snapshot(eval_case: EvalCase) -> dict[str, Any]:
    return {
        "name": eval_case.name,
        "agent": eval_case.agent,
        "givens": eval_case.givens,
        "expects": eval_case.expects,
        "semantic_expects": eval_case.semantic_expects,
        "span": _span_snapshot(eval_case.span),
    }


def _monitor_snapshot(monitor: MonitorDef) -> dict[str, Any]:
    return {
        "name": monitor.name,
        "agent": monitor.agent,
        "severity": monitor.severity,
        "condition": monitor.condition,
        "expectation": monitor.expectation,
        "span": _span_snapshot(monitor.span),
    }


def _span_snapshot(span: SourceSpan | None) -> dict[str, Any] | None:
    if span is None:
        return None
    return {"path": _relative_path(span.path), "line": span.line, "column": span.column}


def _relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value
