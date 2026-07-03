"""Static context satisfiability checks for agent dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from contract4agents.ast import AgentDef, DatasourceDef, SourceSpan
from contract4agents.composition import parse_composition_declaration
from contract4agents.diagnostics import Diagnostic
from contract4agents.semantic_checks._index import ProjectIndex

_Status = Literal["ok", "missing", "ambiguous", "cycle"]


@dataclass(frozen=True)
class _ChildRelation:
    parent: AgentDef
    child: AgentDef
    span: SourceSpan | None


@dataclass(frozen=True)
class _Resolution:
    status: _Status
    type_name: str
    path: tuple[str, ...]
    candidates: tuple[str, ...] = ()
    cycle: tuple[str, ...] = ()

    @classmethod
    def ok(cls, type_name: str, path: tuple[str, ...]) -> _Resolution:
        return cls("ok", type_name, path)


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
        resolution = _resolve_type(type_name, available, datasources, resolving=(), path=())
        if resolution.status != "ok":
            diagnostics.append(_diagnostic_for_resolution(relation, resolution))
    return diagnostics


def _available_context(agent: AgentDef) -> set[str]:
    return {
        parameter.normalized_type
        for parameter in agent.parameters
        if not parameter.nullable and parameter.default is None
    } | {_normalize_type(type_name) for type_name in agent.list_attr("host_context")}


def _agent_datasources(agent: AgentDef, index: ProjectIndex) -> list[DatasourceDef]:
    datasources: list[DatasourceDef] = []
    for use in agent.uses:
        if use.kind != "datasource":
            continue
        datasource = index.datasource_defs.get(use.name)
        if datasource is not None:
            datasources.append(datasource)
    return datasources


def _resolve_type(
    type_name: str,
    available: set[str],
    datasources: list[DatasourceDef],
    resolving: tuple[str, ...],
    path: tuple[str, ...],
) -> _Resolution:
    normalized = _normalize_type(type_name)
    if normalized in available:
        return _Resolution.ok(normalized, (*path, normalized))
    if normalized in resolving:
        cycle_start = resolving.index(normalized)
        return _Resolution("cycle", normalized, path, cycle=(*resolving[cycle_start:], normalized))

    candidates = [datasource for datasource in datasources if _normalize_type(datasource.produces) == normalized]
    if not candidates:
        return _Resolution("missing", normalized, path)

    valid: list[DatasourceDef] = []
    first_failure: _Resolution | None = None
    for candidate in candidates:
        proof = _resolve_datasource(candidate, available, datasources, (*resolving, normalized), path)
        if proof.status == "ok":
            valid.append(candidate)
            continue
        if first_failure is None or (first_failure.status != "cycle" and proof.status == "cycle"):
            first_failure = proof

    if len(valid) > 1:
        return _Resolution(
            "ambiguous",
            normalized,
            path,
            candidates=tuple(sorted(datasource.name for datasource in valid)),
        )
    if len(valid) == 1:
        return _Resolution.ok(normalized, (*path, f"{valid[0].name}:{normalized}"))
    return first_failure or _Resolution("missing", normalized, path)


def _resolve_datasource(
    datasource: DatasourceDef,
    available: set[str],
    datasources: list[DatasourceDef],
    resolving: tuple[str, ...],
    path: tuple[str, ...],
) -> _Resolution:
    datasource_path = (*path, datasource.name)
    for required in datasource.requires:
        proof = _resolve_type(required, available, datasources, resolving, datasource_path)
        if proof.status != "ok":
            return proof
    return _Resolution.ok(datasource.produces, datasource_path)


def _diagnostic_for_resolution(relation: _ChildRelation, resolution: _Resolution) -> Diagnostic:
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


def _normalize_type(raw_type: str) -> str:
    value = raw_type.strip().rstrip("?")
    if value.endswith("[]"):
        value = value[:-2]
    if value.startswith("list[") and value.endswith("]"):
        value = value[5:-1]
    return value


__all__ = ["check_context_dependencies"]
