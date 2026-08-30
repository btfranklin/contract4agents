"""Shared execution boundary for bound Python host callables."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from contract4agents.materialization._types import normalize_structural_value


@dataclass(frozen=True)
class HostCallResult:
    """Validated host output in Python and provider transport forms."""

    validated_value: object
    json_value: object


@dataclass(frozen=True)
class HostCallableBoundary:
    """Validate, execute, and validate one bound Python host callable."""

    display_name: str
    implementation: Callable[..., object]
    input_adapter: TypeAdapter[Any] | None
    output_adapter: TypeAdapter[Any]

    @classmethod
    def create(
        cls,
        display_name: str,
        implementation: Callable[..., object],
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> HostCallableBoundary:
        """Create a boundary from generated contract types."""

        return cls(
            display_name=display_name,
            implementation=implementation,
            input_adapter=TypeAdapter(input_type) if input_type is not None else None,
            output_adapter=output_adapter,
        )

    def validate_arguments(self, raw: object) -> dict[str, object]:
        """Return strict generated arguments in Python mode."""

        if not isinstance(raw, Mapping):
            raise ValueError(f"Host callable `{self.display_name}` arguments must be an object")
        if self.input_adapter is None:
            if raw:
                raise ValueError(f"Host callable `{self.display_name}` does not accept arguments")
            return {}
        validated = self.input_adapter.validate_python(raw)
        if isinstance(validated, BaseModel):
            dumped = validated.model_dump(mode="python")
        else:
            dumped = self.input_adapter.dump_python(validated, mode="python")
        if not isinstance(dumped, Mapping):
            raise TypeError(f"Host callable `{self.display_name}` arguments must validate to an object")
        return dict(cast(Mapping[str, object], dumped))

    async def invoke(self, raw: object) -> HostCallResult:
        """Validate arguments and invoke the host callable once."""

        return await self.invoke_validated(self.validate_arguments(raw))

    async def invoke_validated(
        self,
        arguments: Mapping[str, object],
    ) -> HostCallResult:
        """Invoke the host callable with arguments that this boundary validated."""

        if _is_async_callable(self.implementation):
            raw_result = self.implementation(**dict(arguments))
        else:
            raw_result = await asyncio.to_thread(
                self.implementation,
                **dict(arguments),
            )
        if inspect.isawaitable(raw_result):
            raw_result = await raw_result
        validated = self.output_adapter.validate_python(
            normalize_structural_value(raw_result),
            strict=True,
        )
        return HostCallResult(
            validated_value=validated,
            json_value=self.output_adapter.dump_python(validated, mode="json"),
        )


def _is_async_callable(implementation: Callable[..., object]) -> bool:
    if inspect.iscoroutinefunction(implementation):
        return True
    return inspect.iscoroutinefunction(cast(Any, implementation).__call__)


__all__ = ["HostCallableBoundary", "HostCallResult"]
