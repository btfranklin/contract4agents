"""Provider-neutral accumulation of attempt-bound trace closure evidence."""

from __future__ import annotations

from dataclasses import dataclass, field

from contract4agents.ir import SemanticId
from contract4agents.tracing._closure import (
    TraceAttemptClosure,
    TraceClosureEvidence,
    TraceClosureStatus,
    TraceFrontier,
    TraceInstrumentationChannel,
)
from contract4agents.tracing._models import TraceAttempt, TraceEvent, TraceRunContext


@dataclass
class AttemptCaptureState:
    attempt: TraceAttempt
    agent_id: SemanticId
    provider_trace_ids: set[str] = field(default_factory=set)
    ended_trace_ids: set[str] = field(default_factory=set)
    response_ids: set[str] = field(default_factory=set)
    response_status: TraceClosureStatus = "incomplete"
    response_evidence_refs: set[str] = field(default_factory=set)
    reason: str = "The response-normalization path has not been closed."


def prior_attempt(
    closure: TraceClosureEvidence | None,
    attempt_id: str,
) -> TraceAttemptClosure | None:
    if closure is None:
        return None
    return next(
        (item for item in closure.attempts if item.attempt.attempt_id == attempt_id),
        None,
    )


def build_trace_closure(
    *,
    context: TraceRunContext,
    prior_events: tuple[TraceEvent, ...],
    prior_closure: TraceClosureEvidence | None,
    events: tuple[TraceEvent, ...],
    attempts: tuple[AttemptCaptureState, ...],
    unbound_trace_ids: frozenset[str],
    channels: frozenset[TraceInstrumentationChannel],
    attested_channels: frozenset[TraceInstrumentationChannel],
    evidence_refs: frozenset[str],
    provider: str,
) -> TraceClosureEvidence:
    """Combine one captured segment with an exact prior trace frontier."""

    if (
        prior_closure is not None
        and not events
        and not attempts
        and not unbound_trace_ids
        and not evidence_refs
    ):
        return prior_closure

    current_attempts = tuple(_attempt_closure(state, provider) for state in attempts)
    if unbound_trace_ids:
        current_status: TraceClosureStatus = "incomplete"
        current_reason = "One or more SDK traces were created without attempt identity."
    elif not current_attempts:
        if prior_closure is None:
            current_status = "unverified"
            current_reason = "No attempt-scoped SDK execution was captured."
        else:
            current_status = "complete"
            current_reason = "No new SDK execution occurred in the resumed trace segment."
    elif any(
        item.lifecycle_status == "incomplete" or item.response_status == "incomplete"
        for item in current_attempts
    ):
        current_status = "incomplete"
        current_reason = "One or more captured attempts have an open instrumentation path."
    elif any(not item.complete for item in current_attempts):
        current_status = "unverified"
        current_reason = "One or more captured attempts lack verifiable instrumentation evidence."
    else:
        current_status = "complete"
        current_reason = "Every captured attempt closed its SDK lifecycle and response-normalization path."

    prior_attempts = prior_closure.attempts if prior_closure is not None else ()
    attempts_by_id = {item.attempt.attempt_id: item for item in prior_attempts}
    for item in current_attempts:
        existing = attempts_by_id.get(item.attempt.attempt_id)
        if existing is not None and existing != item:
            raise ValueError(
                f"Attempt `{item.attempt.attempt_id}` conflicts with prior closure evidence"
            )
        attempts_by_id[item.attempt.attempt_id] = item
    combined_attempts = tuple(sorted(attempts_by_id.values(), key=lambda item: item.attempt))

    if prior_closure is None:
        status = current_status
        combined_channels = tuple(channels)
        reason = current_reason
        combined_refs: set[str] = set()
    else:
        prior_status = prior_closure.status
        if "incomplete" in {prior_status, current_status}:
            status = "incomplete"
        elif "unverified" in {prior_status, current_status}:
            status = "unverified"
        else:
            status = "complete"
        if current_attempts or unbound_trace_ids:
            combined_channels = tuple(set(prior_closure.channels) & channels)
        else:
            combined_channels = tuple(set(prior_closure.channels) | attested_channels)
        reason = (
            "Prior and current trace segments were combined at an exact validated frontier; "
            f"current segment: {current_reason}"
        )
        combined_refs = set(prior_closure.evidence_refs)

    combined_refs.update(evidence_refs)
    for item in combined_attempts:
        combined_refs.update(item.evidence_refs)
    combined_events = (*prior_events, *events)
    return TraceClosureEvidence(
        context=context,
        status=status,
        reason=reason,
        frontier=TraceFrontier.from_events(combined_events),
        channels=combined_channels,
        attempts=combined_attempts,
        evidence_refs=tuple(combined_refs)
        or (f"contract4agents:{provider}:session:{context.run_id}",),
    )


def _attempt_closure(
    state: AttemptCaptureState,
    provider: str,
) -> TraceAttemptClosure:
    lifecycle_status: TraceClosureStatus = (
        "complete"
        if state.provider_trace_ids and state.provider_trace_ids == state.ended_trace_ids
        else "incomplete"
    )
    evidence_refs = set(state.response_evidence_refs)
    evidence_refs.update(
        f"provider:{provider}:{trace_id}" for trace_id in state.provider_trace_ids
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


__all__ = ["AttemptCaptureState", "build_trace_closure", "prior_attempt"]
