"""Schema signature helpers for drift checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def schema_signature(schema: Mapping[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    return {
        "required": sorted(item for item in required if isinstance(item, str)),
        "properties": {
            str(name): _normalize_schema_fragment(value)
            for name, value in sorted(properties.items())
            if isinstance(value, Mapping)
        },
    }


def _normalize_schema_fragment(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_schema_fragment(child)
            for key, child in sorted(value.items())
            if key not in {"title", "description", "default", "examples"}
        }
    if isinstance(value, list):
        return [_normalize_schema_fragment(item) for item in value]
    return value


__all__ = ["schema_signature"]
