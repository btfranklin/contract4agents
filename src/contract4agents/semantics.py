"""Semantic analysis for parsed Contract4Agents projects."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.ast import AgentDef, ContractProject, DatasourceDef, MonitorDef, SourceSpan, TypeDef
from contract4agents.composition import parse_composition_declaration
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
from contract4agents.hosted_tools import SUPPORTED_HOSTED_TOOLS, split_hosted_tool_name
from contract4agents.pydantic_interop import python_type_ref_diagnostics

BUILTIN_TYPES = {"str", "int", "float", "bool", "AgentRef"}
TEXT_AGENT_ATTRIBUTES = {"description", "goal"}
LIST_AGENT_ATTRIBUTES = {"assertions", "composition", "guards", "policy", "routes", "success"}
AGENT_ATTRIBUTES = TEXT_AGENT_ATTRIBUTES | LIST_AGENT_ATTRIBUTES
COMMON_AGENT_ATTRIBUTE_MISSPELLINGS = {
    "assertion": "assertions",
    "guard": "guards",
    "route": "routes",
}


@dataclass(frozen=True)
class SemanticResult:
    diagnostics: list[Diagnostic]

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


@dataclass(frozen=True)
class _ProjectIndex:
    type_defs: dict[str, TypeDef]
    agent_defs: dict[str, AgentDef]
    datasource_defs: dict[str, DatasourceDef]
    project_tools: set[str]
    project_hosted_tools: set[str]
    datasource_targets: set[str]

    @classmethod
    def from_project(cls, project: ContractProject) -> _ProjectIndex:
        agent_defs = project.agents
        datasource_defs = project.datasources
        return cls(
            type_defs=project.types,
            agent_defs=agent_defs,
            datasource_defs=datasource_defs,
            project_tools={use.name for agent in agent_defs.values() for use in agent.uses if use.kind == "tool"},
            project_hosted_tools={
                use.name for agent in agent_defs.values() for use in agent.uses if use.kind == "hosted_tool"
            },
            datasource_targets=set(datasource_defs) | {item.produces for item in datasource_defs.values()},
        )

    @property
    def agent_names(self) -> set[str]:
        return set(self.agent_defs)


def analyze_project(project: ContractProject) -> SemanticResult:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(
        _duplicates([(item.name, item.span) for module in project.modules for item in module.types], "type")
    )
    diagnostics.extend(
        _duplicates([(item.name, item.span) for module in project.modules for item in module.agents], "agent")
    )
    diagnostics.extend(
        _duplicates(
            [
                (item.name, item.span or SourceSpan(module.path, 1))
                for module in project.modules
                for item in module.datasources
            ],
            "datasource",
        )
    )
    index = _ProjectIndex.from_project(project)
    for type_def in index.type_defs.values():
        diagnostics.extend(_check_type(type_def, index))
    for datasource in index.datasource_defs.values():
        diagnostics.extend(_check_datasource(datasource, index))
    for agent in index.agent_defs.values():
        diagnostics.extend(_check_agent(agent, index))
    for eval_case in project.evals:
        diagnostics.extend(_check_eval(eval_case.agent, eval_case.expects, eval_case.semantic_expects, index))
    for monitor in project.monitors:
        diagnostics.extend(_check_monitor(monitor, index))
    return SemanticResult(diagnostics)


def _duplicates(items: list[tuple[str, SourceSpan]], label: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    seen: dict[str, SourceSpan] = {}
    for name, span in items:
        if name in seen:
            diagnostics.append(
                Diagnostic(
                    "SEM000",
                    f"Duplicate {label} declaration `{name}`",
                    span=span,
                    hint=f"First declaration was at {seen[name].display()}",
                )
            )
        else:
            seen[name] = span
    return diagnostics


def _check_type(type_def: TypeDef, index: _ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(python_type_ref_diagnostics(type_def))
    if type_def.source == "python":
        return diagnostics
    seen: set[str] = set()
    for field in type_def.fields:
        if field.name in seen:
            diagnostics.append(
                Diagnostic("SEM001", f"Duplicate field `{field.name}` on type `{type_def.name}`", span=field.span)
            )
        seen.add(field.name)
        diagnostics.extend(_check_type_ref(field.type_name, index, field.span, f"field `{field.name}`"))
    return diagnostics


def _check_datasource(datasource: DatasourceDef, index: _ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_check_type_ref(datasource.produces, index, datasource.span, "datasource output"))
    for required in datasource.requires:
        diagnostics.extend(_check_type_ref(required, index, datasource.span, "datasource requirement"))
    if ":" not in datasource.python:
        diagnostics.append(
            Diagnostic(
                "SEM010",
                f"Datasource `{datasource.name}` python reference must be `module:function`",
                span=datasource.span,
            )
        )
    if datasource.cache not in {"none", "run", "thread"}:
        diagnostics.append(
            Diagnostic("SEM011", f"Invalid datasource cache scope `{datasource.cache}`", span=datasource.span)
        )
    return diagnostics


def _check_agent(
    agent: AgentDef,
    index: _ProjectIndex,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_check_agent_attributes(agent))
    for parameter in agent.parameters:
        diagnostics.extend(_check_type_ref(parameter.type_name, index, parameter.span, "agent parameter"))
    diagnostics.extend(_check_type_ref(agent.return_type, index, agent.span, "agent return type"))
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
            _check_expression_refs(
                expression,
                agent,
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


def _check_eval(
    agent_name: str,
    expects: list[str],
    semantic_expects: list[str],
    index: _ProjectIndex,
) -> list[Diagnostic]:
    agent = index.agent_defs.get(agent_name)
    if not agent:
        return [Diagnostic("SEM040", f"Eval references unknown agent `{agent_name}`")]
    diagnostics: list[Diagnostic] = []
    for expression in expects:
        diagnostics.extend(
            _check_expression_refs(
                expression,
                agent,
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


def _check_monitor(
    rule: MonitorDef,
    index: _ProjectIndex,
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
                _check_trace_refs(parsed, index, index.project_tools, index.project_hosted_tools, rule.span)
            )
    return diagnostics


def _check_composition(agent: AgentDef, index: _ProjectIndex) -> list[Diagnostic]:
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
                    hint=(
                        "Expected one of: agent_as_tool(AgentName), as_tool(AgentName), "
                        "handoff(AgentName), isolated_subagent(AgentName)."
                    ),
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
        provider_tools = SUPPORTED_HOSTED_TOOLS.get(provider)
        if provider_tools is None:
            diagnostics.append(
                Diagnostic("SEM061", f"Unknown hosted tool provider `{provider}` for `{use.name}`", span=use.span)
            )
            continue
        tool_options = provider_tools.get(tool)
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


def _check_expression_refs(
    expression: str,
    agent: AgentDef,
    index: _ProjectIndex,
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
        diagnostics.extend(_check_parsed_expression(parsed, agent, index, tool_names, hosted_tool_names, span))
    return diagnostics


def _check_parsed_expression(
    parsed: ParsedExpression,
    agent: AgentDef,
    index: _ProjectIndex,
    tool_names: set[str],
    hosted_tool_names: set[str],
    span: SourceSpan,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    type_name = referenced_type(parsed)
    if type_name and type_name not in index.type_defs:
        diagnostics.append(Diagnostic("SEM002", f"Unknown type `{type_name}` in expression", span=span))
    return_type = index.type_defs.get(agent.return_type)
    if return_type and return_type.source == "native":
        output_fields = {field.name for field in return_type.fields}
        for field in referenced_output_fields(parsed):
            if field not in output_fields:
                diagnostics.append(
                    Diagnostic(
                        "SEM050",
                        f"Expression references unknown output field `{field}` on `{agent.return_type}`",
                        span=span,
                    )
                )
    diagnostics.extend(_check_trace_refs(parsed, index, tool_names, hosted_tool_names, span))
    return diagnostics


def _check_trace_refs(
    parsed: ParsedExpression,
    index: _ProjectIndex,
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


def _check_type_ref(
    raw_type: str,
    index: _ProjectIndex,
    span: object,
    context: str,
) -> list[Diagnostic]:
    normalized = _normalize_type(raw_type)
    if not normalized or normalized in BUILTIN_TYPES or normalized in index.type_defs or _is_literal_union(raw_type):
        return []
    return [Diagnostic("SEM002", f"Unknown type `{normalized}` in {context}", span=span)]  # type: ignore[arg-type]


def _normalize_type(raw_type: str) -> str:
    value = raw_type.strip().rstrip("?")
    if value.endswith("[]"):
        value = value[:-2]
    if value.startswith("list[") and value.endswith("]"):
        value = value[5:-1]
    if " between " in value:
        value = value.split(" ", 1)[0]
    return value


def _is_literal_union(raw_type: str) -> bool:
    return '"' in raw_type and "|" in raw_type
