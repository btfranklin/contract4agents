"""OpenAI Agents SDK span correlation into normalized trace schema."""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Iterable, Mapping
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Self

from contract4agents.ir import CanonicalIR, SemanticId
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing._closure import (
    TraceClosureEvidence,
    TraceInstrumentationChannel,
)
from contract4agents.tracing._models import (
    NormalizedTrace,
    ProviderCorrelation,
    RedactionMetadata,
    TraceAttempt,
    TraceEvent,
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
from contract4agents.tracing._provider_evidence import (
    EvidenceState,
    ProviderOutcome,
    ProviderOutcomeCategory,
    ProviderOutcomeEvidence,
    ProviderOutcomePhase,
    ProviderUsageEvidence,
)
from contract4agents.tracing._session import NormalizedTraceSessionCore
from contract4agents.tracing._sinks import NormalizedTraceSink

_OPENAI_CAPTURED_CHANNELS: frozenset[TraceInstrumentationChannel] = frozenset(
    {
        "agent",
        "composition",
        "handoff",
        "output",
        "provider_response",
        "tool",
    }
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


class OpenAINormalizedTraceSession(NormalizedTraceSessionCore):
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
        self.router = router
        super().__init__(
            ir,
            plan,
            provider="openai",
            session_name="OpenAI trace",
            provenance_source="contract4agents-openai-capture",
            captured_channels=_OPENAI_CAPTURED_CHANNELS,
            run_id=run_id,
            thread_id=thread_id,
            sink=sink,
            prior_trace=prior_trace,
            prior_closure=prior_closure,
        )
        self._span_mapper = OpenAISpanMapper(ir)
        self._span_attempt: dict[str, TraceAttempt | None] = {}
        self._activation_token: Token[OpenAINormalizedTraceSession | None] | None = None

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
        return self._start_provider_trace(
            trace_id,
            unbound_reason="SDK trace started without attempt identity.",
            unbound_provenance_source="openai-agents-sdk-tracing-router",
        )

    def _on_trace_end(self, trace_id: str) -> None:
        self._end_provider_trace(trace_id)

    def _on_span_start(self, span: object) -> None:
        with self._lock:
            if self._closed:
                return
            trace_id = text_attr(span, "trace_id")
            attempt = self._active_provider_attempts.get(trace_id)
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
                self._active_provider_attempts.pop(trace_id, None)
                raise
            self._span_attempt[span_id] = attempt
            self._span_mapper.register(span_id, parent_id, semantic)

    def _on_span_end(self, span: object) -> None:
        with self._lock:
            if self._closed:
                return
            trace_id = text_attr(span, "trace_id")
            if trace_id not in self._active_provider_attempts:
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
                self._active_provider_attempts.pop(trace_id, None)
                raise

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
        events = self.normalize_response_events(raw_responses, agent=agent, attempt=attempt)
        selected = self._require_attempt(attempt)
        agent_id = self._require_agent(agent)
        response_ids = tuple(
            str(event.data["response_identity"])
            for event in events
            if event.event_type == "provider.response.normalized"
            and "response_identity" in event.data
        )
        self.record_provider_outcome(
            ProviderOutcomeEvidence(
                agent_id=agent_id,
                attempt_id=selected.attempt_id,
                invocation_id=selected.invocation_id,
                attempt_number=selected.number,
                phase="response",
                outcome="succeeded",
                category="transport",
                state="observed",
                classifier_provenance="openai-agents-sdk.result",
                response_id=response_ids[-1] if response_ids else None,
                response_received=bool(response_ids),
            ),
            attempt=selected,
        )
        self.record_provider_usage(
            _openai_usage_evidence(result, agent_id=agent_id, attempt=selected),
            attempt=selected,
            provider_identity=response_ids[-1] if response_ids else selected.attempt_id,
        )
        return events

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
        self._record_openai_exception_outcome(exception, agent_id=agent_id, attempt=selected)
        self.record_provider_usage(
            _openai_usage_evidence(
                getattr(getattr(exception, "run_data", None), "context_wrapper", None),
                agent_id=agent_id,
                attempt=selected,
            ),
            attempt=selected,
            provider_identity=(
                _safe_text_attr(exception, "response_id")
                or _safe_text_attr(exception, "request_id")
                or selected.attempt_id
            ),
        )
        return events

    def _record_openai_exception_outcome(
        self,
        exception: BaseException,
        *,
        agent_id: SemanticId,
        attempt: TraceAttempt,
    ) -> None:
        outcome, category, phase, state = _classify_openai_exception(exception)
        status_code = _safe_int_attr(exception, "status_code")
        request_id = _safe_text_attr(exception, "request_id")
        response_id = _safe_text_attr(exception, "response_id")
        error_code = _safe_code_attr(exception, "code")
        retry_after = _safe_float_attr(exception, "retry_after_seconds")
        if retry_after is None:
            retry_after = _safe_float_attr(exception, "retry_after")
        self.record_provider_outcome(
            ProviderOutcomeEvidence(
                agent_id=agent_id,
                attempt_id=attempt.attempt_id,
                invocation_id=attempt.invocation_id,
                attempt_number=attempt.number,
                phase=phase,
                outcome=outcome,
                category=category,
                state=state,
                classifier_provenance="openai-agents-sdk.exception-attributes",
                http_status=status_code,
                provider_error_code=error_code,
                request_id=request_id,
                response_id=response_id,
                retry_after_seconds=retry_after,
                response_received=response_id is not None,
            ),
            attempt=attempt,
        )

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

    def _attempt_binding_active(self) -> bool:
        return self._activation_token is not None

    def _release(self) -> None:
        self.router._release(self)

__all__ = [
    "OpenAINormalizedTraceRouter",
    "OpenAINormalizedTraceSession",
    "normalize_openai_exception_responses",
    "normalize_openai_response_events",
    "resolve_provider_tool_grant",
]


def _openai_usage_evidence(
    value: object,
    *,
    agent_id: SemanticId,
    attempt: TraceAttempt,
) -> ProviderUsageEvidence:
    usage = getattr(value, "context_wrapper", value)
    usage = getattr(usage, "usage", None)
    if usage is None:
        return ProviderUsageEvidence(
            scope="attempt",
            coverage="unavailable",
            aggregation_identity=attempt.attempt_id,
            aggregation_basis="one OpenAI Agents SDK host attempt",
            provenance="openai-agents-sdk.context_wrapper.usage",
            agent_id=agent_id,
            attempt_id=attempt.attempt_id,
            invocation_id=attempt.invocation_id,
        )
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    request_count = _safe_nonnegative_int(getattr(usage, "requests", None))
    input_tokens = _safe_nonnegative_int(getattr(usage, "input_tokens", None))
    cached_input_tokens = _safe_nonnegative_int(getattr(input_details, "cached_tokens", None))
    output_tokens = _safe_nonnegative_int(getattr(usage, "output_tokens", None))
    reasoning_tokens = _safe_nonnegative_int(getattr(output_details, "reasoning_tokens", None))
    total_tokens = _safe_nonnegative_int(getattr(usage, "total_tokens", None))
    if cached_input_tokens is not None and input_tokens is not None and cached_input_tokens > input_tokens:
        cached_input_tokens = None
    numeric_facts = (
        request_count,
        input_tokens,
        cached_input_tokens,
        output_tokens,
        reasoning_tokens,
        total_tokens,
    )
    if not any(value is not None for value in numeric_facts):
        coverage = "unavailable"
    elif input_tokens is None or output_tokens is None or total_tokens is None:
        coverage = "partial"
    else:
        if total_tokens != input_tokens + output_tokens:
            total_tokens = None
            coverage = "partial"
        else:
            coverage = "complete"
    return ProviderUsageEvidence(
        scope="attempt",
        coverage=coverage,  # type: ignore[arg-type]
        aggregation_identity=attempt.attempt_id,
        aggregation_basis="OpenAI Agents SDK aggregate context_wrapper.usage",
        provenance="openai-agents-sdk.context_wrapper.usage",
        request_count=request_count,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        agent_id=agent_id,
        attempt_id=attempt.attempt_id,
        invocation_id=attempt.invocation_id,
    )


def _classify_openai_exception(
    exception: BaseException,
) -> tuple[ProviderOutcome, ProviderOutcomeCategory, ProviderOutcomePhase, EvidenceState]:
    """Classify only documented Agents SDK types and stable structured facts."""

    try:
        from agents.exceptions import MCPToolCancellationError, ModelRefusalError, ModelTimeoutError
    except ImportError:  # pragma: no cover - optional dependency
        timeout_match = refusal_match = cancellation_match = False
    else:
        timeout_match = isinstance(exception, ModelTimeoutError)
        refusal_match = isinstance(exception, ModelRefusalError)
        cancellation_match = isinstance(exception, MCPToolCancellationError)
    if timeout_match:
        return "failed", "provider_timeout", "transport", "observed"
    if refusal_match:
        return "refused", "refusal", "response", "observed"
    if cancellation_match:
        return "cancelled", "cancelled", "cancellation", "observed"
    status = _safe_int_attr(exception, "status_code")
    if status in {401}:
        return "failed", "authentication", "transport", "inferred"
    if status in {403}:
        return "failed", "authorization", "transport", "inferred"
    if status in {429}:
        return "failed", "rate_limit", "transport", "inferred"
    # asyncio cancellation is host lifecycle evidence, not provider cancellation.
    if isinstance(exception, asyncio.CancelledError):
        return "unknown", "unknown", "transport", "unverified"
    return "failed", "provider_error", "transport", "inferred"


def _safe_text_attr(value: object, name: str) -> str | None:
    candidate = getattr(value, name, None)
    return candidate.strip() if isinstance(candidate, str) and candidate.strip() else None


def _safe_code_attr(value: object, name: str) -> str | None:
    candidate = _safe_text_attr(value, name)
    if candidate is None or len(candidate) > 128:
        return None
    return candidate if all(char.isalnum() or char in "._:-" for char in candidate) else None


def _safe_int_attr(value: object, name: str) -> int | None:
    candidate = getattr(value, name, None)
    return candidate if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0 else None


def _safe_float_attr(value: object, name: str) -> float | None:
    candidate = getattr(value, name, None)
    if isinstance(candidate, bool) or not isinstance(candidate, int | float):
        return None
    return float(candidate) if candidate >= 0 and math.isfinite(candidate) else None


def _safe_nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
