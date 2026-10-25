"""Runtime checks for model classes emitted by the Python generator."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel


def generated_model(namespace: Mapping[str, object], name: str) -> type[BaseModel]:
    value = namespace[name]
    assert isinstance(value, type) and issubclass(value, BaseModel)
    return value
