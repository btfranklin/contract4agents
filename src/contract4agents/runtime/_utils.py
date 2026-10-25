"""Small runtime utility helpers for Contract4Agents internals."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Coroutine
from typing import Protocol, TypeVar

T = TypeVar("T")


ResultT_co = TypeVar("ResultT_co", covariant=True)


class PythonCallable(Protocol[ResultT_co]):
    """A callable loaded or constructed at a dynamic Python boundary."""

    def __call__(self, *args: object, **kwargs: object) -> ResultT_co: ...


def load_python_ref(reference: str) -> object:
    module_name, _, attr = reference.partition(":")
    if not module_name or not attr:
        raise ValueError(f"Invalid python reference: {reference}")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def run_async(coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


__all__ = ["PythonCallable", "load_python_ref", "run_async"]
