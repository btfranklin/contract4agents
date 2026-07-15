"""Semantic checks for contract declarations."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from contract4agents.ast import (
    AgentDef,
    CompositionDef,
    ControlDef,
    ExternalContextDef,
    IsolationDef,
    OperationalControlDef,
    QualityDef,
    ToolDef,
)
from contract4agents.diagnostics import Diagnostic
from contract4agents.parser._values import unquote
from contract4agents.semantic_checks._index import ProjectIndex
from contract4agents.semantic_checks._types import check_type_ref

AUDIENCES = {"model", "adapter", "host", "evaluator", "reviewer"}
ASSESSMENTS = {"static", "adapter", "runtime", "host_attested", "post_run", "semantic", "advisory"}
SEVERITIES = {"low", "medium", "high", "critical"}
ISOLATION_DIMENSIONS = {
    "context": {"explicit_only", "inherited"},
    "capabilities": {"declared_only", "inherited"},
    "state": {"fresh", "shared"},
    "filesystem": {"none", "ephemeral", "inherited_read_only", "inherited"},
    "network": {"denied", "allowlisted", "inherited"},
    "secrets": {"none", "declared_only", "inherited"},
    "return": {"final_output_only", "full_trace"},
}
_EXECUTION_BOUNDARY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")


def check_tool(tool: ToolDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics = _check_parameters(tool.parameters, index, f"tool `{tool.name}`")
    diagnostics.extend(check_type_ref(tool.return_type, index, tool.span, f"tool `{tool.name}` return type"))
    if not tool.description:
        diagnostics.append(Diagnostic("SEM100", f"Tool `{tool.name}` requires a description", span=tool.span))
    if tool.side_effect is None:
        diagnostics.append(
            Diagnostic("SEM133", f"Tool `{tool.name}` requires boolean `side_effect` metadata", span=tool.span)
        )
    return diagnostics


def check_external_context(item: ExternalContextDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics = check_type_ref(item.type_name, index, item.span, f"external context `{item.name}`")
    if item.sensitivity not in {"public", "internal", "confidential", "restricted"}:
        diagnostics.append(
            Diagnostic(
                "SEM101",
                f"External context `{item.name}` has invalid sensitivity `{item.sensitivity}`",
                span=item.span,
            )
        )
    if item.render not in {"markdown", "json", "text"}:
        diagnostics.append(
            Diagnostic(
                "SEM102", f"External context `{item.name}` has invalid render mode `{item.render}`", span=item.span
            )
        )
    return diagnostics


def check_agent_contract(agent: AgentDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    seen_grants: set[str] = set()
    for grant in agent.grants:
        if grant.capability in seen_grants:
            diagnostics.append(
                Diagnostic(
                    "SEM103",
                    f"Agent `{agent.name}` grants capability `{grant.capability}` more than once",
                    span=grant.span,
                )
            )
        seen_grants.add(grant.capability)
        if grant.capability not in index.tool_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM104",
                    f"Agent `{agent.name}` grants unknown tool `{grant.capability}`",
                    span=grant.span,
                )
            )
        if grant.availability not in {"enabled", "denied"}:
            diagnostics.append(
                Diagnostic(
                    "SEM105",
                    f"Grant `{agent.name}:{grant.capability}` requires availability `enabled` or `denied`",
                    span=grant.span,
                )
            )
            continue
        if grant.availability == "denied":
            if grant.authorization is not None or grant.execution is not None or grant.isolation is not None:
                diagnostics.append(
                    Diagnostic(
                        "SEM106",
                        f"Denied grant `{agent.name}:{grant.capability}` cannot declare authorization, "
                        "execution, or isolation",
                        span=grant.span,
                    )
                )
            continue
        if grant.authorization not in {"preapproved", "approval_required"}:
            diagnostics.append(
                Diagnostic(
                    "SEM107",
                    f"Enabled grant `{agent.name}:{grant.capability}` requires explicit authorization",
                    span=grant.span,
                )
            )
        if (
            grant.execution is None
            or _EXECUTION_BOUNDARY.fullmatch(grant.execution) is None
        ):
            diagnostics.append(
                Diagnostic(
                    "SEM108",
                    f"Enabled grant `{agent.name}:{grant.capability}` requires a valid execution boundary",
                    span=grant.span,
                )
            )
        if grant.isolation is not None and grant.isolation not in index.isolation_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM109",
                    f"Grant `{agent.name}:{grant.capability}` references unknown isolation `{grant.isolation}`",
                    span=grant.span,
                )
            )

    seen_context: set[str] = set()
    parameter_names = {parameter.name for parameter in agent.parameters}
    for requirement in agent.context:
        if requirement.name in parameter_names or requirement.name in seen_context:
            diagnostics.append(
                Diagnostic(
                    "SEM110",
                    f"Agent `{agent.name}` declares context slot `{requirement.name}` more than once",
                    span=requirement.span,
                )
            )
        seen_context.add(requirement.name)
        diagnostics.extend(
            check_type_ref(requirement.type_name, index, requirement.span, f"context `{agent.name}.{requirement.name}`")
        )
        if requirement.origin not in {"datasource", "external"}:
            diagnostics.append(
                Diagnostic(
                    "SEM145",
                    f"Agent-local context `{agent.name}.{requirement.name}` must use a datasource or external origin",
                    span=requirement.span,
                    hint=(
                        "Invocation, parent, handoff, and stage provenance belongs in agent signatures, "
                        "composition mappings, or run specs."
                    ),
                )
            )
        if requirement.origin in {"datasource", "external"} and not requirement.source:
            diagnostics.append(
                Diagnostic(
                    "SEM111",
                    f"Context `{agent.name}.{requirement.name}` requires a named {requirement.origin} source",
                    span=requirement.span,
                )
            )
        if requirement.origin == "datasource" and requirement.source not in index.datasource_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM112",
                    f"Context `{agent.name}.{requirement.name}` references unknown datasource `{requirement.source}`",
                    span=requirement.span,
                )
            )
        elif requirement.origin == "datasource" and requirement.source:
            datasource = index.datasource_defs[requirement.source]
            if datasource.return_type != requirement.type_name:
                diagnostics.append(
                    Diagnostic(
                        "SEM134",
                        f"Context `{agent.name}.{requirement.name}` type `{requirement.type_name}` does not match "
                        f"datasource `{requirement.source}` output `{datasource.return_type}`",
                        span=requirement.span,
                    )
                )
        if requirement.origin == "external" and requirement.source not in index.external_context_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM113",
                    f"Context `{agent.name}.{requirement.name}` references unknown external context "
                    f"`{requirement.source}`",
                    span=requirement.span,
                )
            )
        elif requirement.origin == "external" and requirement.source:
            external = index.external_context_defs[requirement.source]
            if external.type_name != requirement.type_name:
                diagnostics.append(
                    Diagnostic(
                        "SEM135",
                        f"Context `{agent.name}.{requirement.name}` type `{requirement.type_name}` does not match "
                        f"external context `{requirement.source}` output `{external.type_name}`",
                        span=requirement.span,
                    )
                )
        if requirement.origin == "datasource" and requirement.source in index.datasource_defs:
            datasource = index.datasource_defs[requirement.source]
            expected = {parameter.name for parameter in datasource.parameters}
            configured = set(requirement.mappings)
            unknown = sorted(configured - expected)
            missing = sorted(expected - configured)
            if unknown:
                diagnostics.append(
                    Diagnostic(
                        "SEM140",
                        f"Context `{agent.name}.{requirement.name}` maps unknown datasource inputs: "
                        f"{', '.join(unknown)}",
                        span=requirement.span,
                    )
                )
            if missing:
                diagnostics.append(
                    Diagnostic(
                        "SEM141",
                        f"Context `{agent.name}.{requirement.name}` is missing datasource input mappings: "
                        f"{', '.join(missing)}",
                        span=requirement.span,
                    )
                )
            for parameter in datasource.parameters:
                expression = requirement.mappings.get(parameter.name)
                if expression is None:
                    continue
                resolved_type = _resolve_context_mapping_type(expression, agent, requirement, index)
                if resolved_type is None:
                    diagnostics.append(
                        Diagnostic(
                            "SEM142",
                            f"Context `{agent.name}.{requirement.name}` has invalid input mapping "
                            f"`{parameter.name} = {expression}`",
                            span=requirement.span,
                            hint=(
                                "Use an invocation path such as `input.request.account_id` or a previously "
                                "declared context path such as `context.account.id`."
                            ),
                        )
                    )
                elif resolved_type != _field_type(parameter):
                    diagnostics.append(
                        Diagnostic(
                            "SEM143",
                            f"Context `{agent.name}.{requirement.name}` maps `{parameter.name}` "
                            f"from `{resolved_type}` but the datasource requires `{_field_type(parameter)}`",
                            span=requirement.span,
                        )
                    )
        elif requirement.mappings:
            diagnostics.append(
                Diagnostic(
                    "SEM144",
                    f"Context `{agent.name}.{requirement.name}` can map inputs only for a datasource origin",
                    span=requirement.span,
                )
            )
    return diagnostics


def _resolve_context_mapping_type(
    expression: str,
    agent: AgentDef,
    requirement: Any,
    index: ProjectIndex,
) -> str | None:
    parts = expression.split(".")
    if len(parts) < 2 or parts[0] not in {"input", "context"} or any(not part for part in parts):
        return None
    source: Any
    if parts[0] == "input":
        source = next((item for item in agent.parameters if item.name == parts[1]), None)
    else:
        position = agent.context.index(requirement)
        source = next((item for item in agent.context[:position] if item.name == parts[1]), None)
    if source is None:
        return None
    current = _field_type(source)
    for name in parts[2:]:
        owner = current.rstrip("?")
        type_def = index.type_defs.get(owner)
        if type_def is None:
            return None
        field = next((item for item in type_def.fields if item.name == name), None)
        if field is None:
            return None
        current = _field_type(field)
    return current


def _field_type(field: Any) -> str:
    return f"{field.type_name}{'?' if getattr(field, 'nullable', False) else ''}"


def check_composition(edge: CompositionDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    source = index.agent_defs.get(edge.source_agent)
    target = index.agent_defs.get(edge.target_agent)
    if source is None:
        diagnostics.append(
            Diagnostic(
                "SEM114",
                f"Composition `{edge.name}` references unknown source agent `{edge.source_agent}`",
                span=edge.span,
            )
        )
    if target is None:
        diagnostics.append(
            Diagnostic(
                "SEM115",
                f"Composition `{edge.name}` references unknown target agent `{edge.target_agent}`",
                span=edge.span,
            )
        )
    if edge.mode not in {"delegate", "handoff"}:
        diagnostics.append(
            Diagnostic("SEM116", f"Composition `{edge.name}` requires mode `delegate` or `handoff`", span=edge.span)
        )
    if not edge.description:
        diagnostics.append(Diagnostic("SEM117", f"Composition `{edge.name}` requires a description", span=edge.span))
    if edge.history not in {"none", "summary", "full"}:
        diagnostics.append(
            Diagnostic(
                "SEM118", f"Composition `{edge.name}` has invalid history policy `{edge.history}`", span=edge.span
            )
        )
    if edge.isolation is not None and edge.isolation not in index.isolation_defs:
        diagnostics.append(
            Diagnostic(
                "SEM119", f"Composition `{edge.name}` references unknown isolation `{edge.isolation}`", span=edge.span
            )
        )
    if target is not None:
        target_parameters = {parameter.name for parameter in target.parameters}
        unknown = sorted(set(edge.mappings) - target_parameters)
        missing = sorted(target_parameters - set(edge.mappings))
        if unknown:
            diagnostics.append(
                Diagnostic(
                    "SEM120",
                    f"Composition `{edge.name}` maps unknown target inputs: {', '.join(unknown)}",
                    span=edge.span,
                )
            )
        if missing:
            diagnostics.append(
                Diagnostic(
                    "SEM121",
                    f"Composition `{edge.name}` is missing target input mappings: {', '.join(missing)}",
                    span=edge.span,
                )
            )
    return diagnostics


def check_isolation(item: IsolationDef) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not item.dimensions:
        diagnostics.append(
            Diagnostic("SEM122", f"Isolation `{item.name}` must declare at least one dimension", span=item.span)
        )
    for dimension, value in item.dimensions.items():
        allowed = ISOLATION_DIMENSIONS.get(dimension)
        if allowed is None:
            diagnostics.append(
                Diagnostic("SEM123", f"Isolation `{item.name}` has unknown dimension `{dimension}`", span=item.span)
            )
        elif value not in allowed:
            diagnostics.append(
                Diagnostic(
                    "SEM124",
                    f"Isolation `{item.name}` has invalid `{dimension}` value `{value}`",
                    span=item.span,
                    hint=f"Expected one of: {', '.join(sorted(allowed))}.",
                )
            )
    return diagnostics


def check_control(item: ControlDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics = _check_owned_declaration(item.name, item.agent, item.span, "Control", index)
    attrs = item.attributes
    assessment = _text(attrs.get("assessment"))
    if assessment not in ASSESSMENTS:
        diagnostics.append(Diagnostic("SEM125", f"Control `{item.name}` requires a valid assessment", span=item.span))
    if "require" not in attrs:
        diagnostics.append(
            Diagnostic("SEM126", f"Control `{item.name}` requires a `require` expression", span=item.span)
        )
    severity = _text(attrs.get("severity"))
    if severity not in SEVERITIES:
        diagnostics.append(Diagnostic("SEM136", f"Control `{item.name}` requires a valid severity", span=item.span))
    if _text(attrs.get("required")) not in {"true", "false"}:
        diagnostics.append(
            Diagnostic("SEM137", f"Control `{item.name}` requires boolean `required` metadata", span=item.span)
        )
    expected_evidence = attrs.get("expected_evidence", [])
    if expected_evidence and not isinstance(expected_evidence, list):
        diagnostics.append(
            Diagnostic("SEM138", f"Control `{item.name}` expected_evidence must be a list", span=item.span)
        )
    diagnostics.extend(_check_audiences(item.name, attrs.get("audience"), item.span, default=False))
    return diagnostics


def check_quality(item: QualityDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics = _check_owned_declaration(item.name, item.agent, item.span, "Quality", index)
    if not item.rubric:
        diagnostics.append(Diagnostic("SEM127", f"Quality `{item.name}` requires a rubric", span=item.span))
    diagnostics.extend(_check_audiences(item.name, item.audiences, item.span, default=True))
    return diagnostics


def check_operational_control(item: OperationalControlDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics = _check_owned_declaration(item.name, item.agent, item.span, "Operational control", index)
    if "require" not in item.attributes:
        diagnostics.append(
            Diagnostic("SEM128", f"Operational control `{item.name}` requires a `require` expression", span=item.span)
        )
    severity = _text(item.attributes.get("severity"))
    if severity not in SEVERITIES:
        diagnostics.append(
            Diagnostic("SEM139", f"Operational control `{item.name}` requires a valid severity", span=item.span)
        )
    diagnostics.extend(
        _check_audiences(item.name, item.attributes.get("audience"), item.span, default=True)
    )
    return diagnostics


def _check_parameters(parameters: Iterable[Any], index: ProjectIndex, owner: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for parameter in parameters:
        if parameter.name in seen:
            diagnostics.append(
                Diagnostic("SEM129", f"Duplicate parameter `{parameter.name}` on {owner}", span=parameter.span)
            )
        seen.add(parameter.name)
        diagnostics.extend(
            check_type_ref(parameter.type_name, index, parameter.span, f"parameter `{parameter.name}` on {owner}")
        )
    return diagnostics


def _check_owned_declaration(name: str, agent: str, span: Any, label: str, index: ProjectIndex) -> list[Diagnostic]:
    if agent in index.agent_defs:
        return []
    return [Diagnostic("SEM130", f"{label} `{name}` references unknown agent `{agent}`", span=span)]


def _check_audiences(name: str, value: Any, span: Any, *, default: bool) -> list[Diagnostic]:
    if value in (None, []) and default:
        return []
    if not isinstance(value, list) or not value:
        return [Diagnostic("SEM131", f"Declaration `{name}` requires a non-empty audience list", span=span)]
    unknown = sorted(set(str(item) for item in value) - AUDIENCES)
    if not unknown:
        return []
    return [Diagnostic("SEM132", f"Declaration `{name}` has unknown audiences: {', '.join(unknown)}", span=span)]


def _text(value: Any) -> str:
    return unquote(str(value)) if value is not None else ""


__all__ = [
    "check_composition",
    "check_control",
    "check_external_context",
    "check_isolation",
    "check_operational_control",
    "check_quality",
    "check_tool",
    "check_agent_contract",
]
