"""Datasource and runtime context internals for Contract4Agents."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from contract4agents.runtime._errors import (
    AmbiguousDatasource,
    ContractRuntimeError,
    DatasourceExecutionFailed,
    DatasourcePermissionDenied,
    DatasourceResolutionCycle,
    MissingContextSlot,
)
from contract4agents.runtime._trace import TraceRecorder


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


@dataclass(frozen=True)
class _DatasourceProof:
    satisfiable: bool
    cycle: tuple[str, ...] | None = None


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
        return [item for item in self._items.values() if item.produces == type_name]


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
        all_candidates = registry.by_output(type_name)
        candidates = _allowed_datasources(all_candidates, allowed_datasources)
        if all_candidates and not candidates:
            raise DatasourcePermissionDenied(type_name)
        proofs = [
            (
                candidate,
                self._requirements_are_resolvable(
                    candidate,
                    registry,
                    allowed_datasources,
                    resolving=(type_name,),
                ),
            )
            for candidate in candidates
        ]
        candidates = [candidate for candidate, proof in proofs if proof.satisfiable]
        if not candidates:
            for candidate, proof in proofs:
                if proof.cycle:
                    return candidate
            raise MissingContextSlot(type_name)
        if len(candidates) > 1:
            raise AmbiguousDatasource(type_name, [candidate.name for candidate in candidates])
        return candidates[0]

    def _requirements_are_resolvable(
        self,
        candidate: DatasourceSpec,
        registry: DatasourceRegistry,
        allowed_datasources: Collection[str] | None,
        resolving: tuple[str, ...],
    ) -> _DatasourceProof:
        cycle: tuple[str, ...] | None = None
        for required in candidate.requires:
            proof = self._type_is_resolvable(required, registry, allowed_datasources, resolving)
            if proof.satisfiable:
                continue
            if proof.cycle and cycle is None:
                cycle = proof.cycle
            return _DatasourceProof(False, cycle)
        return _DatasourceProof(True)

    def _type_is_resolvable(
        self,
        type_name: str,
        registry: DatasourceRegistry,
        allowed_datasources: Collection[str] | None,
        resolving: tuple[str, ...],
    ) -> _DatasourceProof:
        if type_name in self.values:
            return _DatasourceProof(True)
        if type_name in resolving:
            return _DatasourceProof(False, (*resolving, type_name))

        all_candidates = registry.by_output(type_name)
        candidates = _allowed_datasources(all_candidates, allowed_datasources)
        if not candidates:
            return _DatasourceProof(False)

        cycle: tuple[str, ...] | None = None
        for candidate in candidates:
            proof = self._requirements_are_resolvable(
                candidate,
                registry,
                allowed_datasources,
                resolving=(*resolving, type_name),
            )
            if proof.satisfiable:
                return proof
            if proof.cycle and cycle is None:
                cycle = proof.cycle
        return _DatasourceProof(False, cycle)

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
        cached = self._cached_datasource_value(type_name, datasource_spec)
        if cached:
            return cached
        return await self._execute_datasource(type_name, datasource_spec)

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

    def _cached_datasource_value(self, type_name: str, datasource_spec: DatasourceSpec) -> ContextValue | None:
        cache = self._cache_for(datasource_spec)
        if cache is None or datasource_spec.name not in cache:
            return None
        self.trace.record("datasource.resolved", datasource=datasource_spec.name, produces=type_name, cache="hit")
        value = cache[datasource_spec.name]
        self.values[type_name] = value
        return value

    async def _execute_datasource(self, type_name: str, datasource_spec: DatasourceSpec) -> ContextValue:
        self.trace.record("datasource.started", datasource=datasource_spec.name, produces=type_name)
        ds_context = DatasourceContext(self.values, self.trace, self._cache_for(datasource_spec))
        result = datasource_spec.func(ds_context)
        if inspect.isawaitable(result):
            result = await result
        context_value = _coerce_context_value(datasource_spec, result)
        self.values[type_name] = context_value
        cache = self._cache_for(datasource_spec)
        if cache is not None:
            cache[datasource_spec.name] = context_value
        self.trace.record("datasource.resolved", datasource=datasource_spec.name, produces=type_name, cache="miss")
        return context_value

    def _cache_for(self, datasource_spec: DatasourceSpec) -> dict[str, ContextValue] | None:
        if datasource_spec.cache == "none":
            return None
        if datasource_spec.cache == "thread":
            return self.thread_cache
        return self.datasource_cache


def _coerce_context_value(spec: DatasourceSpec, result: Any) -> ContextValue:
    if isinstance(result, ContextValue):
        return result
    if spec.render:
        rendered = spec.render(result)
    else:
        raise DatasourceExecutionFailed(spec.name, "datasource returned a raw value without a renderer")
    return ContextValue(spec.produces, result, rendered, spec.name, {})


def _allowed_datasources(
    candidates: list[DatasourceSpec], allowed_datasources: Collection[str] | None
) -> list[DatasourceSpec]:
    if allowed_datasources is None:
        return candidates
    allowed = set(allowed_datasources)
    return [candidate for candidate in candidates if candidate.name in allowed]


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
