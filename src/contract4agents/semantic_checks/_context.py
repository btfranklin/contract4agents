"""Static context satisfiability checks for agent dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents._datasource_resolution import (
    DatasourceCandidate,
    DatasourceResolution,
    resolve_datasource_type,
)
from contract4agents.ast import AgentDef, DatasourceDef, SourceSpan
from contract4agents.composition import parse_composition_declaration
from contract4agents.diagnostics import Diagnostic
from contract4agents.semantic_checks._index import ProjectIndex
from contract4agents.type_refs import canonical_type_name


@dataclass(frozen=True)
class _ChildRelation:
    parent: AgentDef
    child: AgentDef
    span: SourceSpan | None


def check_context_dependencies(index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for relation in _child_relations(index):
        diagnostics.extend(_check_relation(relation, index))
    return diagnostics


def _child_relations(index: ProjectIndex) -> list[_ChildRelation]:
    relations: list[_ChildRelation] = []
    for parent in index.agent_defs.values():
        seen: set[str] = set()
        spans = {use.name: use.span for use in parent.uses if use.kind == "agent"}
        for child_name in [use.name for use in parent.uses if use.kind == "agent"]:
            _append_relation(relations, seen, parent, child_name, spans.get(child_name), index)
        composition_span = parent.attribute_spans.get("composition", parent.span)
        for item in parent.list_attr("composition"):
            declaration = parse_composition_declaration(item)
            if declaration is None:
                continue
            _append_relation(relations, seen, parent, declaration.agent, composition_span, index)
    return relations


def _append_relation(
    relations: list[_ChildRelation],
    seen: set[str],
    parent: AgentDef,
    child_name: str,
    span: SourceSpan | None,
    index: ProjectIndex,
) -> None:
    if child_name in seen:
        return
    child = index.agent_defs.get(child_name)
    if child is None:
        return
    seen.add(child_name)
    relations.append(_ChildRelation(parent, child, span))


def _check_relation(relation: _ChildRelation, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    available = _available_context(relation.parent)
    datasources = _agent_datasources(relation.parent, index)
    for parameter in relation.child.parameters:
        if parameter.nullable or parameter.default is not None:
            continue
        type_name = parameter.normalized_type
        resolution = resolve_datasource_type(
            type_name,
            available=available,
            datasources=_datasource_candidates(datasources),
        )
        if resolution.status != "ok":
            diagnostics.append(_diagnostic_for_resolution(relation, resolution))
    return diagnostics


def _available_context(agent: AgentDef) -> set[str]:
    return {
        parameter.normalized_type
        for parameter in agent.parameters
        if not parameter.nullable and parameter.default is None
    } | {canonical_type_name(type_name) for type_name in agent.list_attr("host_context")}


def _agent_datasources(agent: AgentDef, index: ProjectIndex) -> list[DatasourceDef]:
    datasources: list[DatasourceDef] = []
    for use in agent.uses:
        if use.kind != "datasource":
            continue
        datasource = index.datasource_defs.get(use.name)
        if datasource is not None:
            datasources.append(datasource)
    return datasources


def _datasource_candidates(datasources: list[DatasourceDef]) -> list[DatasourceCandidate]:
    return [
        DatasourceCandidate(datasource.name, datasource.produces, tuple(datasource.requires))
        for datasource in datasources
    ]


def _diagnostic_for_resolution(relation: _ChildRelation, resolution: DatasourceResolution) -> Diagnostic:
    parent = relation.parent.name
    child = relation.child.name
    if resolution.status == "ambiguous":
        return Diagnostic(
            "SEM073",
            f"Agent `{parent}` cannot prove context for `{child}` parameter type `{resolution.type_name}` "
            f"because datasource resolution is ambiguous",
            span=relation.span,
            hint=(
                f"`{resolution.type_name}` can be produced by: {', '.join(resolution.candidates)}. "
                f"Resolution path: {_format_path(resolution.path)}."
            ),
        )
    if resolution.status == "cycle":
        return Diagnostic(
            "SEM074",
            f"Agent `{parent}` cannot prove context for `{child}` parameter type `{resolution.type_name}` "
            "because datasource requirements form a cycle",
            span=relation.span,
            hint=f"Cycle: {' -> '.join(resolution.cycle)}. Resolution path: {_format_path(resolution.path)}.",
        )
    return Diagnostic(
        "SEM072",
        f"Agent `{parent}` cannot supply required context `{resolution.type_name}` for child agent `{child}`",
        span=relation.span,
        hint=(
            f"Add `{resolution.type_name}` as a required parent parameter, declare `host_context = "
            f"[{resolution.type_name}]`, or add a datasource chain on `{parent}` that can produce it. "
            f"Resolution path: {_format_path(resolution.path)}."
        ),
    )


def _format_path(path: tuple[str, ...]) -> str:
    return " -> ".join(path) if path else "(parent context)"


__all__ = ["check_context_dependencies"]
