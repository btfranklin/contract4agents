"""Project a contract schema onto Bedrock's native generation constraints."""

from __future__ import annotations

from collections.abc import Mapping

from contract4agents._strict_json import json_array, json_object

_HOST_BOUNDS = frozenset({
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "maxItems",
})
_MISSING = object()


def bedrock_output_schema(schema: Mapping[str, object], *, strict: bool = False) -> dict[str, object]:
    """Project generation constraints; the original schema remains the host boundary."""

    _validate_references(schema)
    optional_properties = 0
    union_properties = 0

    if strict and schema.get("type") != "object":
        raise ValueError("Bedrock strict schema output requires an object at the root")

    def project(source: Mapping[str, object]) -> dict[str, object]:
        nonlocal optional_properties, union_properties
        result: dict[str, object] = {}
        schema_type = source.get("type")
        has_object_type = schema_type == "object" or (
            strict and isinstance(schema_type, list) and "object" in schema_type
        )
        if has_object_type and source.get("additionalProperties") is not False:
            raise ValueError("Bedrock schema output requires closed objects")
        properties = source.get("properties")
        if isinstance(properties, Mapping):
            required = json_array("required properties", source.get("required", []))
            if strict:
                required_set = set(required)
                for name, child in properties.items():
                    if name not in required_set and not _optional_null_is_supported(child, schema):
                        raise ValueError(
                            "Bedrock strict schema output cannot preserve this optional field's default behavior"
                        )
                required = [*required, *(name for name in properties if name not in required)]
                result["required"] = required
            else:
                optional_properties += len(set(properties) - set(required))
            union_properties += sum(
                1 for child in properties.values()
                if isinstance(child, Mapping) and ("anyOf" in child or isinstance(child.get("type"), list))
            )
        for key, value in source.items():
            if strict and key == "oneOf":
                raise ValueError("Bedrock strict schema output does not support oneOf")
            if key in _HOST_BOUNDS:
                continue
            if strict and key == "required" and isinstance(properties, Mapping):
                result[key] = list(properties)
            elif key == "minItems":
                result[key] = 0 if value == 0 else 1
            elif key in {"properties", "$defs"}:
                result[key] = {
                    name: project(json_object("schema", child))
                    for name, child in json_object(key, value).items()
                }
            elif key == "items":
                result[key] = project(json_object("items schema", value))
            elif key in {"anyOf", "allOf"}:
                result[key] = [project(json_object("schema branch", child)) for child in json_array(key, value)]
            else:
                result[key] = value
        return result

    projected = project(schema)
    if optional_properties > 12:
        raise ValueError("Bedrock schema output supports at most 12 optional properties")
    if union_properties > 16:
        raise ValueError("Bedrock schema output supports at most 16 union properties")
    return projected


def _optional_null_is_supported(value: object, root: Mapping[str, object]) -> bool:
    if not isinstance(value, Mapping) or value.get("default", _MISSING) is not None:
        return False
    return _schema_allows_null(value, root, frozenset())


def _schema_allows_null(
    schema: Mapping[str, object], root: Mapping[str, object], active_references: frozenset[str],
) -> bool:
    schema_type = schema.get("type")
    if schema_type is not None:
        if schema_type != "null" and (not isinstance(schema_type, list) or "null" not in schema_type):
            return False

    enum = schema.get("enum")
    if isinstance(enum, list) and None not in enum:
        return False
    if "const" in schema and schema["const"] is not None:
        return False

    negated = schema.get("not")
    if isinstance(negated, Mapping) and _schema_allows_null(negated, root, active_references):
        return False

    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in active_references:
            return False
        target: object = root
        for segment in reference[2:].split("/"):
            key = segment.replace("~1", "/").replace("~0", "~")
            target = json_object("schema reference", target)[key]
        if not _schema_allows_null(
            json_object("schema reference", target), root, active_references | {reference},
        ):
            return False

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and not any(
        isinstance(branch, Mapping) and _schema_allows_null(branch, root, active_references)
        for branch in any_of
    ):
        return False

    all_of = schema.get("allOf")
    if isinstance(all_of, list) and not all(
        isinstance(branch, Mapping) and _schema_allows_null(branch, root, active_references)
        for branch in all_of
    ):
        return False

    return True


def _validate_references(schema: Mapping[str, object]) -> None:
    checked: set[str] = set()

    def visit(value: object, active: frozenset[str]) -> None:
        if isinstance(value, Mapping):
            reference = value.get("$ref")
            if isinstance(reference, str):
                if not reference.startswith("#/"):
                    raise ValueError("Bedrock schema output requires internal references")
                if reference in active:
                    raise ValueError("Bedrock schema output does not support recursive schemas")
                if reference not in checked:
                    target: object = schema
                    for segment in reference[2:].split("/"):
                        key = segment.replace("~1", "/").replace("~0", "~")
                        target = json_object("schema reference", target)[key]
                    visit(target, active | {reference})
                    checked.add(reference)
            for child in value.values():
                visit(child, active)
        elif isinstance(value, list):
            for child in value:
                visit(child, active)

    visit(schema, frozenset())
