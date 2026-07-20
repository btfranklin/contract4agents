"""Small strict JSON decoding primitives for versioned public artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast


def json_object(label: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} must be an object with string keys")
    return cast(Mapping[str, object], value)


def json_array(label: str, value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    return value


def json_string(label: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    return value


def json_strings(label: str, value: object) -> tuple[str, ...]:
    return tuple(json_string(label, item) for item in json_array(label, value))


def require_exact_keys(
    label: str,
    payload: Mapping[str, object],
    required: set[str],
) -> None:
    missing = sorted(required - set(payload))
    unknown = sorted(set(payload) - required)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise ValueError(f"Invalid {label} keys: {'; '.join(details)}")


__all__ = [
    "json_array",
    "json_object",
    "json_string",
    "json_strings",
    "require_exact_keys",
]
