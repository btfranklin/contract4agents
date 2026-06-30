"""JSON Schema generation for Contract4Agents type declarations."""

from __future__ import annotations

import re
from typing import Any

from contract4agents.ast import FieldDef, TypeDef


def type_to_schema(type_def: TypeDef) -> dict[str, Any]:
    required: list[str] = []
    properties: dict[str, Any] = {}
    for field in type_def.fields:
        properties[field.name] = field_to_schema(field)
        if not field.nullable and field.default is None:
            required.append(field.name)
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": type_def.name,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
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
