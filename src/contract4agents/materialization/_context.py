"""Typed runtime resolution for declared datasource and external context slots."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId, semantic_id
from contract4agents.materialization._host_callables import HostCallableBoundary
from contract4agents.materialization._types import (
    build_parameter_model,
    type_adapter_for,
)
from contract4agents.planning import MaterializationPlan

if TYPE_CHECKING:
    from contract4agents.tracing import NormalizedTraceSink, TraceEvent, TraceSemanticRefs


class ContextResolutionError(RuntimeError):
    """A declared context value could not be safely resolved or validated."""

    def __init__(self, semantic_id: SemanticId, message: str) -> None:
        super().__init__(f"{semantic_id}: {message}")
        self.semantic_id = semantic_id


@dataclass(frozen=True)
class ResolvedContextValue:
    context_id: SemanticId
    agent_id: SemanticId
    origin: str
    origin_id: SemanticId
    value: object
    rendered: str
    from_cache: bool


class ContextRuntime:
    """Resolve the context portion of a materialized graph from canonical declarations."""

    def __init__(
        self,
        ir: CanonicalIR,
        plan: MaterializationPlan,
        implementations: FrozenMap[SemanticId, object],
        output_types: FrozenMap[str, type[object]],
        *,
        trace_sink: NormalizedTraceSink | None = None,
    ) -> None:
        from contract4agents.tracing import NoOpNormalizedTraceSink

        self.ir = ir
        self.plan = plan
        self.implementations = implementations
        self.output_types = output_types
        self.trace_sink = trace_sink or NoOpNormalizedTraceSink()
        self._host_boundaries = self._build_host_boundaries()
        self._run_cache: dict[tuple[str, str, str], ResolvedContextValue] = {}
        self._thread_cache: dict[tuple[str, str, str], ResolvedContextValue] = {}
        self._in_flight: dict[tuple[str, str, str, str], asyncio.Task[ResolvedContextValue]] = {}
        self._event_counter = 0

    def _build_host_boundaries(self) -> dict[SemanticId, HostCallableBoundary]:
        boundaries: dict[SemanticId, HostCallableBoundary] = {}
        for context in self.ir.contexts.values():
            origin_id = context.origin_id
            if origin_id is None or origin_id in boundaries:
                continue
            implementation = self.implementations.get(origin_id)
            if not callable(implementation):
                continue
            if context.origin == "datasource":
                capability = self.ir.capabilities[origin_id]
                input_type = build_parameter_model(
                    f"{capability.name.replace('.', '_')}Input",
                    capability.parameters,
                    self.output_types,
                )
                output_type = capability.output_type
                display_name = capability.name
            elif context.origin == "external":
                external = self.ir.external_contexts[origin_id]
                input_type = None
                output_type = external.output_type
                display_name = external.name
            else:
                continue
            boundaries[origin_id] = HostCallableBoundary.create(
                display_name,
                cast(Callable[..., object], implementation),
                input_type,
                type_adapter_for(output_type, self.output_types),
            )
        return boundaries

    async def resolve_agent(
        self,
        agent: str,
        inputs: Mapping[str, object],
        *,
        run_id: str,
        thread_id: str | None = None,
    ) -> FrozenMap[str, ResolvedContextValue]:
        """Resolve every local context slot for one typed agent invocation."""

        agent_id = semantic_id("agent", agent)
        agent_ir = self.ir.agents.get(agent_id)
        if agent_ir is None:
            raise KeyError(agent)
        if not run_id.strip():
            raise ValueError("run_id must be non-empty")
        resolved_inputs = _validate_parameters(
            f"{agent}Input",
            agent_ir.parameters,
            inputs,
            self.output_types,
            agent_id,
        )
        resolved: dict[str, ResolvedContextValue] = {}
        for context_id in agent_ir.context_ids:
            value = await self._resolve(
                context_id,
                resolved_inputs,
                resolved,
                run_id=run_id,
                thread_id=thread_id or run_id,
            )
            resolved[value.context_id.parts[-1]] = value
        return FrozenMap(resolved)

    def complete_run(self, run_id: str) -> None:
        """Release all runtime-owned state after one host run completes."""

        self._complete_scope("run", run_id)

    def complete_thread(self, thread_id: str) -> None:
        """Release all runtime-owned state after one host thread completes."""

        self._complete_scope("thread", thread_id)

    async def _resolve(
        self,
        context_id: SemanticId,
        inputs: Mapping[str, object],
        resolved_context: Mapping[str, ResolvedContextValue],
        *,
        run_id: str,
        thread_id: str,
    ) -> ResolvedContextValue:
        context = self.ir.contexts[context_id]
        if context.origin_id is None or context.origin not in {"datasource", "external"}:
            raise ContextResolutionError(
                context_id,
                "agent-local context must use a declared datasource or external origin",
            )
        boundary = self._host_boundaries.get(context.origin_id)
        if boundary is None:
            raise ContextResolutionError(context_id, "the materialized provider is not callable")

        if context.origin == "datasource":
            capability = self.ir.capabilities[context.origin_id]
            raw_arguments = {
                name: _resolve_mapping(expression, inputs, resolved_context)
                for name, expression in context.input_mappings.items()
            }
            try:
                arguments = boundary.validate_arguments(raw_arguments)
            except Exception as exc:
                raise ContextResolutionError(
                    context_id,
                    f"input validation failed ({type(exc).__name__})",
                ) from exc
            cache_scope = capability.cache or "none"
            render = capability.render or "json"
            sensitivity = "internal"
        else:
            arguments = boundary.validate_arguments({})
            cache_scope = "run"
            render = self.ir.external_contexts[context.origin_id].render
            sensitivity = self.ir.external_contexts[context.origin_id].sensitivity

        argument_key = json.dumps(_plain(arguments), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        cache = self._cache(cache_scope)
        cache_owner = thread_id if cache_scope == "thread" else run_id
        cache_key = (cache_owner, str(context_id), argument_key)
        cached = cache.get(cache_key) if cache is not None else None
        if cached is not None:
            result = ResolvedContextValue(
                cached.context_id,
                cached.agent_id,
                cached.origin,
                cached.origin_id,
                cached.value,
                cached.rendered,
                True,
            )
            self._emit(result, run_id, thread_id, cache_scope, render, sensitivity)
            return result

        if cache is None:
            result = await self._resolve_provider(
                context_id,
                arguments,
                boundary,
                run_id,
                thread_id,
                render,
            )
            self._emit(result, run_id, thread_id, cache_scope, render, sensitivity)
            return result

        in_flight_key = (cache_scope, *cache_key)
        task = self._in_flight.get(in_flight_key)
        reused = task is not None
        if task is None:
            task = asyncio.create_task(
                self._resolve_provider(
                    context_id,
                    arguments,
                    boundary,
                    run_id,
                    thread_id,
                    render,
                )
            )
            self._in_flight[in_flight_key] = task
            task.add_done_callback(partial(self._finish_resolution, in_flight_key))
        resolved = await asyncio.shield(task)
        result = ResolvedContextValue(
            resolved.context_id,
            resolved.agent_id,
            resolved.origin,
            resolved.origin_id,
            resolved.value,
            resolved.rendered,
            reused,
        )
        self._emit(result, run_id, thread_id, cache_scope, render, sensitivity)
        return result

    async def _resolve_provider(
        self,
        context_id: SemanticId,
        arguments: Mapping[str, object],
        boundary: HostCallableBoundary,
        run_id: str,
        thread_id: str,
        render: str,
    ) -> ResolvedContextValue:
        context = self.ir.contexts[context_id]
        if context.origin_id is None:
            raise ContextResolutionError(context_id, "context origin is not resolved")
        origin_id = context.origin_id
        try:
            value = (await boundary.invoke_validated(arguments)).validated_value
        except Exception as exc:
            self._emit_failure(context_id, context.agent_id, origin_id, run_id, thread_id, exc)
            raise ContextResolutionError(
                context_id,
                f"provider resolution or output validation failed ({type(exc).__name__})",
            ) from exc
        result = ResolvedContextValue(
            context_id,
            context.agent_id,
            context.origin,
            origin_id,
            value,
            _render(value, render),
            False,
        )
        return result

    def _finish_resolution(
        self,
        key: tuple[str, str, str, str],
        task: asyncio.Task[ResolvedContextValue],
    ) -> None:
        if not task.cancelled() and task.exception() is None:
            cache = self._cache(key[0])
            if cache is not None:
                cache[(key[1], key[2], key[3])] = task.result()
        if self._in_flight.get(key) is task:
            del self._in_flight[key]

    def _complete_scope(self, scope: str, owner: str) -> None:
        if not owner.strip():
            raise ValueError(f"{scope}_id must be non-empty")
        if any(key[0] == scope and key[1] == owner for key in self._in_flight):
            raise RuntimeError(f"Cannot complete {scope} `{owner}` while context resolution is active")
        if scope == "run":
            self._run_cache = {
                key: value for key, value in self._run_cache.items() if key[0] != owner
            }
        else:
            self._thread_cache = {
                key: value for key, value in self._thread_cache.items() if key[0] != owner
            }

    def _cache(self, scope: str) -> dict[tuple[str, str, str], ResolvedContextValue] | None:
        if scope == "run":
            return self._run_cache
        if scope == "thread":
            return self._thread_cache
        return None

    def _emit(
        self,
        result: ResolvedContextValue,
        run_id: str,
        thread_id: str,
        cache_scope: str,
        render: str,
        sensitivity: str,
    ) -> None:
        from contract4agents.tracing import TraceSemanticRefs

        capability_id = result.origin_id if result.origin == "datasource" else None
        self.trace_sink.emit(
            self._event(
                "datasource.resolved" if result.origin == "datasource" else "context.resolved",
                run_id,
                thread_id,
                TraceSemanticRefs(
                    agent_id=result.agent_id,
                    capability_id=capability_id,
                    context_id=result.context_id,
                ),
                {
                    "cache": cache_scope,
                    "from_cache": result.from_cache,
                    "origin": result.origin,
                    "render": render,
                    "sensitivity": sensitivity,
                },
                result.origin_id,
            )
        )

    def _emit_failure(
        self,
        context_id: SemanticId,
        agent_id: SemanticId,
        origin_id: SemanticId,
        run_id: str,
        thread_id: str,
        error: Exception,
    ) -> None:
        from contract4agents.tracing import TraceSemanticRefs

        self.trace_sink.emit(
            self._event(
                "datasource.failed" if origin_id.kind == "datasource" else "context.failed",
                run_id,
                thread_id,
                TraceSemanticRefs(
                    agent_id=agent_id,
                    capability_id=origin_id if origin_id.kind == "datasource" else None,
                    context_id=context_id,
                ),
                {"error_type": type(error).__name__},
                origin_id,
            )
        )

    def _event(
        self,
        event_type: str,
        run_id: str,
        thread_id: str,
        semantic: TraceSemanticRefs,
        data: Mapping[str, object],
        origin_id: SemanticId,
    ) -> TraceEvent:
        from contract4agents.tracing import (
            ProviderCorrelation,
            RedactionMetadata,
            TraceEvent,
            TraceRunContext,
        )

        self._event_counter += 1
        binding = self.plan.bindings[origin_id]
        locator = binding.locator.get("python")
        evidence = f"binding:{origin_id}:{locator}" if isinstance(locator, str) else f"binding:{origin_id}"
        return TraceEvent(
            context=TraceRunContext(
                run_id,
                thread_id,
                self.plan.contract_digest,
                self.plan.plan_digest,
            ),
            event_id=f"context-{self._event_counter:08d}",
            parent_event_id=None,
            event_type=event_type,
            timestamp=time.time(),
            semantic=semantic,
            data=data,
            provider=ProviderCorrelation("contract4agents"),
            evidence_refs=(evidence,),
            provenance={
                "binding_kind": binding.kind,
                "execution": binding.execution,
                "mechanism": binding.mechanism,
            },
            redaction=RedactionMetadata(),
        )


def _validate_parameters(
    name: str,
    parameters: tuple[Any, ...],
    values: Mapping[str, object],
    output_types: FrozenMap[str, type[object]],
    semantic_id: SemanticId,
) -> dict[str, object]:
    model = build_parameter_model(name, parameters, output_types)
    if model is None:
        if values:
            raise ContextResolutionError(semantic_id, "received inputs for a parameterless declaration")
        return {}
    try:
        instance = model(**dict(values))
    except Exception as exc:
        raise ContextResolutionError(semantic_id, f"input validation failed ({type(exc).__name__})") from exc
    return dict(instance.model_dump(mode="python"))  # type: ignore[attr-defined]


def _resolve_mapping(
    expression: str,
    inputs: Mapping[str, object],
    context: Mapping[str, ResolvedContextValue],
) -> object:
    parts = expression.split(".")
    if len(parts) < 2 or parts[0] not in {"input", "context"}:
        raise ValueError(f"Unsupported context mapping `{expression}`")
    if parts[0] == "input":
        current: object = inputs[parts[1]]
    else:
        current = context[parts[1]].value
    for segment in parts[2:]:
        if isinstance(current, Mapping):
            current = current[segment]
        else:
            current = getattr(current, segment)
    return current


def _plain(value: object) -> object:
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _render(value: object, mode: str) -> str:
    plain = _plain(value)
    if mode == "text" and isinstance(plain, str):
        return plain
    if mode == "markdown" and isinstance(plain, Mapping):
        return "\n".join(
            f"- **{key}:** {_inline(child)}" for key, child in sorted(plain.items())
        )
    return json.dumps(plain, ensure_ascii=False, indent=2, sort_keys=True)


def _inline(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = [
    "ContextResolutionError",
    "ContextRuntime",
    "ResolvedContextValue",
]
