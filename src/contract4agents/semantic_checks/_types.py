"""Type and datasource semantic checks."""

from __future__ import annotations

import json

from contract4agents.ast import DatasourceDef, EnumDef, TypeDef
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
from contract4agents.language_spec import CACHE_SCOPES, RENDER_MODES
from contract4agents.parser._values import unquote
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
        if field.default is not None:
            raw_type = field.type_name + ("?" if field.nullable else "")
            diagnostics.extend(
                _check_enum_default(type_def.name, field.name, raw_type, field.default, index, field.span)
            )
    return diagnostics


def check_enum(enum_def: EnumDef) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not enum_def.values:
        diagnostics.append(
            Diagnostic("SEM014", f"Enum `{enum_def.name}` must declare at least one value", span=enum_def.span)
        )
    seen: set[str] = set()
    for value in enum_def.values:
        if not value:
            diagnostics.append(
                Diagnostic("SEM015", f"Enum `{enum_def.name}` values cannot be empty", span=enum_def.span)
            )
        elif value in seen:
            diagnostics.append(
                Diagnostic(
                    "SEM016",
                    f"Duplicate value `{value}` on enum `{enum_def.name}`",
                    span=enum_def.span,
                )
            )
        seen.add(value)
    return diagnostics


def check_datasource(datasource: DatasourceDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(check_type_ref(datasource.return_type, index, datasource.span, "datasource output"))
    for parameter in datasource.parameters:
        diagnostics.extend(
            check_type_ref(parameter.type_name, index, parameter.span, f"datasource parameter `{parameter.name}`")
        )
    if datasource.cache not in CACHE_SCOPES:
        diagnostics.append(
            Diagnostic("SEM011", f"Invalid datasource cache scope `{datasource.cache}`", span=datasource.span)
        )
    if datasource.render not in RENDER_MODES:
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


def _check_enum_default(
    type_name: str,
    field_name: str,
    raw_type: str,
    raw_default: str,
    index: ProjectIndex,
    span: object,
) -> list[Diagnostic]:
    try:
        type_ref = parse_type_ref(raw_type)
    except ValueError:
        return []
    try:
        value = json.loads(raw_default)
    except json.JSONDecodeError:
        value = unquote(raw_default)
    if _default_conforms_to_enums(value, type_ref, index):
        return []
    return [
        Diagnostic(
            "SEM017",
            f"Default for field `{type_name}.{field_name}` does not conform to `{raw_type}`",
            span=span,  # type: ignore[arg-type]
        )
    ]


def _default_conforms_to_enums(value: object, type_ref: TypeRef, index: ProjectIndex) -> bool:
    if isinstance(type_ref, NullableTypeRef):
        return value is None or _default_conforms_to_enums(value, type_ref.item, index)
    if isinstance(type_ref, ListTypeRef):
        return (
            not _contains_enum(type_ref.item, index)
            or isinstance(value, list)
            and all(_default_conforms_to_enums(item, type_ref.item, index) for item in value)
        )
    if isinstance(type_ref, MapTypeRef):
        return (
            not _contains_enum(type_ref.value, index)
            or isinstance(value, dict)
            and all(_default_conforms_to_enums(item, type_ref.value, index) for item in value.values())
        )
    if isinstance(type_ref, NamedTypeRef):
        declaration = index.type_defs.get(type_ref.type_id.parts[0])
        if isinstance(declaration, EnumDef):
            return isinstance(value, str) and value in declaration.values
        if isinstance(declaration, TypeDef) and _contains_enum(type_ref, index):
            if not isinstance(value, dict):
                return False
            fields = {field.name: field for field in declaration.fields}
            return all(
                name not in fields
                or _default_conforms_to_enums(
                    child,
                    parse_type_ref(fields[name].type_name + ("?" if fields[name].nullable else "")),
                    index,
                )
                for name, child in value.items()
            )
    return True


def _contains_enum(type_ref: TypeRef, index: ProjectIndex, seen: frozenset[str] = frozenset()) -> bool:
    if isinstance(type_ref, NullableTypeRef | ListTypeRef):
        return _contains_enum(type_ref.item, index, seen)
    if isinstance(type_ref, MapTypeRef):
        return _contains_enum(type_ref.value, index, seen)
    if isinstance(type_ref, NamedTypeRef):
        name = type_ref.type_id.parts[0]
        if name in seen:
            return False
        declaration = index.type_defs.get(name)
        if isinstance(declaration, EnumDef):
            return True
        if isinstance(declaration, TypeDef):
            next_seen = seen | {name}
            return any(
                _contains_enum(parse_type_ref(field.type_name), index, next_seen)
                for field in declaration.fields
            )
    return False


__all__ = ["check_datasource", "check_enum", "check_type", "check_type_ref"]
