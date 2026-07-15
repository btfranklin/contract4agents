"""OpenAI Agents SDK span correlation into normalized trace schema V2."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Protocol

from contract4agents.adapters._openai_names import contract_tool_name
from contract4agents.ir import CanonicalIR, SemanticId, semantic_id
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing._models import (
    NormalizedTrace,
    ProviderCorrelation,
    RedactionMetadata,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
)


class _EventSink(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


class OpenAINormalizedTraceProcessor:
    """A per-run Agents SDK tracing processor that preserves raw span correlation.

    Register an instance with ``agents.add_trace_processor`` before executing a
    materialized graph. The processor deliberately excludes provider inputs and
    outputs from normalized payloads; the provider trace remains the evidence
    source through its trace/span IDs.
    """

    def __init__(
        self,
        ir: CanonicalIR,
        plan: MaterializationPlan,
        *,
        run_id: str,
        thread_id: str | None = None,
        sink: _EventSink | None = None,
    ) -> None:
        if plan.contract_digest == "" or not run_id.strip():
            raise ValueError("plan and run_id are required")
        self.ir = ir
        self.plan = plan
        self.context = TraceRunContext(
            run_id,
            thread_id or run_id,
            plan.contract_digest,
            plan.plan_digest,
        )
        self.sink = sink
        self.events: list[TraceEvent] = []
        self._span_parent: dict[str, str | None] = {}
        self._span_semantic: dict[str, TraceSemanticRefs] = {}
        self._lock = threading.Lock()

    def on_trace_start(self, trace: object) -> None:
        del trace

    def on_trace_end(self, trace: object) -> None:
        del trace

    def on_span_start(self, span: object) -> None:
        with self._lock:
            span_id = _text_attr(span, "span_id")
            parent_id = _optional_text_attr(span, "parent_id")
            self._span_parent[span_id] = parent_id
            event_type, semantic = self._classify(span, completed=False)
            self._span_semantic[span_id] = semantic
            self._record(
                span,
                event_id=f"openai:{span_id}:started",
                parent_event_id=f"openai:{parent_id}:started" if parent_id else None,
                event_type=event_type,
                semantic=semantic,
                timestamp=_timestamp(getattr(span, "started_at", None)),
            )

    def on_span_end(self, span: object) -> None:
        with self._lock:
            span_id = _text_attr(span, "span_id")
            start_semantic = self._span_semantic.get(span_id)
            event_type, semantic = self._classify(span, completed=True)
            semantic = start_semantic or semantic
            timestamp = _timestamp(getattr(span, "ended_at", None))
            error = getattr(span, "error", None)
            if error is not None:
                event_type = f"{event_type.rsplit('.', 1)[0]}.failed"
            if event_type == "agent.completed" and error is None:
                accepted_id = f"openai:{span_id}:output-accepted"
                self._record(
                    span,
                    event_id=accepted_id,
                    parent_event_id=f"openai:{span_id}:started",
                    event_type="output.accepted",
                    semantic=semantic,
                    timestamp=timestamp,
                )
                parent = accepted_id
            else:
                parent = f"openai:{span_id}:started"
            self._record(
                span,
                event_id=f"openai:{span_id}:completed",
                parent_event_id=parent,
                event_type=event_type,
                semantic=semantic,
                timestamp=timestamp,
                error=error is not None,
            )

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        return None

    def normalized_trace(self) -> NormalizedTrace:
        with self._lock:
            return NormalizedTrace(tuple(self.events))

    def _classify(self, span: object, *, completed: bool) -> tuple[str, TraceSemanticRefs]:
        data = getattr(span, "span_data", None)
        span_type = str(getattr(data, "type", "custom"))
        suffix = "completed" if completed else "started"
        if span_type == "agent":
            name = str(getattr(data, "name", ""))
            agent_id = semantic_id("agent", name)
            return f"agent.{suffix}", TraceSemanticRefs(
                agent_id=agent_id if agent_id in self.ir.agents else None
            )
        if span_type == "function":
            raw_name = str(getattr(data, "name", ""))
            try:
                name = contract_tool_name(raw_name)
            except ValueError:
                name = raw_name
            parent_agent = self._parent_agent(span)
            edge = next(
                (
                    item
                    for item in self.ir.composition.values()
                    if item.name == name and (parent_agent is None or item.source_agent_id == parent_agent)
                ),
                None,
            )
            if edge is not None:
                return f"composition.{suffix}", TraceSemanticRefs(
                    agent_id=edge.source_agent_id,
                    composition_id=edge.id,
                    isolation_id=edge.isolation_id,
                )
            capability_id: SemanticId | None = semantic_id("tool", name)
            if capability_id not in self.ir.capabilities:
                capability_id = None
            grant_id = None
            if parent_agent is not None and capability_id is not None:
                candidate = semantic_id("grant", parent_agent.parts[0], capability_id.parts[0])
                grant_id = candidate if candidate in self.ir.grants else None
            return f"tool.{suffix}", TraceSemanticRefs(
                agent_id=parent_agent,
                capability_id=capability_id,
                grant_id=grant_id,
            )
        if span_type == "handoff":
            source = str(getattr(data, "from_agent", ""))
            target = str(getattr(data, "to_agent", ""))
            edge = next(
                (
                    item
                    for item in self.ir.composition.values()
                    if item.mode == "handoff"
                    and item.source_agent_id == semantic_id("agent", source)
                    and item.target_agent_id == semantic_id("agent", target)
                ),
                None,
            )
            return f"handoff.{suffix}", TraceSemanticRefs(
                agent_id=semantic_id("agent", source) if source else None,
                composition_id=edge.id if edge is not None else None,
                isolation_id=edge.isolation_id if edge is not None else None,
            )
        return f"provider.{span_type}.{suffix}", TraceSemanticRefs(agent_id=self._parent_agent(span))

    def _parent_agent(self, span: object) -> SemanticId | None:
        parent_id = _optional_text_attr(span, "parent_id")
        visited: set[str] = set()
        while parent_id is not None and parent_id not in visited:
            visited.add(parent_id)
            semantic = self._span_semantic.get(parent_id)
            if semantic is not None and semantic.agent_id is not None:
                return semantic.agent_id
            parent_id = self._span_parent.get(parent_id)
        return None

    def _record(
        self,
        span: object,
        *,
        event_id: str,
        parent_event_id: str | None,
        event_type: str,
        semantic: TraceSemanticRefs,
        timestamp: float,
        error: bool = False,
    ) -> None:
        trace_id = _text_attr(span, "trace_id")
        span_id = _text_attr(span, "span_id")
        span_data = getattr(span, "span_data", None)
        event = TraceEvent(
            context=self.context,
            event_id=event_id,
            parent_event_id=parent_event_id,
            event_type=event_type,
            timestamp=timestamp,
            semantic=semantic,
            data={"error": error, "provider_span_type": str(getattr(span_data, "type", "custom"))},
            provider=ProviderCorrelation("openai", trace_id=trace_id, span_id=span_id),
            evidence_refs=(f"provider:openai:{trace_id}:{span_id}",),
            provenance={"source": "openai-agents-sdk-tracing-processor"},
            redaction=RedactionMetadata(),
        )
        self.events.append(event)
        if self.sink is not None:
            self.sink.emit(event)


def _text_attr(value: object, name: str) -> str:
    item = getattr(value, name, None)
    if not isinstance(item, str) or not item:
        raise ValueError(f"OpenAI span `{name}` must be a non-empty string")
    return item


def _optional_text_attr(value: object, name: str) -> str | None:
    item = getattr(value, name, None)
    return item if isinstance(item, str) and item else None


def _timestamp(value: object) -> float:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return time.time()


__all__ = ["OpenAINormalizedTraceProcessor"]
