"""Private provider-neutral normalized-trace session state."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

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
from contract4agents.tracing._provider_evidence import (
    ProviderOutcomeEvidence,
    ProviderUsageEvidence,
)
from contract4agents.tracing._sinks import NormalizedTraceSink


class NormalizedTraceSessionCore:
    """Reusable attempt, resume, event, and closure state for one provider run."""

    def __init__(
        self,
        ir: CanonicalIR,
        plan: MaterializationPlan,
        *,
        provider: str,
        session_name: str,
        provenance_source: str,
        captured_channels: Iterable[TraceInstrumentationChannel],
        run_id: str,
        thread_id: str | None = None,
        sink: NormalizedTraceSink | None = None,
        prior_trace: NormalizedTrace | None = None,
        prior_closure: TraceClosureEvidence | None = None,
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
        self._provider = provider
        self._session_name = session_name
        self._provenance_source = provenance_source
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
        self._attempt_context: ContextVar[TraceAttempt | None] = ContextVar(
            f"contract4agents_{provider}_attempt_{id(self)}",
            default=None,
        )
        self._attempts: dict[str, AttemptCaptureState] = {}
        self._active_provider_attempts: dict[str, TraceAttempt] = {}
        self._unbound_trace_ids: set[str] = set()
        self._closed = False
        self._closed_snapshot: TraceCaptureSnapshot | None = None
        self._channels: set[TraceInstrumentationChannel] = set(captured_channels)
        self._attested_channels: set[TraceInstrumentationChannel] = set()
        self._closure_evidence_refs: set[str] = set()
        self._last_provider_trace_id: str | None = None
        self._provider_event_counter = sum(
            1 for event in self._prior_events if event.event_id.startswith(f"{provider}:")
        )
        self._lock = threading.Lock()

    def normalized_trace(self) -> NormalizedTrace:
        with self._lock:
            return NormalizedTrace((*self._prior_events, *self.events))

    def emit(self, event: TraceEvent) -> None:
        """Accept an adjacent normalized event into this run's evidence."""

        if event.context != self.context:
            raise ValueError(
                f"Trace event does not match the {self._session_name} session run context"
            )
        with self._lock:
            if self._closed:
                raise RuntimeError(
                    f"A closed {self._session_name} session cannot accept evidence"
                )
            self._accept_event(event)

    def _accept_event(self, event: TraceEvent) -> None:
        if self.sink is not None:
            self.sink.emit(event)
        self.events.append(event)

    @contextmanager
    def bind_attempt(
        self,
        attempt: TraceAttempt,
        *,
        agent: str | SemanticId,
    ) -> Iterator[None]:
        """Bind attempt identity while the host executes one provider invocation."""

        if self._closed:
            raise RuntimeError(
                f"A closed {self._session_name} session cannot bind an attempt"
            )
        if not self._attempt_binding_active():
            raise RuntimeError(
                f"Enter the {self._session_name} session before binding an attempt"
            )
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
        if any(
            not isinstance(reference, str) or not reference.strip()
            for reference in selected_refs
        ):
            raise ValueError("Channel evidence references must be non-empty strings")
        self._channels.update(selected_channels)
        self._attested_channels.update(selected_channels)
        self._closure_evidence_refs.update(selected_refs)

    def snapshot(self) -> TraceCaptureSnapshot:
        """Snapshot one immutable trace and closure frontier without closing."""

        with self._lock:
            if self._closed:
                if self._closed_snapshot is None:
                    raise RuntimeError(
                        f"Closed {self._session_name} session has no capture snapshot"
                    )
                return self._closed_snapshot
            closure = self._build_closure()
            trace = NormalizedTrace((*self._prior_events, *self.events))
            return TraceCaptureSnapshot(trace, closure)

    def close(self) -> TraceCaptureSnapshot:
        """Return the final trace-plus-closure snapshot and release provider routing."""

        with self._lock:
            if self._closed_snapshot is None:
                if self._attempt_binding_active():
                    raise RuntimeError(
                        f"Exit the {self._session_name} session before closing it"
                    )
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
                            f"contract4agents:{self._provider}:session:{self.context.run_id}",
                        ),
                        provenance={"source": self._provenance_source},
                        redaction=RedactionMetadata(),
                    )
                    self._accept_event(event)
                closure = self._build_closure()
                trace = NormalizedTrace((*self._prior_events, *self.events))
                self._closed_snapshot = TraceCaptureSnapshot(trace, closure)
                self._closed = True
            snapshot = self._closed_snapshot
        self._release()
        return snapshot

    @property
    def closed_snapshot(self) -> TraceCaptureSnapshot:
        if self._closed_snapshot is None:
            raise RuntimeError(f"The {self._session_name} session has not been closed")
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
                f"contract4agents:{agent_id}:attempt:{selected.attempt_id}:"
                "output-schema-failed"
            ),
            event_type="output.schema_failed",
            agent=agent_id,
            data={"attempt": selected.to_dict(), "validation_phase": "contract_structure"},
            evidence_refs=evidence_refs,
            provenance_source="host-output-schema-validation",
        )

    def record_output_accepted(
        self,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> TraceEvent:
        """Record adapter- or host-validated canonical output evidence."""

        selected, agent_id = self._require_attempt_agent(attempt, agent)
        return self._record_host_event(
            event_id=(
                f"contract4agents:{agent_id}:attempt:{selected.attempt_id}:"
                "output-accepted"
            ),
            event_type="output.accepted",
            agent=agent_id,
            data={"attempt": selected.to_dict(), "validation_phase": "contract_structure"},
            evidence_refs=evidence_refs,
            provenance_source="adapter-output-schema-validation",
        )

    def record_host_domain_validation_started(
        self,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> TraceEvent:
        """Record that the host started application-owned domain validation."""

        return self._record_host_domain_validation(
            agent=agent,
            attempt=attempt,
            outcome="started",
            evidence_refs=evidence_refs,
        )

    def record_host_domain_validation_accepted(
        self,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> TraceEvent:
        """Record that application-owned domain validation accepted the output."""

        return self._record_host_domain_validation(
            agent=agent,
            attempt=attempt,
            outcome="accepted",
            evidence_refs=evidence_refs,
        )

    def record_host_domain_validation_failure(
        self,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> TraceEvent:
        """Record a content-free application-owned domain validation failure."""

        return self._record_host_domain_validation(
            agent=agent,
            attempt=attempt,
            outcome="failed",
            evidence_refs=evidence_refs,
        )

    def _record_host_domain_validation(
        self,
        *,
        agent: str | SemanticId,
        attempt: TraceAttempt | None,
        outcome: Literal["started", "accepted", "failed"],
        evidence_refs: tuple[str, ...],
    ) -> TraceEvent:
        selected, agent_id = self._require_attempt_agent(attempt, agent)
        return self._record_host_event(
            event_id=(
                f"contract4agents:{agent_id}:attempt:{selected.attempt_id}:"
                f"host-domain-validation-{outcome}"
            ),
            event_type=f"output.domain_validation.{outcome}",
            agent=agent_id,
            data={"attempt": selected.to_dict(), "validation_phase": "host_domain"},
            evidence_refs=evidence_refs,
            provenance_source="host-domain-validation",
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

    def record_provider_outcome(
        self,
        evidence: ProviderOutcomeEvidence,
        *,
        attempt: TraceAttempt | None = None,
        provider_identity: str | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> TraceEvent:
        """Report one validated terminal provider outcome without content."""

        self._ensure_open()
        selected = self._require_attempt(attempt)
        agent_id = self._require_agent(evidence.agent_id)
        if evidence.attempt_id != selected.attempt_id:
            raise ValueError("Provider outcome evidence attempt_id does not match the bound attempt")
        state = self._attempt_state(selected, agent_id)
        self._channels.add("provider_outcome")
        refs = tuple(evidence_refs) or tuple(
            reference
            for reference in (
                f"provider:{self._provider}:request:{evidence.request_id}" if evidence.request_id else None,
                f"provider:{self._provider}:response:{evidence.response_id}" if evidence.response_id else None,
            )
            if reference is not None
        )
        event = self._record_provider_event(
            event_type="provider.outcome.reported",
            semantic=TraceSemanticRefs(agent_id=agent_id),
            data={"evidence": evidence.to_dict()},
            attempt=selected,
            trace_id=getattr(self, "_last_provider_trace_id", None),
            request_id=evidence.request_id,
            span_id=provider_identity,
            evidence_refs=refs,
            provenance_source="contract4agents-provider-outcome-normalizer",
        )
        state.outcome_status = "complete"
        state.outcome_evidence_refs.update(event.evidence_refs)
        return event

    def report_provider_outcome(
        self,
        evidence: ProviderOutcomeEvidence,
        *,
        attempt: TraceAttempt | None = None,
        provider_identity: str | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> TraceEvent:
        """Alias for :meth:`record_provider_outcome` used by host adapters."""

        return self.record_provider_outcome(
            evidence,
            attempt=attempt,
            provider_identity=provider_identity,
            evidence_refs=evidence_refs,
        )

    def record_provider_usage(
        self,
        evidence: ProviderUsageEvidence,
        *,
        attempt: TraceAttempt | None = None,
        provider_identity: str | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> TraceEvent:
        """Report one validated usage aggregate without prompts or responses."""

        self._ensure_open()
        selected = self._require_attempt(attempt)
        state = self._attempts.get(selected.attempt_id)
        if evidence.attempt_id is not None and evidence.attempt_id != selected.attempt_id:
            raise ValueError("Provider usage evidence attempt_id does not match the bound attempt")
        if state is None:
            if evidence.agent_id is None:
                raise ValueError("Provider usage evidence requires agent_id for an unbound attempt")
            state = self._attempt_state(selected, self._require_agent(evidence.agent_id))
        if evidence.agent_id is not None and self._require_agent(evidence.agent_id) != state.agent_id:
            raise ValueError("Provider usage evidence agent_id does not match the bound attempt")
        self._channels.add("provider_usage")
        event = self._record_provider_event(
            event_type="provider.usage.reported",
            semantic=TraceSemanticRefs(agent_id=state.agent_id),
            data={"evidence": evidence.to_dict()},
            attempt=selected,
            trace_id=getattr(self, "_last_provider_trace_id", None),
            span_id=provider_identity,
            evidence_refs=tuple(evidence_refs),
            provenance_source="contract4agents-provider-usage-normalizer",
        )
        state.usage_status = "complete"
        state.usage_evidence_refs.update(event.evidence_refs)
        return event

    def report_provider_usage(
        self,
        evidence: ProviderUsageEvidence,
        *,
        attempt: TraceAttempt | None = None,
        provider_identity: str | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> TraceEvent:
        """Alias for :meth:`record_provider_usage` used by host adapters."""

        return self.record_provider_usage(
            evidence,
            attempt=attempt,
            provider_identity=provider_identity,
            evidence_refs=evidence_refs,
        )

    def _start_provider_trace(
        self,
        trace_id: str,
        *,
        unbound_reason: str,
        unbound_provenance_source: str,
    ) -> bool:
        with self._lock:
            if self._closed:
                return False
            attempt = self._current_attempt()
            if attempt is None:
                event = TraceEvent(
                    context=self.context,
                    event_id=f"{self._provider}:trace:{trace_id}:unbound",
                    parent_event_id=None,
                    event_type="instrumentation.unbound",
                    timestamp=time.time(),
                    semantic=TraceSemanticRefs(),
                    data={"reason": unbound_reason},
                    provider=ProviderCorrelation(self._provider, trace_id=trace_id),
                    evidence_refs=(f"provider:{self._provider}:{trace_id}",),
                    provenance={"source": unbound_provenance_source},
                    redaction=RedactionMetadata(),
                )
                self._accept_event(event)
                self._unbound_trace_ids.add(trace_id)
                return True
            state = self._attempts[attempt.attempt_id]
            state.provider_trace_ids.add(trace_id)
            self._active_provider_attempts[trace_id] = attempt
            self._last_provider_trace_id = trace_id
            return True

    def _end_provider_trace(self, trace_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            attempt = self._active_provider_attempts.pop(trace_id, None)
            if attempt is not None:
                self._attempts[attempt.attempt_id].ended_trace_ids.add(trace_id)

    def _mark_response_complete(
        self,
        *,
        response_ids: Iterable[str],
        evidence_refs: Iterable[str],
        reason: str,
        attempt: TraceAttempt | None = None,
    ) -> None:
        selected = attempt or self._current_attempt()
        if selected is None:
            return
        state = self._attempts[selected.attempt_id]
        state.response_ids.update(response_ids)
        state.response_evidence_refs.update(evidence_refs)
        if not state.response_failed_closed:
            state.response_status = "complete"
            state.reason = reason

    def _mark_response_unverified(
        self,
        reason: str,
        *,
        attempt: TraceAttempt | None = None,
    ) -> None:
        selected = attempt or self._current_attempt()
        if selected is None:
            return
        state = self._attempts[selected.attempt_id]
        if state.response_status != "complete" and not state.response_failed_closed:
            state.response_status = "unverified"
            state.reason = reason

    def _mark_response_incomplete(
        self,
        reason: str,
        *,
        attempt: TraceAttempt | None = None,
    ) -> None:
        selected = attempt or self._current_attempt()
        if selected is None:
            return
        state = self._attempts[selected.attempt_id]
        state.response_failed_closed = True
        state.response_status = "incomplete"
        state.reason = reason

    def _record_provider_event(
        self,
        *,
        event_type: str,
        semantic: TraceSemanticRefs,
        data: Mapping[str, object] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        request_id: str | None = None,
        parent_event_id: str | None = None,
        attempt: TraceAttempt | None = None,
        evidence_refs: Iterable[str] = (),
        provenance_source: str,
        timestamp: float | None = None,
    ) -> TraceEvent:
        """Record provider metadata without retaining provider prompts or results."""

        self._ensure_open()
        selected = attempt or self._current_attempt()
        payload = dict(data or {})
        if selected is not None:
            payload["attempt"] = selected.to_dict()
        with self._lock:
            self._provider_event_counter += 1
            event_id = (
                f"{self._provider}:{self.context.run_id}:"
                f"{self._provider_event_counter}:{event_type}"
            )
            references = tuple(evidence_refs)
            if not references:
                identity = request_id or span_id or trace_id
                if identity is not None:
                    references = (f"provider:{self._provider}:{identity}",)
            event = TraceEvent(
                context=self.context,
                event_id=event_id,
                parent_event_id=parent_event_id,
                event_type=event_type,
                timestamp=time.time() if timestamp is None else timestamp,
                semantic=semantic,
                data=payload,
                provider=ProviderCorrelation(
                    self._provider,
                    trace_id=trace_id,
                    span_id=span_id,
                    request_id=request_id,
                ),
                evidence_refs=references,
                provenance={"source": provenance_source},
                redaction=RedactionMetadata(),
            )
            self._accept_event(event)
            return event

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
            provider=self._provider,
        )

    def _attempt_state(
        self,
        attempt: TraceAttempt,
        agent_id: SemanticId,
    ) -> AttemptCaptureState:
        prior = self._prior_attempt(attempt.attempt_id)
        if prior is not None:
            if prior.attempt != attempt or prior.agent_id != agent_id:
                raise ValueError(
                    f"Attempt `{attempt.attempt_id}` conflicts with prior closure identity"
                )
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
            raise ValueError(
                f"Attempt `{attempt.attempt_id}` has inconsistent session identity"
            )
        return state

    def _prior_attempt(self, attempt_id: str) -> TraceAttemptClosure | None:
        return prior_attempt(self._prior_closure, attempt_id)

    def _close_response_path(
        self,
        state: AttemptCaptureState,
        events: tuple[TraceEvent, ...],
        reason: str,
    ) -> None:
        receipts = tuple(
            event
            for event in events
            if event.event_type == "provider.response.normalized"
        )
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
            raise ValueError(
                f"Attempt `{selected.attempt_id}` conflicts with current session identity"
            )
        if prior is not None and prior.attempt != selected:
            raise ValueError(
                f"Attempt `{selected.attempt_id}` conflicts with prior closure identity"
            )
        if self._prior_closure is not None and current is None and prior is None:
            raise ValueError(
                f"Attempt `{selected.attempt_id}` is not present in prior or current "
                "execution evidence"
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
            raise RuntimeError(
                f"A closed {self._session_name} session cannot accept evidence"
            )

    def _current_attempt(self) -> TraceAttempt | None:
        return self._attempt_context.get()

    def _current_agent_id(self) -> SemanticId:
        attempt = self._require_attempt(None)
        return self._attempts[attempt.attempt_id].agent_id

    def _maybe_current_agent_id(self) -> SemanticId | None:
        attempt = self._current_attempt()
        return (
            self._attempts[attempt.attempt_id].agent_id
            if attempt is not None
            else None
        )

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

    def _attempt_binding_active(self) -> bool:
        return True

    def _release(self) -> None:
        return None


__all__ = ["NormalizedTraceSessionCore"]
