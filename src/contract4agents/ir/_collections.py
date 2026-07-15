"""Small immutable collection primitives for canonical IR values."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from typing import Generic, TypeAlias, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class FrozenMap(Mapping[K, V], Generic[K, V]):
    """An insertion-ordered mapping backed only by an immutable tuple."""

    __slots__ = ("_items",)

    def __init__(self, values: Mapping[K, V] | Iterable[tuple[K, V]] = ()) -> None:
        items = tuple(values.items()) if isinstance(values, Mapping) else tuple(values)
        for index, (key, _value) in enumerate(items):
            if any(existing_key == key for existing_key, _existing_value in items[:index]):
                raise ValueError(f"Duplicate frozen-map key: {key!r}")
        self._items = items

    def __getitem__(self, key: K) -> V:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[K]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(self._items)

    def __repr__(self) -> str:
        return f"FrozenMap({self._items!r})"

    def items_tuple(self) -> tuple[tuple[K, V], ...]:
        """Return the immutable backing items for deterministic consumers."""

        return self._items


JsonScalar: TypeAlias = None | bool | int | float | str
FrozenJsonValue: TypeAlias = JsonScalar | tuple["FrozenJsonValue", ...] | FrozenMap[str, "FrozenJsonValue"]


def freeze_json(value: object) -> FrozenJsonValue:
    """Validate and recursively freeze a JSON-compatible value."""

    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical JSON values cannot contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        frozen_items: list[tuple[str, FrozenJsonValue]] = []
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("Canonical JSON object keys must be strings")
            frozen_items.append((key, freeze_json(child)))
        return FrozenMap(frozen_items)
    if isinstance(value, list | tuple):
        return tuple(freeze_json(child) for child in value)
    raise TypeError(f"Value of type {type(value).__name__} is not canonical JSON")


__all__ = ["FrozenJsonValue", "FrozenMap", "JsonScalar", "freeze_json"]
