"""Agent, composition, and hosted-tool semantic checks."""

from __future__ import annotations

from contract4agents.ast import AgentDef, SourceSpan
from contract4agents.composition import parse_composition_declaration
from contract4agents.diagnostics import Diagnostic
from contract4agents.hosted_tools import hosted_tool_descriptor, split_hosted_tool_name
from contract4agents.semantic_checks._expressions import check_expression_refs
from contract4agents.semantic_checks._index import ProjectIndex
from contract4agents.semantic_checks._types import check_type_ref

TEXT_AGENT_ATTRIBUTES = {"description", "goal"}
LIST_AGENT_ATTRIBUTES = {"assertions", "composition", "guards", "policy", "routes", "success"}
AGENT_ATTRIBUTES = TEXT_AGENT_ATTRIBUTES | LIST_AGENT_ATTRIBUTES
COMMON_AGENT_ATTRIBUTE_MISSPELLINGS = {
    "assertion": "assertions",
    "guard": "guards",
    "route": "routes",
}


def check_agent(
    agent: AgentDef,
    index: ProjectIndex,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_check_agent_attributes(agent))
    for parameter in agent.parameters:
        diagnostics.extend(check_type_ref(parameter.type_name, index, parameter.span, "agent parameter"))
    diagnostics.extend(check_type_ref(agent.return_type, index, agent.span, "agent return type"))
    datasource_outputs: dict[str, int] = {}
    tool_names = {use.name for use in agent.uses if use.kind == "tool"}
    hosted_tool_names = {use.name for use in agent.uses if use.kind == "hosted_tool"}
    diagnostics.extend(_check_hosted_tools(agent))
    diagnostics.extend(_check_composition(agent, index))
    for use in agent.uses:
        if use.kind == "agent" and use.name not in index.agent_defs:
            diagnostics.append(
                Diagnostic("SEM020", f"Agent `{agent.name}` uses unknown agent `{use.name}`", span=use.span)
            )
        if use.kind == "datasource":
            datasource = index.datasource_defs.get(use.name)
            if not datasource:
                diagnostics.append(
                    Diagnostic("SEM021", f"Agent `{agent.name}` uses unknown datasource `{use.name}`", span=use.span)
                )
            else:
                datasource_outputs[datasource.produces] = datasource_outputs.get(datasource.produces, 0) + 1
    for type_name, count in datasource_outputs.items():
        if count > 1:
            diagnostics.append(
                Diagnostic(
                    "SEM022",
                    f"Agent `{agent.name}` has ambiguous datasources for `{type_name}`",
                    span=agent.span,
                    hint="Declare a single datasource per produced type until explicit disambiguation exists.",
                )
            )
    for expression in agent.list_attr("guards") + agent.list_attr("assertions"):
        diagnostics.extend(
            check_expression_refs(
                expression,
                agent.name,
                agent.return_type,
                index,
                tool_names,
                hosted_tool_names,
                span=agent.span,
                contract_expression=True,
            )
        )
    return diagnostics


def _check_agent_attributes(agent: AgentDef) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for key, value in agent.attributes.items():
        span = agent.attribute_spans.get(key, agent.span)
        if key not in AGENT_ATTRIBUTES:
            hint = _unknown_agent_attribute_hint(key)
            diagnostics.append(
                Diagnostic(
                    "SEM070",
                    f"Unknown agent attribute `{key}` on `{agent.name}`",
                    span=span,
                    hint=hint,
                )
            )
            continue
        if key in TEXT_AGENT_ATTRIBUTES and not isinstance(value, str):
            diagnostics.append(
                Diagnostic(
                    "SEM071",
                    f"Agent attribute `{key}` on `{agent.name}` must be a string",
                    span=span,
                )
            )
        elif key in LIST_AGENT_ATTRIBUTES and not isinstance(value, list):
            diagnostics.append(
                Diagnostic(
                    "SEM071",
                    f"Agent attribute `{key}` on `{agent.name}` must be a list",
                    span=span,
                )
            )
    return diagnostics


