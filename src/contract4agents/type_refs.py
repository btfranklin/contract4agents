"""Shared helpers for Contract4Agents type-reference strings."""

from __future__ import annotations

import re

BUILTIN_TYPES = frozenset({"str", "int", "float", "bool", "AgentRef"})

_BOUNDED_NUMERIC_RE = re.compile(r"(float|int)\s+between\s+([0-9.]+)\s+and\s+([0-9.]+)")
_LITERAL_RE = re.compile(r'"([^"]+)"')


def canonical_type_name(raw_type: str) -> str:
    """Return the scalar type name under nullable, collection, and bounds wrappers."""
    value = _strip_nullable(raw_type)
    member_type = collection_member_type(value)
    if member_type is not None:
        return canonical_type_name(member_type)
    bounds = numeric_bounds(value)
    if bounds is not None:
        return bounds[0]
    return value


def collection_member_type(raw_type: str) -> str | None:
    value = _strip_nullable(raw_type)
    if value.endswith("[]"):
        return value[:-2].strip()
    if value.startswith("list[") and value.endswith("]"):
        return value[5:-1].strip()
    return None


def literal_values(raw_type: str) -> list[str]:
    return _LITERAL_RE.findall(raw_type)


def is_literal_type(raw_type: str) -> bool:
    return bool(literal_values(raw_type))


def is_literal_union(raw_type: str) -> bool:
    return is_literal_type(raw_type) and "|" in raw_type


def numeric_bounds(raw_type: str) -> tuple[str, float, float] | None:
    match = _BOUNDED_NUMERIC_RE.fullmatch(_strip_nullable(raw_type))
    if match is None:
        return None
    base, minimum, maximum = match.groups()
    return base, float(minimum), float(maximum)


def is_bounded_numeric_type(raw_type: str) -> bool:
    return numeric_bounds(raw_type) is not None


def is_builtin_type(raw_type: str) -> bool:
    return canonical_type_name(raw_type) in BUILTIN_TYPES


def referenced_type_names(raw_type: str) -> set[str]:
    member_type = collection_member_type(raw_type)
    if member_type is not None:
        return referenced_type_names(member_type)
    if is_literal_type(raw_type) or is_bounded_numeric_type(raw_type):
        return set()
    canonical = canonical_type_name(raw_type)
    if not canonical or canonical in BUILTIN_TYPES:
        return set()
    return {canonical}


def _strip_nullable(raw_type: str) -> str:
    return raw_type.strip().rstrip("?").strip()


__all__ = [
    "BUILTIN_TYPES",
    "canonical_type_name",
    "collection_member_type",
    "is_bounded_numeric_type",
    "is_builtin_type",
    "is_literal_type",
    "is_literal_union",
    "literal_values",
    "numeric_bounds",
    "referenced_type_names",
]
