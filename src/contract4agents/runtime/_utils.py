"""Small runtime utility helpers for Contract4Agents internals."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Coroutine
from typing import Any


def load_python_ref(reference: str) -> Any:
    module_name, _, attr = reference.partition(":")
    if not module_name or not attr:
        raise ValueError(f"Invalid python reference: {reference}")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


__all__ = ["load_python_ref", "run_async"]
