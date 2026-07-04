"""JSON Schema generation for Contract4Agents type declarations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contract4agents.ast import FieldDef, TypeDef
from contract4agents.type_refs import (
    canonical_type_name,
    collection_member_type,
    is_builtin_type,
    literal_values,
    numeric_bounds,
    referenced_type_names,
)


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
    if value.endswith("?"):
        return {"anyOf": [_raw_type_to_schema(value[:-1].strip()), {"type": "null"}]}
    member_type = collection_member_type(value)
    if member_type is not None:
        return {"type": "array", "items": _raw_type_to_schema(member_type)}
    values = literal_values(value)
    if values:
        return {"type": "string", "enum": values}
    bounds = numeric_bounds(value)
    if bounds is not None:
        base, minimum, maximum = bounds
        return {
            "type": "number" if base == "float" else "integer",
            "minimum": minimum,
            "maximum": maximum,
        }
    primitive_schemas = {
        "str": {"type": "string"},
        "int": {"type": "integer"},
        "float": {"type": "number"},
        "bool": {"type": "boolean"},
        "AgentRef": {"type": "string"},
    }
    primitive = primitive_schemas.get(canonical_type_name(value)) if is_builtin_type(value) else None
    if primitive:
        return dict(primitive)
    return {"$ref": f"#/$defs/{canonical_type_name(value)}"}


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
            for nested_name in referenced_type_names(field.type_name):
                add_ref(nested_name)
        visiting.remove(name)

    for field in type_def.fields:
        for ref_name in referenced_type_names(field.type_name):
            add_ref(ref_name)

    return {name: defs[name] for name in sorted(defs)}


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
