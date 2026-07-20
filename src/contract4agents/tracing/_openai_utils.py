"""Small duck-typed field helpers shared by OpenAI trace adapters."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Mapping
from datetime import datetime


def text_attr(value: object, name: str) -> str:
    item = getattr(value, name, None)
    if not isinstance(item, str) or not item:
        raise ValueError(f"OpenAI span `{name}` must be a non-empty string")
    return item


def optional_text_attr(value: object, name: str) -> str | None:
    item = getattr(value, name, None)
    return item if isinstance(item, str) and item else None


def timestamp(value: object) -> float:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return time.time()


def field_value(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def field_text(value: object, name: str) -> str | None:
    item = field_value(value, name)
    return item if isinstance(item, str) and item else None


def provider_model(response: object) -> str | None:
    for name in ("model", "model_name"):
        value = field_text(response, name)
        if value is not None:
            return value
    return None


def locator_tool(locator: Mapping[str, object]) -> object:
    tool = locator.get("tool")
    provider_tool = locator.get("provider_tool")
    if tool is not None and provider_tool is not None and tool != provider_tool:
        return None
    return tool if tool is not None else provider_tool


def batch_identity(response_ids: Iterable[str]) -> str:
    payload = "\n".join(response_ids).encode()
    return f"unscoped-{hashlib.sha256(payload).hexdigest()[:16]}"


__all__ = [
    "batch_identity",
    "field_text",
    "field_value",
    "locator_tool",
    "optional_text_attr",
    "provider_model",
    "text_attr",
    "timestamp",
]
