"""Type and datasource semantic checks."""

from __future__ import annotations

from contract4agents.ast import DatasourceDef, TypeDef
from contract4agents.diagnostics import Diagnostic
from contract4agents.pydantic_interop import python_type_ref_diagnostics
from contract4agents.semantic_checks._index import ProjectIndex

BUILTIN_TYPES = {"str", "int", "float", "bool", "AgentRef"}


def check_type(type_def: TypeDef, index: ProjectIndex) -> list[Diagnostic]:
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
        diagnostics.extend(check_type_ref(field.type_name, index, field.span, f"field `{field.name}`"))
    return diagnostics


def check_datasource(datasource: DatasourceDef, index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(check_type_ref(datasource.produces, index, datasource.span, "datasource output"))
    for required in datasource.requires:
        diagnostics.extend(check_type_ref(required, index, datasource.span, "datasource requirement"))
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


def check_type_ref(
    raw_type: str,
    index: ProjectIndex,
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


__all__ = ["check_datasource", "check_type", "check_type_ref"]
