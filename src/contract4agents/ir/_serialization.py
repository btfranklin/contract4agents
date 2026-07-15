"""Canonical JSON serialization and semantic digest helpers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import TypeAlias, cast

from contract4agents.ir._ids import SemanticId
from contract4agents.ir._model import CanonicalIR
from contract4agents.ir._type_refs import (
    ListTypeRef,
    MapTypeRef,
    NamedTypeRef,
    NullableTypeRef,
    PrimitiveTypeRef,
    format_type_ref,
)

CanonicalJsonScalar: TypeAlias = None | bool | int | float | str
CanonicalJsonValue: TypeAlias = (
    CanonicalJsonScalar | list["CanonicalJsonValue"] | dict[str, "CanonicalJsonValue"]
)


def canonical_ir_data(ir: CanonicalIR) -> dict[str, CanonicalJsonValue]:
    """Return the span-free JSON data that defines the contract identity."""

    value = _canonical_value(ir)
    if not isinstance(value, dict):
        raise TypeError("Canonical IR must serialize to a JSON object")
    return value


def canonical_ir_json(ir: CanonicalIR) -> str:
    """Serialize IR with stable keys, UTF-8 characters, and no insignificant whitespace."""

    return json.dumps(
        canonical_ir_data(ir),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def contract_digest(ir: CanonicalIR) -> str:
    """Return the prefixed SHA-256 digest of canonical IR JSON."""

    encoded = canonical_ir_json(ir).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _canonical_value(value: object) -> CanonicalJsonValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical IR cannot contain NaN or infinity")
        return value
    if isinstance(value, SemanticId):
        return str(value)
    if isinstance(value, PrimitiveTypeRef | NamedTypeRef | NullableTypeRef | ListTypeRef | MapTypeRef):
        return format_type_ref(value)
    if isinstance(value, Mapping):
        result: dict[str, CanonicalJsonValue] = {}
        pairs = sorted(value.items(), key=lambda item: str(item[0]))
        for raw_key, child in pairs:
            if isinstance(raw_key, SemanticId):
                key = str(raw_key)
            elif isinstance(raw_key, str):
                key = raw_key
            else:
                raise TypeError(f"Canonical IR map key has unsupported type {type(raw_key).__name__}")
            result[key] = _canonical_value(child)
        return result
    if isinstance(value, tuple | list):
        return [_canonical_value(child) for child in value]
    if is_dataclass(value) and not isinstance(value, type):
        result = {}
        for data_field in fields(value):
            if data_field.metadata.get("canonical", True) is False:
                continue
            child = cast(object, getattr(value, data_field.name))
            result[data_field.name] = _canonical_value(child)
        return result
    if callable(value):
        raise TypeError("Canonical IR cannot contain callables")
    raise TypeError(f"Canonical IR cannot contain values of type {type(value).__name__}")


__all__ = ["CanonicalJsonValue", "canonical_ir_data", "canonical_ir_json", "contract_digest"]
