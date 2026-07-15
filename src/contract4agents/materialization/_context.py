"""Typed runtime resolution for declared datasource and external context slots."""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId, semantic_id
from contract4agents.materialization._types import build_parameter_model, type_adapter_for
from contract4agents.planning import MaterializationPlan

if TYPE_CHECKING:
    from contract4agents.tracing import TraceEvent, TraceSemanticRefs


class ContextResolutionError(RuntimeError):
    """A declared context value could not be safely resolved or validated."""

    def __init__(self, semantic_id: SemanticId, message: str) -> None:
        super().__init__(f"{semantic_id}: {message}")
        self.semantic_id = semantic_id


@runtime_checkable
class RuntimeTraceSink(Protocol):
    def emit(self, event: TraceEvent) -> None:
        """Accept one normalized runtime event."""


class NoOpRuntimeTraceSink:
    def emit(self, event: TraceEvent) -> None:
        del event


class RecordingRuntimeTraceSink:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)


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
        trace_sink: RuntimeTraceSink | None = None,
    ) -> None:
        self.ir = ir
        self.plan = plan
        self.implementations = implementations
        self.output_types = output_types
        self.trace_sink = trace_sink or NoOpRuntimeTraceSink()
        self._run_cache: dict[tuple[str, str, str], ResolvedContextValue] = {}
        self._thread_cache: dict[tuple[str, str, str], ResolvedContextValue] = {}
        self._event_counter = 0

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

    def clear_run(self, run_id: str) -> None:
        self._run_cache = {
            key: value for key, value in self._run_cache.items() if key[0] != run_id
        }

    def clear_thread(self, thread_id: str) -> None:
        self._thread_cache = {
            key: value for key, value in self._thread_cache.items() if key[0] != thread_id
        }

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
        implementation = self.implementations.get(context.origin_id)
        if implementation is None or not callable(implementation):
            raise ContextResolutionError(context_id, "the materialized provider is not callable")

        if context.origin == "datasource":
            capability = self.ir.capabilities[context.origin_id]
            raw_arguments = {
                name: _resolve_mapping(expression, inputs, resolved_context)
                for name, expression in context.input_mappings.items()
            }
            arguments = _validate_parameters(
                f"{capability.name.replace('.', '_')}Input",
                capability.parameters,
                raw_arguments,
                self.output_types,
                context_id,
            )
            cache_scope = capability.cache or "none"
            render = capability.render or "json"
            sensitivity = "internal"
        else:
            arguments = {}
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

        try:
            raw = implementation(**arguments)
            if inspect.isawaitable(raw):
                raw = await raw
            value = type_adapter_for(context.type_ref, self.output_types).validate_python(raw)
        except Exception as exc:
            self._emit_failure(context_id, context.agent_id, context.origin_id, run_id, thread_id, exc)
            raise ContextResolutionError(
                context_id,
                f"provider resolution or output validation failed ({type(exc).__name__})",
            ) from exc
        result = ResolvedContextValue(
            context_id,
            context.agent_id,
            context.origin,
            context.origin_id,
            value,
            _render(value, render),
            False,
        )
        if cache is not None:
            cache[cache_key] = result
        self._emit(result, run_id, thread_id, cache_scope, render, sensitivity)
        return result

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
    "NoOpRuntimeTraceSink",
    "RecordingRuntimeTraceSink",
    "ResolvedContextValue",
    "RuntimeTraceSink",
]
