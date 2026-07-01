"""Eval, monitor, and expression-reference semantic checks."""

from __future__ import annotations

from contract4agents.ast import MonitorDef, SourceSpan
from contract4agents.diagnostics import Diagnostic
from contract4agents.expressions._grammar import (
    parse_contract_expression,
    parse_expectation,
    parse_monitor_condition,
    parse_monitor_expectation,
    parse_semantic_expectation,
)
from contract4agents.expressions._model import ExpressionError, ParsedExpression
from contract4agents.expressions._refs import referenced_output_fields, referenced_trace_targets, referenced_type
from contract4agents.expressions._trace_ops import TRACE_OPS
from contract4agents.semantic_checks._index import ProjectIndex


def check_eval(
    agent_name: str,
    expects: list[str],
    semantic_expects: list[str],
    index: ProjectIndex,
) -> list[Diagnostic]:
    agent = index.agent_defs.get(agent_name)
    if not agent:
        return [Diagnostic("SEM040", f"Eval references unknown agent `{agent_name}`")]
    diagnostics: list[Diagnostic] = []
    for expression in expects:
        diagnostics.extend(
            check_expression_refs(
                expression,
                agent.name,
                agent.return_type,
                index,
                index.project_tools,
                index.project_hosted_tools,
                span=agent.span,
                contract_expression=False,
            )
        )
    for expression in semantic_expects:
        try:
            parse_semantic_expectation(expression)
        except ExpressionError as exc:
            diagnostics.append(Diagnostic("SEM056", str(exc), span=agent.span))
    return diagnostics


def check_monitor(
    rule: MonitorDef,
    index: ProjectIndex,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    agent = index.agent_defs.get(rule.agent)
    if agent is None:
        diagnostics.append(Diagnostic("SEM030", f"Monitor references unknown agent `{rule.agent}`", span=rule.span))
    for expression, parser in [
        (rule.condition, parse_monitor_condition),
        (rule.expectation, parse_monitor_expectation),
    ]:
        try:
            parsed = parser(expression)
        except ExpressionError as exc:
            diagnostics.append(Diagnostic("SEM052", str(exc), span=rule.span))
            continue
        if parsed:
            diagnostics.extend(
                check_trace_refs(parsed, index, index.project_tools, index.project_hosted_tools, rule.span)
            )
    return diagnostics


def check_expression_refs(
    expression: str,
    agent_name: str,
    return_type_name: str,
    index: ProjectIndex,
    tool_names: set[str],
    hosted_tool_names: set[str],
    *,
    span: SourceSpan,
    contract_expression: bool,
) -> list[Diagnostic]:
    try:
        parsed_items = parse_contract_expression(expression) if contract_expression else [parse_expectation(expression)]
    except ExpressionError as exc:
        return [Diagnostic("SEM052", str(exc), span=span)]
    diagnostics: list[Diagnostic] = []
    for parsed in parsed_items:
        diagnostics.extend(
            _check_parsed_expression(
                parsed,
                agent_name,
                return_type_name,
                index,
                tool_names,
                hosted_tool_names,
                span,
            )
        )
    return diagnostics


def _check_parsed_expression(
    parsed: ParsedExpression,
    agent_name: str,
    return_type_name: str,
    index: ProjectIndex,
    tool_names: set[str],
    hosted_tool_names: set[str],
    span: SourceSpan,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    type_name = referenced_type(parsed)
    if type_name and type_name not in index.type_defs:
        diagnostics.append(Diagnostic("SEM002", f"Unknown type `{type_name}` in expression", span=span))
    return_type = index.type_defs.get(return_type_name)
    if return_type and return_type.source == "native":
        output_fields = {field.name for field in return_type.fields}
        for field in referenced_output_fields(parsed):
            if field not in output_fields:
                diagnostics.append(
                    Diagnostic(
                        "SEM050",
                        f"Expression references unknown output field `{field}` on `{return_type_name}`",
                        span=span,
                    )
                )
    diagnostics.extend(check_trace_refs(parsed, index, tool_names, hosted_tool_names, span))
    return diagnostics


def check_trace_refs(
    parsed: ParsedExpression,
    index: ProjectIndex,
    tool_names: set[str],
    hosted_tool_names: set[str],
    span: SourceSpan,
) -> list[Diagnostic]:
    op, targets = referenced_trace_targets(parsed)
    if not op:
        return []
    diagnostics: list[Diagnostic] = []
    spec = TRACE_OPS[op]
    for target in targets:
        if target.isdigit():
            continue
        if spec.target_kind == "agent" and target not in index.agent_names:
            diagnostics.append(Diagnostic("SEM051", f"Expression references unknown agent `{target}`", span=span))
        elif spec.target_kind == "tool" and target not in tool_names:
            diagnostics.append(Diagnostic("SEM053", f"Expression references unknown tool `{target}`", span=span))
        elif spec.target_kind == "hosted_tool" and target not in hosted_tool_names:
            diagnostics.append(
                Diagnostic("SEM055", f"Expression references unknown hosted tool `{target}`", span=span)
            )
        elif spec.target_kind == "approval_tool" and target not in tool_names:
            diagnostics.append(
                Diagnostic("SEM053", f"Expression references approval for unknown tool `{target}`", span=span)
            )
        elif spec.target_kind == "datasource" and target not in index.datasource_targets:
            diagnostics.append(
                Diagnostic("SEM054", f"Expression references unknown datasource target `{target}`", span=span)
            )
        elif spec.target_kind == "any":
            known_targets = index.agent_names | tool_names | hosted_tool_names | index.datasource_targets
            if target not in known_targets:
                diagnostics.append(
                    Diagnostic("SEM051", f"Expression references unknown trace target `{target}`", span=span)
                )
    return diagnostics


__all__ = ["check_eval", "check_expression_refs", "check_monitor", "check_trace_refs"]