def _unknown_agent_attribute_hint(key: str) -> str:
    expected = COMMON_AGENT_ATTRIBUTE_MISSPELLINGS.get(key)
    if expected:
        return f"Use `{expected}`."
    return "Accepted agent attributes are: " + ", ".join(f"`{item}`" for item in sorted(AGENT_ATTRIBUTES)) + "."


def _check_composition(agent: AgentDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    agent_dependencies = {use.name for use in agent.uses if use.kind == "agent"}
    for item in agent.list_attr("composition"):
        declaration = parse_composition_declaration(item)
        if declaration is None:
            diagnostics.append(
                Diagnostic(
                    "SEM066",
                    f"Malformed composition declaration `{item}` on agent `{agent.name}`",
                    span=agent.span,
                    hint="Expected one of: agent_as_tool(AgentName), handoff(AgentName), isolated_subagent(AgentName).",
                )
            )
            continue
        if declaration.agent not in index.agent_defs:
            diagnostics.append(
                Diagnostic(
                    "SEM067",
                    f"Composition declaration `{item}` references unknown agent `{declaration.agent}`",
                    span=agent.span,
                )
            )
            continue
        if declaration.agent not in agent_dependencies:
            diagnostics.append(
                Diagnostic(
                    "SEM068",
                    f"Composition declaration `{item}` references agent `{declaration.agent}` "
                    "without a matching `use agent` dependency",
                    span=agent.span,
                    hint=f"Add `use agent {declaration.agent} from ...` before declaring composition.",
                )
            )
    return diagnostics


def _check_hosted_tools(agent: AgentDef) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    seen: dict[str, SourceSpan | None] = {}
    for use in agent.uses:
        if use.kind != "hosted_tool":
            continue
        if use.name in seen:
            first_span = seen[use.name]
            diagnostics.append(
                Diagnostic(
                    "SEM065",
                    f"Agent `{agent.name}` declares hosted tool `{use.name}` more than once",
                    span=use.span,
                    hint=f"First declaration was at {first_span.display()}" if first_span else None,
                )
            )
        else:
            seen[use.name] = use.span
        split_name = split_hosted_tool_name(use.name)
        if split_name is None:
            diagnostics.append(
                Diagnostic("SEM060", f"Hosted tool `{use.name}` must be declared as `provider.tool`", span=use.span)
            )
            continue
        provider, tool = split_name
        descriptor = hosted_tool_descriptor(provider)
        if descriptor is None:
            diagnostics.append(
                Diagnostic(
                    "SEM061",
                    f"Hosted tool provider `{provider}` for `{use.name}` has no built-in descriptor",
                    severity="warning",
                    span=use.span,
                    hint=(
                        "Core validation will keep the provider.tool declaration, "
                        "but no bundled adapter validates it."
                    ),
                )
            )
            continue
        tool_options = descriptor.tool_options(tool)
        if tool_options is None:
            diagnostics.append(
                Diagnostic("SEM062", f"Unknown hosted tool `{use.name}` for provider `{provider}`", span=use.span)
            )
            continue
        for option_name, option_value in use.config.items():
            allowed_values = tool_options.get(option_name)
            if allowed_values is None:
                diagnostics.append(
                    Diagnostic(
                        "SEM063",
                        f"Unsupported hosted tool option `{option_name}` for `{use.name}`",
                        span=use.span,
                    )
                )
                continue
            if option_value not in allowed_values:
                diagnostics.append(
                    Diagnostic(
                        "SEM064",
                        f"Invalid value `{option_value}` for hosted tool option `{option_name}` on `{use.name}`",
                        span=use.span,
                        hint=f"Expected one of: {', '.join(sorted(allowed_values))}",
                    )
                )
    return diagnostics


__all__ = ["check_agent"]
