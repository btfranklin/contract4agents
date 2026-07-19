"""OpenAI Agents SDK span correlation into normalized trace schema."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Literal

from contract4agents.adapters._openai_names import contract_tool_name
from contract4agents.ir import CanonicalIR, SemanticId, semantic_id
from contract4agents.planning import GrantMappingPlan, MaterializationPlan
from contract4agents.tracing._models import (
    NormalizedTrace,
    ProviderCorrelation,
    RedactionMetadata,
    TraceAttempt,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
)
from contract4agents.tracing._sinks import NormalizedTraceSink

_OPENAI_SUPPORTED_PROVIDER_HOSTED_CALLS = {
    "web_search_call": "web_search",
}

_OPENAI_UNSUPPORTED_PROVIDER_HOSTED_CALLS = {
    "file_search_call": "file_search",
    "code_interpreter_call": "code_interpreter",
    "image_generation_call": "image_generation",
    "mcp_call": "mcp",
    "mcp_list_tools": "mcp_list_tools",
    "tool_search_call": "tool_search",
}

# These calls are dispatched outside the provider-hosted tool path. Their
# authoritative evidence comes from SDK spans or the host application, not
# model-response normalization.
_OPENAI_HOST_DISPATCHED_CALLS = frozenset(
    {
        "apply_patch_call",
        "computer_call",
        "custom_tool_call",
        "function_call",
        "local_shell_call",
        "shell_call",
    }
)

_MISSING = object()


def resolve_provider_tool_grant(
    plan: MaterializationPlan,
    *,
    agent_id: SemanticId,
    provider: str,
    tool: str,
) -> GrantMappingPlan:
    """Resolve exactly one enabled provider-hosted grant from planned locators."""

    agent_id.require_kind("agent")
    matches: list[GrantMappingPlan] = []
    for grant in plan.grants.values():
        if grant.agent_id != agent_id or grant.availability != "enabled":
            continue
        binding = plan.bindings.get(grant.capability_id)
        if (
            binding is None
            or binding.kind != "tool"
            or binding.execution != "provider_hosted"
        ):
            continue
        locator_tool = _locator_tool(binding.locator)
        if binding.locator.get("provider") == provider and locator_tool == tool:
            matches.append(grant)
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one enabled provider-hosted grant for "
            f"`{agent_id}` and `{provider}:{tool}`; found {len(matches)}"
        )
    return matches[0]


def normalize_openai_response_events(
    plan: MaterializationPlan,
    responses: Iterable[object],
    *,
    agent: str | SemanticId,
    context: TraceRunContext,
    attempt: TraceAttempt | None = None,
    sink: NormalizedTraceSink | None = None,
) -> tuple[TraceEvent, ...]:
    """Normalize provider-hosted calls from Agents SDK model responses.

    Only provider identity, status, model metadata, and correlation identifiers
    are retained. Provider prompts, actions, queries, and outputs are excluded.
    """

    agent_id = agent if isinstance(agent, SemanticId) else semantic_id("agent", agent)
    agent_id.require_kind("agent")
    events: list[TraceEvent] = []
    for response_index, response in enumerate(responses):
        response_id = _field_text(response, "response_id")
        request_id = _field_text(response, "request_id")
        provider_model = _provider_model(response)
        output = _field(response, "output")
        if not isinstance(output, list | tuple):
            continue
        for item_index, item in enumerate(output):
            item_type = _field_text(item, "type")
            if item_type is None or item_type in _OPENAI_HOST_DISPATCHED_CALLS:
                continue
            provider_tool = _OPENAI_SUPPORTED_PROVIDER_HOSTED_CALLS.get(item_type)
            unsupported_tool = _OPENAI_UNSUPPORTED_PROVIDER_HOSTED_CALLS.get(item_type)
            if provider_tool is None:
                provider_tool = unsupported_tool
            unrecognized_call = provider_tool is None and item_type.endswith("_call")
            if provider_tool is None and not unrecognized_call:
                continue
            call_id = _field_text(item, "id")
            identity = ":".join(
                part
                for part in (
                    response_id or request_id or str(response_index),
                    call_id or str(item_index),
                )
            )
            event_id = f"openai:hosted-tool:{identity}"
            data: dict[str, object] = {
                "provider_tool": (
                    f"openai.{provider_tool}"
                    if provider_tool is not None
                    else f"openai.unrecognized:{item_type}"
                )
            }
            if attempt is not None:
                data["attempt"] = attempt.to_dict()
            status = _field_text(item, "status")
            if status is not None:
                data["status"] = status
            if provider_model is not None:
                data["provider_model"] = provider_model
            evidence_refs = tuple(
                reference
                for reference in (
                    f"provider:openai:response:{response_id}" if response_id else None,
                    f"provider:openai:call:{call_id}" if call_id else None,
                )
                if reference is not None
            )
            if unsupported_tool is not None:
                event_type = "capability.undeclared"
                semantic = TraceSemanticRefs(agent_id=agent_id)
                data["reason"] = (
                    f"OpenAI provider-hosted response call type `{item_type}` is not supported by this adapter"
                )
            elif unrecognized_call:
                event_type = "capability.undeclared"
                semantic = TraceSemanticRefs(agent_id=agent_id)
                data["reason"] = (
                    f"Unrecognized OpenAI response call type `{item_type}`; "
                    "provider-hosted execution cannot be ruled out"
                )
            else:
                assert provider_tool is not None
                try:
                    grant = resolve_provider_tool_grant(
                        plan,
                        agent_id=agent_id,
                        provider="openai",
                        tool=provider_tool,
                    )
                except ValueError as exc:
                    event_type = "capability.undeclared"
                    semantic = TraceSemanticRefs(agent_id=agent_id)
                    data["reason"] = str(exc)
                else:
                    if status in {"failed", "cancelled", "canceled", "incomplete"}:
                        event_type = "tool.failed"
                    elif status is not None and status not in {"completed", "succeeded"}:
                        event_type = "tool.started"
                    else:
                        event_type = "tool.completed"
                    semantic = TraceSemanticRefs(
                        agent_id=agent_id,
                        capability_id=grant.capability_id,
                        grant_id=grant.id,
                        isolation_id=grant.isolation_id,
                    )
            event = TraceEvent(
                context=context,
                event_id=event_id,
                parent_event_id=None,
                event_type=event_type,
                timestamp=_timestamp(_field(item, "completed_at")),
                semantic=semantic,
                data=data,
                provider=ProviderCorrelation(
                    "openai",
                    run_id=response_id,
                    request_id=request_id,
                ),
                evidence_refs=evidence_refs,
                provenance={"source": "openai-agents-sdk-model-response"},
                redaction=RedactionMetadata(),
            )
            events.append(event)
            if sink is not None:
                sink.emit(event)
    return tuple(events)


def normalize_openai_exception_responses(
    plan: MaterializationPlan,
    exception: BaseException,
    *,
    agent: str | SemanticId,
    context: TraceRunContext,
    attempt: TraceAttempt | None = None,
    sink: NormalizedTraceSink | None = None,
) -> tuple[TraceEvent, ...]:
    """Normalize model responses retained on an Agents SDK run exception.

    The helper deliberately records only provider response evidence. The host
    still owns exception handling, retry decisions, and lifecycle failure
    events.
    """

    run_data = getattr(exception, "run_data", None)
    if run_data is None:
        return ()
    raw_responses = getattr(run_data, "raw_responses", _MISSING)
    if raw_responses is _MISSING:
        return ()
    if not isinstance(raw_responses, Iterable) or isinstance(raw_responses, str | bytes | Mapping):
        raise TypeError("Agents SDK exception run_data.raw_responses must be an iterable of responses")
    return normalize_openai_response_events(
        plan,
        raw_responses,
        agent=agent,
        context=context,
        attempt=attempt,
        sink=sink,
    )


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
        attempt: TraceAttempt | None = None,
        sink: NormalizedTraceSink | None = None,
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
        self.attempt = attempt
        self.events: list[TraceEvent] = []
        self._span_parent: dict[str, str | None] = {}
        self._span_semantic: dict[str, TraceSemanticRefs] = {}
        self._span_attempt: dict[str, TraceAttempt | None] = {}
        self._attempt_context: ContextVar[TraceAttempt | None] = ContextVar(
            f"contract4agents_openai_attempt_{id(self)}",
            default=None,
        )
        self._capture_context: ContextVar[bool] = ContextVar(
            f"contract4agents_openai_capture_{id(self)}",
            default=False,
        )
        self._owned_trace_ids: set[str] = set()
        self._lock = threading.Lock()

    def on_trace_start(self, trace: object) -> None:
        if not self._capture_context.get():
            return
        trace_id = _text_attr(trace, "trace_id")
        with self._lock:
            self._owned_trace_ids.add(trace_id)

    def on_trace_end(self, trace: object) -> None:
        trace_id = _text_attr(trace, "trace_id")
        with self._lock:
            self._owned_trace_ids.discard(trace_id)

    def on_span_start(self, span: object) -> None:
        with self._lock:
            trace_id = _text_attr(span, "trace_id")
            if trace_id not in self._owned_trace_ids:
                return
            span_id = _text_attr(span, "span_id")
            parent_id = _optional_text_attr(span, "parent_id")
            self._span_parent[span_id] = parent_id
            attempt = self._current_attempt()
            self._span_attempt[span_id] = attempt
            event_type, semantic = self._classify(span, completed=False)
            self._span_semantic[span_id] = semantic
            self._record(
                span,
                event_id=f"openai:{span_id}:started",
                parent_event_id=f"openai:{parent_id}:started" if parent_id else None,
                event_type=event_type,
                semantic=semantic,
                timestamp=_timestamp(getattr(span, "started_at", None)),
                attempt=attempt,
            )

    def on_span_end(self, span: object) -> None:
        with self._lock:
            trace_id = _text_attr(span, "trace_id")
            if trace_id not in self._owned_trace_ids:
                return
            span_id = _text_attr(span, "span_id")
            start_semantic = self._span_semantic.get(span_id)
            event_type, semantic = self._classify(span, completed=True)
            semantic = start_semantic or semantic
            timestamp = _timestamp(getattr(span, "ended_at", None))
            error = getattr(span, "error", None)
            attempt = self._span_attempt.get(span_id)
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
                    attempt=attempt,
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
                attempt=attempt,
            )

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        return None

    def normalized_trace(self) -> NormalizedTrace:
        with self._lock:
            return NormalizedTrace(tuple(self.events))

    def emit(self, event: TraceEvent) -> None:
        """Accept an adjacent normalized event into this run's evidence."""

        if event.context != self.context:
            raise ValueError("Trace event does not match the OpenAI processor run context")
        with self._lock:
            self.events.append(event)
            if self.sink is not None:
                self.sink.emit(event)

    def normalize_response_events(
        self,
        responses: Iterable[object],
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
    ) -> tuple[TraceEvent, ...]:
        """Normalize hosted provider-tool calls observed in model responses."""

        return normalize_openai_response_events(
            self.plan,
            responses,
            agent=agent,
            context=self.context,
            attempt=attempt or self._current_attempt(),
            sink=self,
        )

    def normalize_exception_responses(
        self,
        exception: BaseException,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
    ) -> tuple[TraceEvent, ...]:
        """Normalize raw responses preserved by an Agents SDK exception."""

        return normalize_openai_exception_responses(
            self.plan,
            exception,
            agent=agent,
            context=self.context,
            attempt=attempt or self._current_attempt(),
            sink=self,
        )

    @contextmanager
    def bind_attempt(self, attempt: TraceAttempt) -> Iterator[None]:
        """Bind attempt identity while the host executes one runner invocation."""

        attempt_token = self._attempt_context.set(attempt)
        capture_token = self._capture_context.set(True)
        try:
            yield
        finally:
            self._capture_context.reset(capture_token)
            self._attempt_context.reset(attempt_token)

    @contextmanager
    def capture(self) -> Iterator[None]:
        """Route SDK traces created in this scope to this normalized run."""

        token = self._capture_context.set(True)
        try:
            yield
        finally:
            self._capture_context.reset(token)

    def record_output_schema_failure(
        self,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> TraceEvent:
        """Record a host-observed canonical output validation failure."""

        selected = self._require_attempt(attempt)
        agent_id = self._require_agent(agent)
        return self._record_host_event(
            event_id=(
                f"contract4agents:{agent_id}:attempt:{selected.attempt_id}:output-schema-failed"
            ),
            event_type="output.schema_failed",
            agent=agent_id,
            data={"attempt": selected.to_dict()},
            evidence_refs=evidence_refs,
            provenance_source="host-output-schema-validation",
        )

    def record_terminal_attempt(
        self,
        *,
        agent: str | SemanticId,
        outcome: Literal["succeeded", "failed"],
        attempt: TraceAttempt | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> TraceEvent:
        """Select the terminal attempt whose output governs logical-run assurance."""

        if outcome not in {"succeeded", "failed"}:
            raise ValueError(f"Unsupported terminal attempt outcome `{outcome}`")
        selected = self._require_attempt(attempt)
        agent_id = self._require_agent(agent)
        return self._record_host_event(
            event_id=f"contract4agents:{agent_id}:attempt:{selected.attempt_id}:selected",
            event_type="attempt.selected",
            agent=agent_id,
            data={"attempt": selected.to_dict(), "outcome": outcome},
            evidence_refs=evidence_refs,
            provenance_source="host-attempt-selection",
        )

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
        attempt: TraceAttempt | None = None,
    ) -> None:
        trace_id = _text_attr(span, "trace_id")
        span_id = _text_attr(span, "span_id")
        span_data = getattr(span, "span_data", None)
        provider_span_type = str(getattr(span_data, "type", "custom"))
        data: dict[str, object] = {"error": error, "provider_span_type": provider_span_type}
        if attempt is not None:
            data["attempt"] = attempt.to_dict()
        provider_model = _field_text(span_data, "model")
        if provider_model is None:
            provider_model = _field_text(_field(span_data, "response"), "model")
        if provider_model is not None:
            data["provider_model"] = provider_model
        event = TraceEvent(
            context=self.context,
            event_id=event_id,
            parent_event_id=parent_event_id,
            event_type=event_type,
            timestamp=timestamp,
            semantic=semantic,
            data=data,
            provider=ProviderCorrelation("openai", trace_id=trace_id, span_id=span_id),
            evidence_refs=(f"provider:openai:{trace_id}:{span_id}",),
            provenance={"source": "openai-agents-sdk-tracing-processor"},
            redaction=RedactionMetadata(),
        )
        self.events.append(event)
        if self.sink is not None:
            self.sink.emit(event)

    def _require_attempt(self, attempt: TraceAttempt | None) -> TraceAttempt:
        selected = attempt or self._current_attempt()
        if selected is None:
            raise ValueError("attempt is required for attempt-aware evidence")
        return selected

    def _current_attempt(self) -> TraceAttempt | None:
        return self._attempt_context.get() or self.attempt

    def _require_agent(self, agent: str | SemanticId) -> SemanticId:
        agent_id = agent if isinstance(agent, SemanticId) else semantic_id("agent", agent)
        agent_id.require_kind("agent")
        if agent_id not in self.ir.agents:
            raise ValueError(f"Unknown contract agent `{agent_id}`")
        return agent_id

    def _record_host_event(
        self,
        *,
        event_id: str,
        event_type: str,
        agent: str | SemanticId,
        data: Mapping[str, object],
        evidence_refs: tuple[str, ...],
        provenance_source: str,
    ) -> TraceEvent:
        agent_id = self._require_agent(agent)
        event = TraceEvent(
            context=self.context,
            event_id=event_id,
            parent_event_id=None,
            event_type=event_type,
            timestamp=time.time(),
            semantic=TraceSemanticRefs(agent_id=agent_id),
            data=data,
            provider=ProviderCorrelation("contract4agents"),
            evidence_refs=evidence_refs,
            provenance={"source": provenance_source},
            redaction=RedactionMetadata(),
        )
        self.emit(event)
        return event


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


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _field_text(value: object, name: str) -> str | None:
    item = _field(value, name)
    return item if isinstance(item, str) and item else None


def _provider_model(response: object) -> str | None:
    for name in ("model", "model_name"):
        value = _field_text(response, name)
        if value is not None:
            return value
    return None


def _locator_tool(locator: Mapping[str, object]) -> object:
    tool = locator.get("tool")
    provider_tool = locator.get("provider_tool")
    if tool is not None and provider_tool is not None and tool != provider_tool:
        return None
    return tool if tool is not None else provider_tool


__all__ = [
    "OpenAINormalizedTraceProcessor",
    "normalize_openai_exception_responses",
    "normalize_openai_response_events",
    "resolve_provider_tool_grant",
]
