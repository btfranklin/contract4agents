"""OpenAI Agents SDK span correlation into normalized trace schema."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Literal, Self

from contract4agents.ir import CanonicalIR, SemanticId, semantic_id
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing._capture import (
    AttemptCaptureState,
    build_trace_closure,
    prior_attempt,
)
from contract4agents.tracing._closure import (
    TRACE_INSTRUMENTATION_CHANNELS,
    TraceAttemptClosure,
    TraceCaptureSnapshot,
    TraceClosureEvidence,
    TraceInstrumentationChannel,
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
from contract4agents.tracing._openai_responses import (
    normalize_openai_exception_responses,
    normalize_openai_response_events,
    resolve_provider_tool_grant,
)
from contract4agents.tracing._openai_spans import OpenAISpanMapper
from contract4agents.tracing._openai_utils import (
    field_text,
    field_value,
    optional_text_attr,
    text_attr,
    timestamp,
)
from contract4agents.tracing._sinks import NormalizedTraceSink

_OPENAI_CAPTURED_CHANNELS: frozenset[TraceInstrumentationChannel] = frozenset(
    {"agent", "composition", "handoff", "output", "provider_response", "tool"}
)
_MISSING = object()


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
        prior_trace: NormalizedTrace | None = None,
        prior_closure: TraceClosureEvidence | None = None,
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
            prior_trace=prior_trace,
            prior_closure=prior_closure,
        )

    def on_trace_start(self, trace: object) -> None:
        session = self._current_session.get()
        if session is None:
            return
        trace_id = text_attr(trace, "trace_id")
        with self._lock:
            if self._shutdown:
                return
            existing = self._trace_sessions.get(trace_id)
            if existing is not None and existing is not session:
                raise ValueError(f"OpenAI trace `{trace_id}` is already owned by another session")
            self._trace_sessions[trace_id] = session
        try:
            accepted = session._on_trace_start(trace_id)
        except BaseException:
            with self._lock:
                if self._trace_sessions.get(trace_id) is session:
                    self._trace_sessions.pop(trace_id, None)
            raise
        if not accepted:
            with self._lock:
                if self._trace_sessions.get(trace_id) is session:
                    self._trace_sessions.pop(trace_id, None)

    def on_trace_end(self, trace: object) -> None:
        trace_id = text_attr(trace, "trace_id")
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
        trace_id = text_attr(span, "trace_id")
        with self._lock:
            return self._trace_sessions.get(trace_id)

    def _release(self, session: OpenAINormalizedTraceSession) -> None:
        with self._lock:
            owned = tuple(
                trace_id
                for trace_id, candidate in self._trace_sessions.items()
                if candidate is session
            )
            for trace_id in owned:
                self._trace_sessions.pop(trace_id, None)


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
        prior_trace: NormalizedTrace | None = None,
        prior_closure: TraceClosureEvidence | None = None,
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
        self._prior_events: tuple[TraceEvent, ...]
        self._prior_closure: TraceClosureEvidence | None
        if (prior_trace is None) != (prior_closure is None):
            raise ValueError("prior_trace and prior_closure must be supplied together")
        if prior_trace is not None and prior_closure is not None:
            if prior_trace.run_ids != (run_id,):
                raise ValueError("Prior trace must contain exactly the resumed run")
            TraceCaptureSnapshot(prior_trace, prior_closure)
            if prior_closure.context != self.context:
                raise ValueError("Prior trace closure does not match the resumed session context")
            self._prior_events = prior_trace.events
            self._prior_closure = prior_closure
        else:
            self._prior_events = ()
            self._prior_closure = None
        self.events: list[TraceEvent] = []
        self._span_mapper = OpenAISpanMapper(ir)
        self._span_attempt: dict[str, TraceAttempt | None] = {}
        self._attempt_context: ContextVar[TraceAttempt | None] = ContextVar(
            f"contract4agents_openai_attempt_{id(self)}",
            default=None,
        )
        self._attempts: dict[str, AttemptCaptureState] = {}
        self._active_trace_attempts: dict[str, TraceAttempt] = {}
        self._unbound_trace_ids: set[str] = set()
        self._activation_token: Token[OpenAINormalizedTraceSession | None] | None = None
        self._closed = False
        self._closed_snapshot: TraceCaptureSnapshot | None = None
        self._channels: set[TraceInstrumentationChannel] = set(_OPENAI_CAPTURED_CHANNELS)
        self._attested_channels: set[TraceInstrumentationChannel] = set()
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

    def _on_trace_start(self, trace_id: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            attempt = self._current_attempt()
            if attempt is None:
                event = TraceEvent(
                    context=self.context,
                    event_id=f"openai:trace:{trace_id}:unbound",
                    parent_event_id=None,
                    event_type="instrumentation.unbound",
                    timestamp=time.time(),
                    semantic=TraceSemanticRefs(),
                    data={"reason": "SDK trace started without attempt identity."},
                    provider=ProviderCorrelation("openai", trace_id=trace_id),
                    evidence_refs=(f"provider:openai:{trace_id}",),
                    provenance={"source": "openai-agents-sdk-tracing-router"},
                    redaction=RedactionMetadata(),
                )
                self._accept_event(event)
                self._unbound_trace_ids.add(trace_id)
                return True
            state = self._attempts[attempt.attempt_id]
            state.provider_trace_ids.add(trace_id)
            self._active_trace_attempts[trace_id] = attempt
            return True

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
            trace_id = text_attr(span, "trace_id")
            attempt = self._active_trace_attempts.get(trace_id)
            if attempt is None:
                return
            span_id = text_attr(span, "span_id")
            parent_id = optional_text_attr(span, "parent_id")
            event_type, semantic = self._span_mapper.classify(span, completed=False)
            try:
                self._record(
                    span,
                    event_id=f"openai:{span_id}:started",
                    parent_event_id=f"openai:{parent_id}:started" if parent_id else None,
                    event_type=event_type,
                    semantic=semantic,
                    timestamp=timestamp(getattr(span, "started_at", None)),
                    attempt=attempt,
                )
            except BaseException:
                self._active_trace_attempts.pop(trace_id, None)
                raise
            self._span_attempt[span_id] = attempt
            self._span_mapper.register(span_id, parent_id, semantic)

    def _on_span_end(self, span: object) -> None:
        with self._lock:
            if self._closed:
                return
            trace_id = text_attr(span, "trace_id")
            if trace_id not in self._active_trace_attempts:
                return
            span_id = text_attr(span, "span_id")
            attempt = self._span_attempt.get(span_id)
            if attempt is None:
                return
            start_semantic = self._span_mapper.semantic_for(span_id)
            event_type, semantic = self._span_mapper.classify(span, completed=True)
            semantic = start_semantic or semantic
            event_timestamp = timestamp(getattr(span, "ended_at", None))
            error = getattr(span, "error", None)
            if error is not None:
                event_type = f"{event_type.rsplit('.', 1)[0]}.failed"
            try:
                if event_type == "agent.completed" and error is None:
                    accepted_id = f"openai:{span_id}:output-accepted"
                    self._record(
                        span,
                        event_id=accepted_id,
                        parent_event_id=f"openai:{span_id}:started",
                        event_type="output.accepted",
                        semantic=semantic,
                        timestamp=event_timestamp,
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
                    timestamp=event_timestamp,
                    error=error is not None,
                    attempt=attempt,
                )
            except BaseException:
                self._active_trace_attempts.pop(trace_id, None)
                raise

    def normalized_trace(self) -> NormalizedTrace:
        with self._lock:
            return NormalizedTrace((*self._prior_events, *self.events))

    def emit(self, event: TraceEvent) -> None:
        """Accept an adjacent normalized event into this run's evidence."""

        if event.context != self.context:
            raise ValueError("Trace event does not match the OpenAI session run context")
        with self._lock:
            if self._closed:
                raise RuntimeError("A closed OpenAI trace session cannot accept evidence")
            self._accept_event(event)

    def _accept_event(self, event: TraceEvent) -> None:
        if self.sink is not None:
            self.sink.emit(event)
        self.events.append(event)

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
        channels: Iterable[TraceInstrumentationChannel],
        *,
        evidence_refs: Iterable[str],
    ) -> None:
        """Add host-instrumented coverage channels with immutable references."""

        selected_channels = tuple(channels)
        selected_refs = tuple(evidence_refs)
        self._ensure_open()
        if not selected_channels or not selected_refs:
            raise ValueError("Channel attestation requires channels and evidence references")
        unknown = sorted(set(selected_channels) - set(TRACE_INSTRUMENTATION_CHANNELS))
        if unknown:
            raise ValueError(f"Unsupported instrumentation channels: {', '.join(unknown)}")
        if any(not isinstance(reference, str) or not reference.strip() for reference in selected_refs):
            raise ValueError("Channel evidence references must be non-empty strings")
        self._channels.update(selected_channels)
        self._attested_channels.update(selected_channels)
        self._closure_evidence_refs.update(selected_refs)

    def snapshot(self) -> TraceCaptureSnapshot:
        """Snapshot one immutable trace and closure frontier without closing."""

        with self._lock:
            if self._closed:
                if self._closed_snapshot is None:
                    raise RuntimeError("Closed OpenAI trace session has no capture snapshot")
                return self._closed_snapshot
            else:
                closure = self._build_closure()
            trace = NormalizedTrace((*self._prior_events, *self.events))
            return TraceCaptureSnapshot(trace, closure)

    def close(self) -> TraceCaptureSnapshot:
        """Detach the session and return its final trace-plus-closure snapshot."""

        with self._lock:
            if self._closed_snapshot is None:
                if self._activation_token is not None:
                    raise RuntimeError("Exit the OpenAI trace session before closing it")
                if not self._prior_events and not self.events:
                    event = TraceEvent(
                        context=self.context,
                        event_id=f"contract4agents:{self.context.run_id}:capture-empty",
                        parent_event_id=None,
                        event_type="instrumentation.empty",
                        timestamp=time.time(),
                        semantic=TraceSemanticRefs(),
                        data={"reason": "No SDK execution was captured for this session."},
                        provider=ProviderCorrelation("contract4agents"),
                        evidence_refs=(
                            f"contract4agents:openai:session:{self.context.run_id}",
                        ),
                        provenance={"source": "contract4agents-openai-capture"},
                        redaction=RedactionMetadata(),
                    )
                    self._accept_event(event)
                closure = self._build_closure()
                trace = NormalizedTrace((*self._prior_events, *self.events))
                self._closed_snapshot = TraceCaptureSnapshot(trace, closure)
                self._closed = True
            snapshot = self._closed_snapshot
        self.router._release(self)
        return snapshot

    def _build_closure(self) -> TraceClosureEvidence:
        return build_trace_closure(
            context=self.context,
            prior_events=self._prior_events,
            prior_closure=self._prior_closure,
            events=tuple(self.events),
            attempts=tuple(
                sorted(self._attempts.values(), key=lambda item: item.attempt)
            ),
            unbound_trace_ids=frozenset(self._unbound_trace_ids),
            channels=frozenset(self._channels),
            attested_channels=frozenset(self._attested_channels),
            evidence_refs=frozenset(self._closure_evidence_refs),
            provider="openai",
        )

    @property
    def closed_snapshot(self) -> TraceCaptureSnapshot:
        if self._closed_snapshot is None:
            raise RuntimeError("The OpenAI trace session has not been closed")
        return self._closed_snapshot

    def record_output_schema_failure(
        self,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> TraceEvent:
        """Record a host-observed canonical output validation failure."""

        selected, agent_id = self._require_attempt_agent(attempt, agent)
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
        selected, agent_id = self._require_attempt_agent(attempt, agent)
        return self._record_host_event(
            event_id=f"contract4agents:{agent_id}:attempt:{selected.attempt_id}:selected",
            event_type="attempt.selected",
            agent=agent_id,
            data={"attempt": selected.to_dict(), "outcome": outcome},
            evidence_refs=evidence_refs,
            provenance_source="host-attempt-selection",
        )

    def _attempt_state(self, attempt: TraceAttempt, agent_id: SemanticId) -> AttemptCaptureState:
        prior = self._prior_attempt(attempt.attempt_id)
        if prior is not None:
            if prior.attempt != attempt or prior.agent_id != agent_id:
                raise ValueError(f"Attempt `{attempt.attempt_id}` conflicts with prior closure identity")
            raise ValueError(
                f"Attempt `{attempt.attempt_id}` is sealed by prior closure evidence; "
                "a resumed SDK execution requires a new attempt identity"
            )
        state = self._attempts.get(attempt.attempt_id)
        if state is None:
            state = AttemptCaptureState(attempt, agent_id)
            self._attempts[attempt.attempt_id] = state
            return state
        if state.attempt != attempt or state.agent_id != agent_id:
            raise ValueError(f"Attempt `{attempt.attempt_id}` has inconsistent session identity")
        return state

    def _prior_attempt(self, attempt_id: str) -> TraceAttemptClosure | None:
        return prior_attempt(self._prior_closure, attempt_id)

    def _close_response_path(
        self,
        state: AttemptCaptureState,
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
        trace_id = text_attr(span, "trace_id")
        span_id = text_attr(span, "span_id")
        span_data = getattr(span, "span_data", None)
        provider_span_type = str(getattr(span_data, "type", "custom"))
        data: dict[str, object] = {"error": error, "provider_span_type": provider_span_type}
        if attempt is not None:
            data["attempt"] = attempt.to_dict()
        provider_model = field_text(span_data, "model")
        if provider_model is None:
            provider_model = field_text(field_value(span_data, "response"), "model")
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
        self._accept_event(event)

    def _require_attempt(self, attempt: TraceAttempt | None) -> TraceAttempt:
        selected = attempt or self._current_attempt()
        if selected is None:
            raise ValueError("attempt is required for attempt-aware evidence")
        return selected

    def _require_attempt_agent(
        self,
        attempt: TraceAttempt | None,
        agent: str | SemanticId,
    ) -> tuple[TraceAttempt, SemanticId]:
        selected = self._require_attempt(attempt)
        agent_id = self._require_agent(agent)
        current = self._attempts.get(selected.attempt_id)
        prior = self._prior_attempt(selected.attempt_id)
        if current is not None and current.attempt != selected:
            raise ValueError(f"Attempt `{selected.attempt_id}` conflicts with current session identity")
        if prior is not None and prior.attempt != selected:
            raise ValueError(f"Attempt `{selected.attempt_id}` conflicts with prior closure identity")
        if self._prior_closure is not None and current is None and prior is None:
            raise ValueError(
                f"Attempt `{selected.attempt_id}` is not present in prior or current execution evidence"
            )
        expected = (
            current.agent_id
            if current is not None
            else prior.agent_id
            if prior is not None
            else None
        )
        if expected is not None and expected != agent_id:
            raise ValueError(
                f"Attempt `{selected.attempt_id}` belongs to `{expected}`, not `{agent_id}`"
            )
        return selected, agent_id

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




__all__ = [
    "OpenAINormalizedTraceRouter",
    "OpenAINormalizedTraceSession",
    "normalize_openai_exception_responses",
    "normalize_openai_response_events",
    "resolve_provider_tool_grant",
]
