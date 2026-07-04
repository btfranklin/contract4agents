"""Datasource and runtime context internals for Contract4Agents."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from contract4agents._datasource_resolution import DatasourceCandidate, resolve_datasource_type
from contract4agents.runtime._errors import (
    AmbiguousDatasource,
    ContractRuntimeError,
    DatasourceExecutionFailed,
    DatasourcePermissionDenied,
    DatasourceResolutionCycle,
    MissingContextSlot,
)
from contract4agents.runtime._trace import TraceRecorder
from contract4agents.type_refs import canonical_type_name


@dataclass(frozen=True)
class ContextValue:
    type_name: str
    value: Any
    rendered: str
    source: str
    provenance: dict[str, Any] = field(default_factory=dict)
    sensitive: bool = False


@dataclass(frozen=True)
class RuntimeStateValue:
    type_name: str
    value: Any
    source: str
    provenance: dict[str, Any] = field(default_factory=dict)
    sensitive: bool = True


class DatasourceContext:
    def __init__(
        self,
        values: dict[str, ContextValue],
        trace: TraceRecorder,
        cache: dict[str, ContextValue] | None = None,
    ) -> None:
        self.values = values
        self.trace_recorder = trace
        self.cache = cache if cache is not None else {}

    def get(self, type_name: str) -> ContextValue:
        if type_name not in self.values:
            raise MissingContextSlot(type_name)
        return self.values[type_name]

    def value(
        self,
        *,
        type_name: str,
        value: Any,
        rendered: str,
        source: str,
        provenance: dict[str, Any] | None = None,
        sensitive: bool = False,
    ) -> ContextValue:
        return ContextValue(type_name, value, rendered, source, provenance or {}, sensitive)

    def trace(self, event_type: str, **data: Any) -> None:
        self.trace_recorder.record(event_type, **data)

    def redact(self, value: str) -> str:
        return "[redacted]" if value else value


DatasourceCallable = Callable[[DatasourceContext], ContextValue | Awaitable[ContextValue] | Any]


@dataclass(frozen=True)
class DatasourceSpec:
    name: str
    produces: str
    requires: list[str]
    func: DatasourceCallable
    render: Callable[[Any], str] | None = None
    cache: Literal["none", "run", "thread"] = "run"


def datasource(
    *,
    produces: str,
    requires: list[str] | None = None,
    render: str | Callable[[Any], str] = "markdown",
    cache: Literal["none", "run", "thread"] = "run",
) -> Callable[[DatasourceCallable], DatasourceCallable]:
    def decorate(func: DatasourceCallable) -> DatasourceCallable:
        cast(Any, func).__contract_datasource__ = {
            "produces": produces,
            "requires": requires or [],
            "render": render,
            "cache": cache,
        }
        return func

    return decorate


class DatasourceRegistry:
    def __init__(self) -> None:
        self._items: dict[str, DatasourceSpec] = {}

    def register(self, name: str, spec: DatasourceSpec) -> None:
        self._items[name] = spec

    def register_func(self, name: str, func: DatasourceCallable) -> None:
        meta = getattr(func, "__contract_datasource__", None)
        if not meta:
            raise DatasourceExecutionFailed(name, "missing @datasource metadata")
        renderer = meta["render"] if callable(meta["render"]) else None
        self.register(
            name,
            DatasourceSpec(name, meta["produces"], list(meta["requires"]), func, renderer, meta["cache"]),
        )

    def by_output(self, type_name: str) -> list[DatasourceSpec]:
        normalized = canonical_type_name(type_name)
        return [item for item in self._items.values() if canonical_type_name(item.produces) == normalized]

    def candidates(self) -> list[DatasourceCandidate]:
        return [
            DatasourceCandidate(item.name, item.produces, tuple(item.requires))
            for item in self._items.values()
        ]


class RuntimeContext:
    def __init__(
        self,
        values: dict[str, ContextValue] | None = None,
        hidden: dict[str, RuntimeStateValue] | None = None,
        trace: TraceRecorder | None = None,
        thread_cache: dict[str, ContextValue] | None = None,
    ) -> None:
        self.values = values or {}
        self.hidden = hidden or {}
        self.trace = trace or TraceRecorder()
        self.datasource_cache: dict[str, ContextValue] = {}
        self.thread_cache = thread_cache if thread_cache is not None else {}

    async def resolve(
        self,
        required: list[str],
        registry: DatasourceRegistry,
        allowed_datasources: Collection[str] | None = None,
    ) -> dict[str, ContextValue]:
        for type_name in required:
            await self.resolve_one(type_name, registry, allowed_datasources=allowed_datasources)
        return self.values

    async def resolve_one(
        self,
        type_name: str,
        registry: DatasourceRegistry,
        allowed_datasources: Collection[str] | None = None,
        _resolving: tuple[str, ...] = (),
    ) -> ContextValue:
        if type_name in self.values:
            return self.values[type_name]
        if type_name in _resolving:
            raise DatasourceResolutionCycle((*_resolving, type_name))
        datasource_spec = self._select_datasource(type_name, registry, allowed_datasources)
        try:
            return await self._resolve_datasource(
                type_name,
                datasource_spec,
                registry,
                allowed_datasources,
                _resolving,
            )
        except ContractRuntimeError as exc:
            self.trace.record(
                "datasource.failed", datasource=datasource_spec.name, produces=type_name, reason=str(exc)
            )
            raise
        except Exception as exc:
            self.trace.record(
                "datasource.failed", datasource=datasource_spec.name, produces=type_name, reason=str(exc)
            )
            raise DatasourceExecutionFailed(datasource_spec.name, str(exc)) from exc

    def rendered_context(self) -> str:
        parts = []
        for key, value in sorted(self.values.items()):
            if not value.sensitive:
                parts.append(f"## {key}\n{value.rendered}")
        return "\n\n".join(parts)

    def _select_datasource(
        self,
        type_name: str,
        registry: DatasourceRegistry,
        allowed_datasources: Collection[str] | None,
    ) -> DatasourceSpec:
        resolution = resolve_datasource_type(
            type_name,
            available=self.values,
            datasources=registry.candidates(),
            allowed_datasources=allowed_datasources,
        )
        if resolution.status == "denied":
            raise DatasourcePermissionDenied(type_name)
        if resolution.status == "cycle":
            raise DatasourceResolutionCycle(resolution.cycle)
        if resolution.status == "ambiguous":
            raise AmbiguousDatasource(type_name, list(resolution.candidates))
        if resolution.status == "ok" and resolution.selected is not None:
            return registry._items[resolution.selected]
        if resolution.status == "missing":
            raise MissingContextSlot(type_name)
        raise MissingContextSlot(type_name)

    async def _resolve_datasource(
        self,
        type_name: str,
        datasource_spec: DatasourceSpec,
        registry: DatasourceRegistry,
        allowed_datasources: Collection[str] | None,
        resolving: tuple[str, ...],
    ) -> ContextValue:
        await self._resolve_datasource_requirements(
            type_name,
            datasource_spec,
            registry,
            allowed_datasources,
            resolving,
        )
        cache_key = self._datasource_cache_key(datasource_spec)
        cached = self._cached_datasource_value(type_name, datasource_spec, cache_key)
        if cached:
            return cached
        return await self._execute_datasource(type_name, datasource_spec, cache_key)

    async def _resolve_datasource_requirements(
        self,
        type_name: str,
        datasource_spec: DatasourceSpec,
        registry: DatasourceRegistry,
        allowed_datasources: Collection[str] | None,
        resolving: tuple[str, ...],
    ) -> None:
        for required in datasource_spec.requires:
            await self.resolve_one(
                required,
                registry,
                allowed_datasources=allowed_datasources,
                _resolving=(*resolving, type_name),
            )

    def _cached_datasource_value(
        self,
        type_name: str,
        datasource_spec: DatasourceSpec,
        cache_key: str,
    ) -> ContextValue | None:
        cache = self._cache_for(datasource_spec)
        if cache is None or cache_key not in cache:
            return None
        self.trace.record("datasource.resolved", datasource=datasource_spec.name, produces=type_name, cache="hit")
        value = cache[cache_key]
        _validate_context_value_type(datasource_spec, value)
        self.values[type_name] = value
        return value

    async def _execute_datasource(
        self,
        type_name: str,
        datasource_spec: DatasourceSpec,
        cache_key: str,
    ) -> ContextValue:
        self.trace.record("datasource.started", datasource=datasource_spec.name, produces=type_name)
        ds_context = DatasourceContext(self.values, self.trace, self._cache_for(datasource_spec))
        result = datasource_spec.func(ds_context)
        if inspect.isawaitable(result):
            result = await result
        context_value = _coerce_context_value(datasource_spec, result)
        self.values[type_name] = context_value
        cache = self._cache_for(datasource_spec)
        if cache is not None:
            cache[cache_key] = context_value
        self.trace.record("datasource.resolved", datasource=datasource_spec.name, produces=type_name, cache="miss")
        return context_value

    def _datasource_cache_key(self, datasource_spec: DatasourceSpec) -> str:
        requirements: list[dict[str, Any]] = []
        for required in datasource_spec.requires:
            value = self._context_value_for(required)
            requirements.append(
                {
                    "name": canonical_type_name(required),
                    "type_name": canonical_type_name(value.type_name),
                    "value": value.value,
                    "rendered": value.rendered,
                    "source": value.source,
                    "provenance": value.provenance,
                    "sensitive": value.sensitive,
                }
            )
        payload = {"datasource": datasource_spec.name, "requires": requirements}
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=repr)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return f"ds:{digest}"

    def _context_value_for(self, type_name: str) -> ContextValue:
        if type_name in self.values:
            return self.values[type_name]
        normalized = canonical_type_name(type_name)
        if normalized in self.values:
            return self.values[normalized]
        raise MissingContextSlot(type_name)

    def _cache_for(self, datasource_spec: DatasourceSpec) -> dict[str, ContextValue] | None:
        if datasource_spec.cache == "none":
            return None
        if datasource_spec.cache == "thread":
            return self.thread_cache
        return self.datasource_cache


def _coerce_context_value(spec: DatasourceSpec, result: Any) -> ContextValue:
    if isinstance(result, ContextValue):
        _validate_context_value_type(spec, result)
        return result
    if spec.render:
        rendered = spec.render(result)
    else:
        raise DatasourceExecutionFailed(spec.name, "datasource returned a raw value without a renderer")
    return ContextValue(spec.produces, result, rendered, spec.name, {})


def _validate_context_value_type(spec: DatasourceSpec, value: ContextValue) -> None:
    expected = canonical_type_name(spec.produces)
    actual = canonical_type_name(value.type_name)
    if actual != expected:
        raise DatasourceExecutionFailed(
            spec.name,
            f"datasource returned `{value.type_name}` but declares `{spec.produces}`",
        )


__all__ = [
    "ContextValue",
    "DatasourceCallable",
    "DatasourceContext",
    "DatasourceRegistry",
    "DatasourceSpec",
    "RuntimeContext",
    "RuntimeStateValue",
    "datasource",
]
