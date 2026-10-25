"""Narrow bridge from runtime contract annotations to Python and Pydantic.

Python has no static type for arbitrary runtime annotation expressions. Keep
that uncertainty here; callers use typed model classes and adapters.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo


def enum_annotation(values: tuple[str, ...]) -> object:
    return Literal.__getitem__(values)


def nullable_annotation(item: object) -> object:
    return cast(Any, item) | None


def list_annotation(item: object) -> object:
    return list.__class_getitem__(item)


def map_annotation(key: object, value: object) -> object:
    return dict.__class_getitem__((key, value))


def constrained_annotation(item: object, metadata: object) -> object:
    return Annotated[cast(Any, item), metadata]


def contract_model(name: str, fields: Mapping[str, tuple[object, FieldInfo]]) -> type[BaseModel]:
    factory = cast(Any, create_model)
    return cast(type[BaseModel], factory(
        name,
        __config__=ConfigDict(extra="forbid", strict=True, allow_inf_nan=False),
        __module__="contract4agents.generated",
        **fields,
    ))


def field_info(default: object) -> FieldInfo:
    return cast(FieldInfo, Field(default))
