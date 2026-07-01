"""JSON Schema generation for Contract4Agents type declarations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from contract4agents.ast import FieldDef, TypeDef


def type_to_schema(type_def: TypeDef, type_defs: Mapping[str, TypeDef] | None = None) -> dict[str, Any]:
    schema = _type_to_schema(type_def, include_schema=True)
    if type_defs is not None:
        defs = _referenced_defs(type_def, type_defs)
        if defs:
            schema["$defs"] = defs
    return schema


def _type_to_schema(type_def: TypeDef, *, include_schema: bool) -> dict[str, Any]:
    required: list[str] = []
    properties: dict[str, Any] = {}
    for field in type_def.fields:
        properties[field.name] = field_to_schema(field)
        if not field.nullable and field.default is None:
            required.append(field.name)
    schema: dict[str, Any] = {
        "title": type_def.name,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if include_schema:
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
    if required:
        schema["required"] = required
    return schema


def field_to_schema(field: FieldDef) -> dict[str, Any]:
    schema = _raw_type_to_schema(field.type_name)
    if field.nullable:
        schema = {"anyOf": [schema, {"type": "null"}]}
    if field.default is not None:
        schema["default"] = _coerce_default(field.default)
    return schema


def _raw_type_to_schema(raw_type: str) -> dict[str, Any]:
    value = raw_type.strip()
    if value.endswith("[]"):
        return {"type": "array", "items": _raw_type_to_schema(value[:-2])}
    if value.startswith("list[") and value.endswith("]"):
        return {"type": "array", "items": _raw_type_to_schema(value[5:-1])}
    if _literal_values(value):
        return {"type": "string", "enum": _literal_values(value)}
    between = re.fullmatch(r"(float|int)\s+between\s+([0-9.]+)\s+and\s+([0-9.]+)", value)
    if between:
        base, minimum, maximum = between.groups()
        return {
            "type": "number" if base == "float" else "integer",
            "minimum": float(minimum),
            "maximum": float(maximum),
        }
    primitive = {
        "str": {"type": "string"},
        "int": {"type": "integer"},
        "float": {"type": "number"},
        "bool": {"type": "boolean"},
        "AgentRef": {"type": "string"},
    }.get(value)
    if primitive:
        return dict(primitive)
    return {"$ref": f"#/$defs/{value}"}


def _referenced_defs(type_def: TypeDef, type_defs: Mapping[str, TypeDef]) -> dict[str, Any]:
    defs: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()

    def add_ref(name: str) -> None:
        target = type_defs.get(name)
        if target is None or target.source != "native" or name in defs or name in visiting:
            return
        visiting.add(name)
        defs[name] = _type_to_schema(target, include_schema=False)
        for field in target.fields:
            for nested_name in _referenced_type_names(field.type_name):
                add_ref(nested_name)
        visiting.remove(name)

    for field in type_def.fields:
        for ref_name in _referenced_type_names(field.type_name):
            add_ref(ref_name)

    return {name: defs[name] for name in sorted(defs)}


def _referenced_type_names(raw_type: str) -> set[str]:
    value = raw_type.strip().rstrip("?")
    if value.endswith("[]"):
        return _referenced_type_names(value[:-2])
    if value.startswith("list[") and value.endswith("]"):
        return _referenced_type_names(value[5:-1])
    if _literal_values(value) or re.fullmatch(r"(float|int)\s+between\s+([0-9.]+)\s+and\s+([0-9.]+)", value):
        return set()
    if value in {"str", "int", "float", "bool", "AgentRef"}:
        return set()
    return {value} if value else set()


def _literal_values(raw_type: str) -> list[str]:
    return re.findall(r'"([^"]+)"', raw_type)


def _coerce_default(raw: str) -> object:
    value = raw.strip()
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
