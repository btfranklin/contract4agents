"""Provider-native conversion for immutable model options."""

from __future__ import annotations

from collections.abc import Mapping


def thaw_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Return model options with ordinary JSON container types."""

    return {str(name): _thaw_value(value) for name, value in values.items()}


def _thaw_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(name): _thaw_value(child) for name, child in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw_value(child) for child in value]
    return value


__all__ = ["thaw_mapping"]
