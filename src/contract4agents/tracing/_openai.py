"""OpenAI Agents SDK span correlation into normalized trace schema."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Literal, Self

from contract4agents.adapters._openai_names import contract_tool_name
from contract4agents.ir import CanonicalIR, SemanticId, semantic_id
from contract4agents.planning import GrantMappingPlan, MaterializationPlan
from contract4agents.tracing._closure import (
    TraceAttemptClosure,
    TraceClosureEvidence,
    TraceClosureStatus,
    TraceCoverageChannel,
)
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
    batch_id: str | None = None,
    sink: NormalizedTraceSink | None = None,
) -> tuple[TraceEvent, ...]:
    """Normalize provider-hosted calls from Agents SDK model responses.

    Only provider identity, status, model metadata, and correlation identifiers
    are retained. Provider prompts, actions, queries, and outputs are excluded.
    """

    agent_id = agent if isinstance(agent, SemanticId) else semantic_id("agent", agent)
    agent_id.require_kind("agent")
    response_items = tuple(responses)
    events: list[TraceEvent] = []
    response_identities = [
        _field_text(response, "response_id")
        or _field_text(response, "request_id")
        or f"index-{index}"
        for index, response in enumerate(response_items)
    ]
    selected_batch_id = batch_id or (
        attempt.attempt_id if attempt is not None else _batch_identity(response_identities)
    )
    for response_index, response in enumerate(response_items):
        response_id = _field_text(response, "response_id")
        request_id = _field_text(response, "request_id")
        provider_model = _provider_model(response)
        output = _field(response, "output")
        response_identity = response_identities[response_index]
        receipt_data: dict[str, object] = {
            "output_item_count": len(output) if isinstance(output, list | tuple) else 0,
            "response_identity": response_identity,
        }
        if attempt is not None:
            receipt_data["attempt"] = attempt.to_dict()
        if provider_model is not None:
            receipt_data["provider_model"] = provider_model
        receipt = TraceEvent(
            context=context,
            event_id=(
                f"openai:response-batch:{selected_batch_id}:response:{response_index}:"
                f"{response_identity}:normalized"
            ),
            parent_event_id=None,
            event_type="provider.response.normalized",
            timestamp=_timestamp(_field(response, "completed_at")),
            semantic=TraceSemanticRefs(agent_id=agent_id),
            data=receipt_data,
            provider=ProviderCorrelation("openai", run_id=response_id, request_id=request_id),
            evidence_refs=tuple(
                reference
                for reference in (
                    f"provider:openai:response:{response_id}" if response_id else None,
                    f"provider:openai:request:{request_id}" if request_id else None,
                )
                if reference is not None
            ),
            provenance={"source": "openai-agents-sdk-response-normalizer"},
            redaction=RedactionMetadata(),
        )
        events.append(receipt)
        if sink is not None:
            sink.emit(receipt)
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
    batch_data: dict[str, object] = {
        "batch_id": selected_batch_id,
        "response_count": len(response_items),
        "response_ids": response_identities,
    }
    if attempt is not None:
        batch_data["attempt"] = attempt.to_dict()
    batch = TraceEvent(
        context=context,
        event_id=f"openai:response-batch:{selected_batch_id}:normalized",
        parent_event_id=None,
        event_type="provider.response_batch.normalized",
        timestamp=time.time(),
        semantic=TraceSemanticRefs(agent_id=agent_id),
        data=batch_data,
        provider=ProviderCorrelation("openai"),
        evidence_refs=(f"contract4agents:openai:response-batch:{selected_batch_id}",),
        provenance={"source": "openai-agents-sdk-response-normalizer"},
        redaction=RedactionMetadata(),
    )
    events.append(batch)
    if sink is not None:
        sink.emit(batch)
    return tuple(events)


def normalize_openai_exception_responses(
    plan: MaterializationPlan,
    exception: BaseException,
    *,
    agent: str | SemanticId,
    context: TraceRunContext,
    attempt: TraceAttempt | None = None,
    batch_id: str | None = None,
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
        batch_id=batch_id,
        sink=sink,
    )


@dataclass
class _AttemptState:
    attempt: TraceAttempt
    agent_id: SemanticId
    provider_trace_ids: set[str] = field(default_factory=set)
    ended_trace_ids: set[str] = field(default_factory=set)
    response_ids: set[str] = field(default_factory=set)
    response_status: TraceClosureStatus = "incomplete"
    response_evidence_refs: set[str] = field(default_factory=set)
    reason: str = "The response-normalization path has not been closed."


class OpenAINormalizedTraceRouter:
    """One process-lifetime Agents SDK processor routing into disposable sessions."""

    def __init__(self) -> None:
        self._current_session: ContextVar[OpenAINormalizedTraceSession | None] = ContextVar(
            f"contract4agents_openai_session_{id(self)}",
            default=None,
        )
        self._trace_sessions: dict[str, OpenAINormalizedTraceSession] = {}
        self._lock = threading.Lock()
        self._shutdown = False

    def open_session(
        self,
        ir: CanonicalIR,
        plan: MaterializationPlan,
        *,
        run_id: str,
        thread_id: str | None = None,
        sink: NormalizedTraceSink | None = None,
    ) -> OpenAINormalizedTraceSession:
        """Create one logical-run session without adding another SDK processor."""

        with self._lock:
            if self._shutdown:
                raise RuntimeError("The OpenAI trace router is shut down")
        return OpenAINormalizedTraceSession(
            self,
            ir,
            plan,
            run_id=run_id,
            thread_id=thread_id,
            sink=sink,
        )

    def on_trace_start(self, trace: object) -> None:
        session = self._current_session.get()
        if session is None:
            return
        trace_id = _text_attr(trace, "trace_id")
        with self._lock:
            if self._shutdown:
                return
            existing = self._trace_sessions.get(trace_id)
            if existing is not None and existing is not session:
                raise ValueError(f"OpenAI trace `{trace_id}` is already owned by another session")
            self._trace_sessions[trace_id] = session
        session._on_trace_start(trace_id)

    def on_trace_end(self, trace: object) -> None:
        trace_id = _text_attr(trace, "trace_id")
        with self._lock:
            session = self._trace_sessions.pop(trace_id, None)
        if session is not None:
            session._on_trace_end(trace_id)

    def on_span_start(self, span: object) -> None:
        session = self._session_for_span(span)
        if session is not None:
            session._on_span_start(span)

    def on_span_end(self, span: object) -> None:
        session = self._session_for_span(span)
        if session is not None:
            session._on_span_end(span)

    def force_flush(self) -> None:
        return None

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._trace_sessions.clear()

    @property
    def active_trace_count(self) -> int:
        with self._lock:
            return len(self._trace_sessions)

    def _activate(
        self, session: OpenAINormalizedTraceSession
    ) -> Token[OpenAINormalizedTraceSession | None]:
        return self._current_session.set(session)

    def _deactivate(self, token: Token[OpenAINormalizedTraceSession | None]) -> None:
        self._current_session.reset(token)

    def _session_for_span(self, span: object) -> OpenAINormalizedTraceSession | None:
        trace_id = _text_attr(span, "trace_id")
        with self._lock:
            return self._trace_sessions.get(trace_id)


class OpenAINormalizedTraceSession:
    """Disposable normalized-evidence state for one logical OpenAI run."""

    def __init__(
        self,
        router: OpenAINormalizedTraceRouter,
        ir: CanonicalIR,
        plan: MaterializationPlan,
        *,
        run_id: str,
        thread_id: str | None = None,
        sink: NormalizedTraceSink | None = None,
    ) -> None:
        if plan.contract_digest == "" or not run_id.strip():
            raise ValueError("plan and run_id are required")
        self.router = router
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
        self._span_attempt: dict[str, TraceAttempt | None] = {}
        self._attempt_context: ContextVar[TraceAttempt | None] = ContextVar(
            f"contract4agents_openai_attempt_{id(self)}",
            default=None,
        )
        self._attempts: dict[str, _AttemptState] = {}
        self._active_trace_attempts: dict[str, TraceAttempt] = {}
        self._unbound_trace_ids: set[str] = set()
        self._activation_token: Token[OpenAINormalizedTraceSession | None] | None = None
        self._closed = False
        self._closure: TraceClosureEvidence | None = None
        self._channels: set[TraceCoverageChannel] = {
            "agent",
            "composition",
            "handoff",
            "output",
            "provider_response",
            "tool",
        }
        self._closure_evidence_refs: set[str] = set()
        self._lock = threading.Lock()

    def __enter__(self) -> Self:
        with self._lock:
            if self._closed:
                raise RuntimeError("A closed OpenAI trace session cannot be re-entered")
            if self._activation_token is not None:
                raise RuntimeError("An OpenAI trace session cannot be entered more than once")
            self._activation_token = self.router._activate(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        with self._lock:
            token = self._activation_token
            self._activation_token = None
        if token is not None:
            self.router._deactivate(token)
        self.close()

    def _on_trace_start(self, trace_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            attempt = self._current_attempt()
            if attempt is None:
                self._unbound_trace_ids.add(trace_id)
                return
            state = self._attempts[attempt.attempt_id]
            state.provider_trace_ids.add(trace_id)
            self._active_trace_attempts[trace_id] = attempt

    def _on_trace_end(self, trace_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            attempt = self._active_trace_attempts.pop(trace_id, None)
            if attempt is not None:
                self._attempts[attempt.attempt_id].ended_trace_ids.add(trace_id)

    def _on_span_start(self, span: object) -> None:
        with self._lock:
            if self._closed:
                return
            trace_id = _text_attr(span, "trace_id")
            attempt = self._active_trace_attempts.get(trace_id)
            if attempt is None:
                return
            span_id = _text_attr(span, "span_id")
            parent_id = _optional_text_attr(span, "parent_id")
            self._span_parent[span_id] = parent_id
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

    def _on_span_end(self, span: object) -> None:
        with self._lock:
            if self._closed:
                return
            trace_id = _text_attr(span, "trace_id")
            if trace_id not in self._active_trace_attempts:
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

    def normalized_trace(self) -> NormalizedTrace:
        with self._lock:
            return NormalizedTrace(tuple(self.events))

    def emit(self, event: TraceEvent) -> None:
        """Accept an adjacent normalized event into this run's evidence."""

        if event.context != self.context:
            raise ValueError("Trace event does not match the OpenAI session run context")
        with self._lock:
            if self._closed:
                raise RuntimeError("A closed OpenAI trace session cannot accept evidence")
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
        """Normalize and close one successful attempt's provider-response path."""

        self._ensure_open()
        selected = self._require_attempt(attempt)
        agent_id = self._require_agent(agent)
        state = self._attempt_state(selected, agent_id)
        events = normalize_openai_response_events(
            self.plan,
            responses,
            agent=agent_id,
            context=self.context,
            attempt=selected,
            batch_id=selected.attempt_id,
            sink=self,
        )
        self._close_response_path(state, events, "The successful result's raw responses were normalized.")
        return events

    def record_result(
        self,
        result: object,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
    ) -> tuple[TraceEvent, ...]:
        """Normalize every raw response retained on a successful SDK result."""

        raw_responses = getattr(result, "raw_responses", _MISSING)
        if raw_responses is _MISSING:
            raise TypeError("Agents SDK result must expose raw_responses")
        if not isinstance(raw_responses, Iterable) or isinstance(raw_responses, str | bytes | Mapping):
            raise TypeError("Agents SDK result raw_responses must be an iterable of responses")
        return self.normalize_response_events(raw_responses, agent=agent, attempt=attempt)

    def normalize_exception_responses(
        self,
        exception: BaseException,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
    ) -> tuple[TraceEvent, ...]:
        """Normalize and close an exceptional attempt's provider-response path."""

        self._ensure_open()
        selected = self._require_attempt(attempt)
        agent_id = self._require_agent(agent)
        state = self._attempt_state(selected, agent_id)
        events = normalize_openai_exception_responses(
            self.plan,
            exception,
            agent=agent_id,
            context=self.context,
            attempt=selected,
            batch_id=selected.attempt_id,
            sink=self,
        )
        if events:
            self._close_response_path(state, events, "The exception's retained raw responses were normalized.")
        else:
            state.response_status = "unverified"
            state.reason = "The exception did not expose raw response evidence."
        return events

    @contextmanager
    def bind_attempt(self, attempt: TraceAttempt, *, agent: str | SemanticId) -> Iterator[None]:
        """Bind attempt identity while the host executes one runner invocation."""

        if self._closed:
            raise RuntimeError("A closed OpenAI trace session cannot bind an attempt")
        if self._activation_token is None:
            raise RuntimeError("Enter the OpenAI trace session before binding an attempt")
        agent_id = self._require_agent(agent)
        self._attempt_state(attempt, agent_id)
        attempt_token = self._attempt_context.set(attempt)
        try:
            yield
        finally:
            self._attempt_context.reset(attempt_token)

    def attest_channels(
        self,
        channels: Iterable[TraceCoverageChannel],
        *,
        evidence_refs: Iterable[str],
    ) -> None:
        """Add host-instrumented coverage channels with immutable references."""

        selected_channels = tuple(channels)
        selected_refs = tuple(evidence_refs)
        self._ensure_open()
        if not selected_channels or not selected_refs:
            raise ValueError("Channel attestation requires channels and evidence references")
        allowed = {
            "agent",
            "approval",
            "composition",
            "datasource",
            "guardrail",
            "handoff",
            "output",
            "provider_response",
            "tool",
        }
        unknown = sorted(set(selected_channels) - allowed)
        if unknown:
            raise ValueError(f"Unsupported trace-coverage channels: {', '.join(unknown)}")
        if any(not isinstance(reference, str) or not reference.strip() for reference in selected_refs):
            raise ValueError("Channel evidence references must be non-empty strings")
        self._channels.update(selected_channels)
        self._closure_evidence_refs.update(selected_refs)

    def close(self) -> TraceClosureEvidence:
        """Detach the session and produce immutable identity-bound closure evidence."""

        with self._lock:
            if self._closure is not None:
                return self._closure
            if self._activation_token is not None:
                raise RuntimeError("Exit the OpenAI trace session before closing it")
            attempt_closures = tuple(
                self._attempt_closure(state)
                for state in sorted(self._attempts.values(), key=lambda item: item.attempt)
            )
            if not attempt_closures:
                status: TraceClosureStatus = "unverified"
                reason = "No attempt-scoped SDK execution was captured."
            elif self._unbound_trace_ids:
                status = "incomplete"
                reason = "One or more SDK traces were created without attempt identity."
            elif any(
                item.lifecycle_status == "incomplete" or item.response_status == "incomplete"
                for item in attempt_closures
            ):
                status = "incomplete"
                reason = "One or more captured attempts have an open instrumentation path."
            elif any(not item.complete for item in attempt_closures):
                status = "unverified"
                reason = "One or more captured attempts lack verifiable instrumentation evidence."
            else:
                status = "complete"
                reason = "Every captured attempt closed its SDK lifecycle and response-normalization path."
            refs = set(self._closure_evidence_refs)
            for item in attempt_closures:
                refs.update(item.evidence_refs)
            self._closed = True
            self._closure = TraceClosureEvidence(
                context=self.context,
                status=status,
                reason=reason,
                channels=tuple(self._channels),
                attempts=attempt_closures,
                evidence_refs=tuple(refs) or (f"contract4agents:openai:session:{self.context.run_id}",),
            )
            return self._closure

    @property
    def closure_evidence(self) -> TraceClosureEvidence:
        if self._closure is None:
            raise RuntimeError("The OpenAI trace session has not been closed")
        return self._closure

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

    def _attempt_state(self, attempt: TraceAttempt, agent_id: SemanticId) -> _AttemptState:
        state = self._attempts.get(attempt.attempt_id)
        if state is None:
            state = _AttemptState(attempt, agent_id)
            self._attempts[attempt.attempt_id] = state
            return state
        if state.attempt != attempt or state.agent_id != agent_id:
            raise ValueError(f"Attempt `{attempt.attempt_id}` has inconsistent session identity")
        return state

    def _close_response_path(
        self,
        state: _AttemptState,
        events: tuple[TraceEvent, ...],
        reason: str,
    ) -> None:
        receipts = tuple(event for event in events if event.event_type == "provider.response.normalized")
        state.response_ids.update(
            str(event.data["response_identity"])
            for event in receipts
            if "response_identity" in event.data
        )
        state.response_evidence_refs.update(
            reference for event in events for reference in event.evidence_refs
        )
        state.response_status = "complete"
        state.reason = reason

    def _attempt_closure(self, state: _AttemptState) -> TraceAttemptClosure:
        lifecycle_status: TraceClosureStatus = (
            "complete"
            if state.provider_trace_ids and state.provider_trace_ids == state.ended_trace_ids
            else "incomplete"
        )
        evidence_refs = set(state.response_evidence_refs)
        evidence_refs.update(
            f"provider:openai:{trace_id}" for trace_id in state.provider_trace_ids
        )
        return TraceAttemptClosure(
            attempt=state.attempt,
            agent_id=state.agent_id,
            lifecycle_status=lifecycle_status,
            response_status=state.response_status,
            provider_trace_ids=tuple(state.provider_trace_ids),
            response_ids=tuple(state.response_ids),
            evidence_refs=tuple(evidence_refs),
            reason=state.reason,
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

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("A closed OpenAI trace session cannot accept evidence")

    def _current_attempt(self) -> TraceAttempt | None:
        return self._attempt_context.get()

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


def _batch_identity(response_ids: Iterable[str]) -> str:
    payload = "\n".join(response_ids).encode()
    return f"unscoped-{hashlib.sha256(payload).hexdigest()[:16]}"


__all__ = [
    "OpenAINormalizedTraceRouter",
    "OpenAINormalizedTraceSession",
    "normalize_openai_exception_responses",
    "normalize_openai_response_events",
    "resolve_provider_tool_grant",
]
