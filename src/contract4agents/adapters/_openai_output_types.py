"""Pydantic output type generation for the OpenAI adapter."""

from __future__ import annotations

from typing import Any, cast

from contract4agents.adapters._openai_types import OpenAIAgentFactoryError
from contract4agents.compiler import CompilerArtifacts
from contract4agents.runtime import load_python_ref


def build_openai_output_type_registry(artifacts: CompilerArtifacts) -> dict[str, type[Any]]:
    """Generate Pydantic v2 output types from compiled Contract4Agents JSON Schemas."""
    try:
        from pydantic import BaseModel, ConfigDict, Field, create_model
    except Exception as exc:  # noqa: BLE001 - optional OpenAI adapter dependency boundary.
        raise OpenAIAgentFactoryError("Pydantic v2 is required to generate OpenAI output types") from exc

    schemas = artifacts["schemas"]
    imported_types = {
        binding["type"]: binding["python_ref"]
        for binding in artifacts["type_bindings"]
        if binding["source"] == "python" and binding["python_ref"] is not None
    }
    built: dict[str, type[Any]] = {}
    resolving: set[str] = set()

    def build_model(name: str) -> type[Any]:
        if name in built:
            return built[name]
        if name in imported_types:
            try:
                model = load_python_ref(imported_types[name])
            except Exception as exc:
                raise OpenAIAgentFactoryError(f"Could not import Pydantic output type `{name}`") from exc
            if not isinstance(model, type) or not issubclass(model, BaseModel):
                raise OpenAIAgentFactoryError(f"Imported output type `{name}` is not a Pydantic BaseModel")
            built[name] = cast(type[Any], model)
            return built[name]
        if name in resolving:
            raise OpenAIAgentFactoryError(f"Cannot generate recursive OpenAI output type `{name}`")
        schema = schemas.get(name)
        if schema is None:
            raise OpenAIAgentFactoryError(f"No schema compiled for `{name}`")
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise OpenAIAgentFactoryError(f"Cannot generate OpenAI output type `{name}` from non-object schema")
        resolving.add(name)
        required = set(_string_list(schema.get("required", [])))
        fields: dict[str, tuple[Any, Any]] = {}
        for field_name, field_schema in schema["properties"].items():
            if not isinstance(field_schema, dict):
                raise OpenAIAgentFactoryError(f"Cannot generate field `{name}.{field_name}` from non-object schema")
            annotation, field_kwargs = annotation_for(field_schema)
            if "default" in field_schema:
                default: Any = field_schema["default"]
            elif field_name in required:
                default = ...
            else:
                default = None
            fields[str(field_name)] = (annotation, Field(default, **field_kwargs))
        create_model_any = cast(Any, create_model)
        model = create_model_any(
            name,
            __config__=ConfigDict(extra="forbid"),
            __module__="contract4agents.adapters.openai.generated",
            **fields,
        )
        built[name] = cast(type[Any], model)
        resolving.remove(name)
        return built[name]

    def annotation_for(schema: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        nullable = _nullable_schema(schema)
        if nullable is not None:
            annotation, kwargs = annotation_for(nullable)
            return annotation | None, kwargs
        if "$ref" in schema:
            ref_name = _schema_ref_name(str(schema["$ref"]))
            return build_model(ref_name), {}
        if "enum" in schema:
            values = _string_list(schema["enum"])
            if not values:
                raise OpenAIAgentFactoryError("Cannot generate OpenAI output type from empty enum")
            from typing import Literal as TypingLiteral

            return TypingLiteral.__getitem__(tuple(values)), {}
        schema_type = schema.get("type")
        if schema_type == "array":
            items = schema.get("items")
            if not isinstance(items, dict):
                raise OpenAIAgentFactoryError("Cannot generate OpenAI output type from array without item schema")
            item_annotation, _ = annotation_for(items)
            return list.__class_getitem__(item_annotation), {}
        if schema_type == "string":
            return str, {}
        if schema_type == "integer":
            return int, _numeric_constraints(schema)
        if schema_type == "number":
            return float, _numeric_constraints(schema)
        if schema_type == "boolean":
            return bool, {}
        raise OpenAIAgentFactoryError(f"Cannot generate OpenAI output type from unsupported schema `{schema}`")

    for type_name in schemas:
        build_model(type_name)
    return built


def _schema_ref_name(ref: str) -> str:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        raise OpenAIAgentFactoryError(f"Unsupported schema reference `{ref}`")
    return ref.removeprefix(prefix)


def _nullable_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    any_of = schema.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2:
        return None
    non_null = [item for item in any_of if isinstance(item, dict) and item.get("type") != "null"]
    nulls = [item for item in any_of if isinstance(item, dict) and item.get("type") == "null"]
    if len(non_null) == 1 and len(nulls) == 1:
        return cast(dict[str, Any], non_null[0])
    return None


def _numeric_constraints(schema: dict[str, Any]) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    if "minimum" in schema:
        constraints["ge"] = schema["minimum"]
    if "maximum" in schema:
        constraints["le"] = schema["maximum"]
    return constraints


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OpenAIAgentFactoryError("Expected a list of strings in generated schema")
    return list(value)


__all__ = ["build_openai_output_type_registry"]
