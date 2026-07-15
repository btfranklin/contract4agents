"""Type and datasource semantic checks."""

from __future__ import annotations

from contract4agents.ast import DatasourceDef, TypeDef
from contract4agents.diagnostics import Diagnostic
from contract4agents.ir._type_refs import (
    ListTypeRef,
    MapTypeRef,
    NamedTypeRef,
    NullableTypeRef,
    PrimitiveTypeRef,
    TypeRef,
    parse_type_ref,
)
from contract4agents.semantic_checks._index import ProjectIndex


def check_type(type_def: TypeDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for field in type_def.fields:
        if field.name in seen:
            diagnostics.append(
                Diagnostic("SEM001", f"Duplicate field `{field.name}` on type `{type_def.name}`", span=field.span)
            )
        seen.add(field.name)
        diagnostics.extend(check_type_ref(field.type_name, index, field.span, f"field `{field.name}`"))
    return diagnostics


def check_datasource(datasource: DatasourceDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(check_type_ref(datasource.return_type, index, datasource.span, "datasource output"))
    for parameter in datasource.parameters:
        diagnostics.extend(
            check_type_ref(parameter.type_name, index, parameter.span, f"datasource parameter `{parameter.name}`")
        )
    if datasource.cache not in {"none", "run", "thread"}:
        diagnostics.append(
            Diagnostic("SEM011", f"Invalid datasource cache scope `{datasource.cache}`", span=datasource.span)
        )
    if datasource.render not in {"markdown", "json", "text"}:
        diagnostics.append(
            Diagnostic("SEM012", f"Invalid datasource render mode `{datasource.render}`", span=datasource.span)
        )
    if not datasource.description:
        diagnostics.append(
            Diagnostic("SEM013", f"Datasource `{datasource.name}` requires a description", span=datasource.span)
        )
    return diagnostics


def check_type_ref(
    raw_type: str,
    index: ProjectIndex,
    span: object,
    context: str,
) -> list[Diagnostic]:
    try:
        parsed = parse_type_ref(raw_type)
    except ValueError as exc:
        return [Diagnostic("SEM002", f"Invalid portable type `{raw_type}` in {context}", span=span, hint=str(exc))]  # type: ignore[arg-type]
    unknown = sorted(name for name in _named_type_names(parsed) if name not in index.type_defs)
    return [Diagnostic("SEM002", f"Unknown type `{name}` in {context}", span=span) for name in unknown]  # type: ignore[arg-type]


def _named_type_names(type_ref: TypeRef) -> set[str]:
    if isinstance(type_ref, NamedTypeRef):
        return {type_ref.type_id.parts[0]}
    if isinstance(type_ref, NullableTypeRef | ListTypeRef):
        return _named_type_names(type_ref.item)
    if isinstance(type_ref, MapTypeRef):
        return _named_type_names(type_ref.value)
    if isinstance(type_ref, PrimitiveTypeRef):
        return set()
    raise TypeError(f"Unsupported type reference {type(type_ref).__name__}")


__all__ = ["check_datasource", "check_type", "check_type_ref"]
