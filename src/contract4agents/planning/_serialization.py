"""Deterministic native-object-free materialization-plan serialization."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import TypeAlias, cast

from contract4agents.ir import (
    ListTypeRef,
    MapTypeRef,
    NamedTypeRef,
    NullableTypeRef,
    PrimitiveTypeRef,
    SemanticId,
    format_type_ref,
)
from contract4agents.planning._models import MaterializationPlan

PlanJsonScalar: TypeAlias = None | bool | int | float | str
PlanJsonValue: TypeAlias = PlanJsonScalar | list["PlanJsonValue"] | dict[str, "PlanJsonValue"]


def materialization_plan_data(
    plan: MaterializationPlan,
    *,
    include_digest: bool = True,
) -> dict[str, PlanJsonValue]:
    """Return deterministic JSON data, optionally including its derived digest."""

    value = _plan_value(plan)
    if not isinstance(value, dict):
        raise TypeError("Materialization plan must serialize to a JSON object")
    if include_digest:
        value["plan_digest"] = compute_plan_digest(plan)
    return value


def canonical_materialization_plan_json(plan: MaterializationPlan) -> str:
    return _json(materialization_plan_data(plan))


def compute_plan_digest(plan: MaterializationPlan) -> str:
    payload = _json(materialization_plan_data(plan, include_digest=False)).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _json(value: dict[str, PlanJsonValue]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _plan_value(value: object) -> PlanJsonValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Materialization plans cannot contain NaN or infinity")
        return value
    if isinstance(value, SemanticId):
        return str(value)
    if isinstance(value, PrimitiveTypeRef | NamedTypeRef | NullableTypeRef | ListTypeRef | MapTypeRef):
        return format_type_ref(value)
    if isinstance(value, Mapping):
        result: dict[str, PlanJsonValue] = {}
        for raw_key, child in sorted(value.items(), key=lambda item: str(item[0])):
            if isinstance(raw_key, SemanticId | str):
                key = str(raw_key)
            else:
                raise TypeError(f"Materialization-plan key has unsupported type {type(raw_key).__name__}")
            result[key] = _plan_value(child)
        return result
    if isinstance(value, tuple | list):
        return [_plan_value(child) for child in value]
    if is_dataclass(value) and not isinstance(value, type):
        result = {}
        for data_field in fields(value):
            if data_field.metadata.get("canonical", True) is False:
                continue
            result[data_field.name] = _plan_value(cast(object, getattr(value, data_field.name)))
        return result
    if callable(value):
        raise TypeError("Materialization plans cannot contain callables")
    raise TypeError(f"Materialization plans cannot contain values of type {type(value).__name__}")


__all__ = [
    "PlanJsonValue",
    "canonical_materialization_plan_json",
    "compute_plan_digest",
    "materialization_plan_data",
]
