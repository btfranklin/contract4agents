"""Markdown rendering for language-service symbols."""

from __future__ import annotations

from contract4agents.ast import (
    AgentDef,
    CompositionDef,
    ContractProject,
    ControlDef,
    DatasourceDef,
    EnumDef,
    EvalCase,
    ExternalContextDef,
    FieldDef,
    IsolationDef,
    OperationalControlDef,
    QualityDef,
    RunSpecDef,
    ToolDef,
    TypeDef,
)
from contract4agents.language_service._model import SymbolId


def render_symbol(project: ContractProject, symbol: SymbolId) -> str | None:
    kind, name = symbol.kind, symbol.name
    if kind == "type":
        declaration = project.types.get(name) or project.enums.get(name)
        return _render_type(declaration) if declaration is not None else None
    if kind == "agent":
        agent = project.agents.get(name)
        return _render_agent(agent) if agent is not None else None
    if kind == "tool":
        tool = project.tools.get(name)
        return _render_callable("tool", tool) if tool is not None else None
    if kind == "datasource":
        datasource = project.datasources.get(name)
        return _render_callable("datasource", datasource) if datasource is not None else None
    if kind == "external_context":
        external_context = project.external_contexts.get(name)
        return _render_external_context(external_context) if external_context is not None else None
    if kind == "isolation":
        isolation = project.isolations.get(name)
        return _render_isolation(isolation) if isolation is not None else None
    if kind == "composition":
        composition = project.compositions.get(name)
        return _render_composition(composition) if composition is not None else None
    if kind == "run_spec":
        run_spec = project.run_specs.get(name)
        return _render_run_spec(run_spec) if run_spec is not None else None
    if kind == "field":
        return _render_field(project, symbol)
    if kind == "stage":
        return f"**Stage `{name}`**\n\nDeclared by run spec `{symbol.owner}`."
    return _render_scoped_symbol(project, symbol)


def callable_signature(item: ToolDef | DatasourceDef) -> str:
    params = ", ".join(_field_signature(field) for field in item.parameters)
    return f"({params}) -> {item.return_type}"


def _render_type(declaration: TypeDef | EnumDef) -> str:
    if isinstance(declaration, EnumDef):
        lines = [f'enum {declaration.name}:', *(f'    "{value}"' for value in declaration.values)]
        rendered = "\n".join(lines)
        return f"```contract\n{rendered}\n```\n\nClosed string enum."
    lines = [f"type {declaration.name}:"]
    lines.extend(
        f"    {field.name}: {field.type_name}{'?' if field.nullable else ''}"
        + (f" = {field.default}" if field.default is not None else "")
        for field in declaration.fields
    )
    rendered = "\n".join(lines)
    return f"```contract\n{rendered}\n```\n\nPortable structural type."


def _render_agent(agent: AgentDef) -> str:
    params = ", ".join(_field_signature(item) for item in agent.parameters)
    lines = [f"agent {agent.name}({params}) -> {agent.return_type}:"]
    lines.extend(f"    use {grant.capability}" for grant in agent.grants)
    description = agent.text_attr("description") or agent.text_attr("goal")
    suffix = f"\n\n{description}" if description else ""
    rendered = "\n".join(lines)
    return f"```contract\n{rendered}\n```{suffix}"


def _render_callable(kind: str, item: ToolDef | DatasourceDef) -> str:
    return f"```contract\n{kind} {item.name}{callable_signature(item)}\n```\n\n{item.description}"


def _render_external_context(item: ExternalContextDef) -> str:
    return (
        f"```contract\nexternal_context {item.name} -> {item.type_name}\n```\n\n"
        f"{item.description}\n\nSensitivity: `{item.sensitivity}` · Render: `{item.render}`"
    )


def _render_isolation(item: IsolationDef) -> str:
    lines = [f"isolation {item.name}:", *(f"    {key} = {value}" for key, value in item.dimensions.items())]
    rendered = "\n".join(lines)
    return f"```contract\n{rendered}\n```\n\nMultidimensional isolation requirement."


def _render_composition(item: CompositionDef) -> str:
    return (
        f"```contract\ncomposition {item.name} from {item.source_agent} to {item.target_agent}:\n"
        f"    mode = {item.mode}\n```\n\n{item.description}"
    )


def _render_scoped_symbol(project: ContractProject, symbol: SymbolId) -> str | None:
    if symbol.owner is None:
        return None
    kind, agent, name = symbol.kind, symbol.owner, symbol.name
    if kind == "control":
        control = next((item for item in project.controls if item.agent == agent and item.name == name), None)
        return _render_control(control) if control is not None else None
    if kind == "quality":
        quality = next((item for item in project.qualities if item.agent == agent and item.name == name), None)
        return _render_quality(quality) if quality is not None else None
    if kind == "operational_control":
        operational_control = next(
            (item for item in project.operational_controls if item.agent == agent and item.name == name),
            None,
        )
        return _render_operational_control(operational_control) if operational_control is not None else None
    if kind == "eval":
        eval_case = next((item for item in project.evals if item.agent == agent and item.name == name), None)
        return _render_eval(eval_case) if eval_case is not None else None
    return None


def _render_field(project: ContractProject, symbol: SymbolId) -> str | None:
    if symbol.owner is None:
        return None
    owner = symbol.owner
    type_declaration = project.types.get(owner)
    if type_declaration is not None:
        field = next((item for item in type_declaration.fields if item.name == symbol.name), None)
        return _render_field_detail(owner, field) if field is not None else None
    callable_declarations: list[ToolDef | DatasourceDef | AgentDef] = []
    callable_declarations.extend(project.tools.values())
    callable_declarations.extend(project.datasources.values())
    callable_declarations.extend(project.agents.values())
    for callable_declaration in callable_declarations:
        if callable_declaration.name != owner:
            continue
        field = next((item for item in callable_declaration.parameters if item.name == symbol.name), None)
        if field is not None:
            return _render_field_detail(owner, field)
    agent = project.agents.get(owner)
    if agent is not None:
        context = next((item for item in agent.context if item.name == symbol.name), None)
        if context is not None:
            return (
                f"```contract\ncontext {context.name}: {context.type_name} from {context.origin}\n```\n\n"
                f"Context required by `{owner}`."
            )
    return None


def _render_field_detail(owner: str, field: FieldDef) -> str:
    return f"```contract\n{_field_signature(field)}\n```\n\nDeclared by `{owner}`."


def _render_control(item: ControlDef) -> str:
    severity = item.attributes.get("severity", "unspecified")
    assessment = item.attributes.get("assessment", "unspecified")
    required = item.attributes.get("required", "unspecified")
    return (
        f"```contract\ncontrol {item.name} for {item.agent}\n```\n\n"
        f"Severity: `{severity}` · Assessment: `{assessment}` · Required: `{required}`"
    )


def _render_quality(item: QualityDef) -> str:
    audiences = ", ".join(f"`{audience}`" for audience in item.audiences)
    return f"```contract\nquality {item.name} for {item.agent}\n```\n\n{item.rubric}\n\nAudience: {audiences}"


def _render_operational_control(item: OperationalControlDef) -> str:
    severity = item.attributes.get("severity", "unspecified")
    return (
        f"```contract\noperational_control {item.name} for {item.agent}\n```\n\n"
        f"Severity: `{severity}` · Host-observed operational requirement."
    )


def _render_eval(item: EvalCase) -> str:
    return (
        f"```contract\neval {item.name} for {item.agent}\n```\n\n"
        f"{len(item.givens)} given value(s) · {len(item.expects) + len(item.semantic_expects)} expectation(s)"
    )


def _render_run_spec(item: RunSpecDef) -> str:
    return (
        f"```contract\nrun_spec {item.name}\n```\n\n"
        f"{len(item.stages)} stage(s) · {len(item.assertions)} assertion(s)"
    )


def _field_signature(field: FieldDef) -> str:
    nullable = "?" if field.nullable else ""
    return f"{field.name}: {field.type_name}{nullable}"


__all__ = ["callable_signature", "render_symbol"]
