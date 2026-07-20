"""Event occurrence and instrumentation-closure assessment for traces."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Literal

from contract4agents.tracing._closure import (
    TraceClosureError,
    TraceClosureEvidence,
    TraceInstrumentationChannel,
    validate_trace_closure,
)
from contract4agents.tracing._models import NormalizedTrace

TraceEvidenceStatus = Literal["complete", "incomplete", "unverified"]


@dataclass(frozen=True)
class TraceEvidenceAssessment:
    """Whether a run has enough event and closure evidence for trace claims."""

    run_id: str
    status: TraceEvidenceStatus
    reason: str
    expected_event_types: tuple[str, ...] = field(default_factory=tuple)
    observed_event_types: tuple[str, ...] = field(default_factory=tuple)
    closure_digest: str | None = None
    closed_channels: tuple[TraceInstrumentationChannel, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text("run_id", self.run_id)
        _require_text("reason", self.reason)
        if self.status not in {"complete", "incomplete", "unverified"}:
            raise ValueError(f"Unsupported trace evidence status `{self.status}`")
        object.__setattr__(self, "expected_event_types", _normalized_references(self.expected_event_types))
        object.__setattr__(self, "observed_event_types", _normalized_references(self.observed_event_types))
        object.__setattr__(self, "closed_channels", tuple(sorted(set(self.closed_channels))))
        object.__setattr__(self, "evidence_refs", _normalized_references(self.evidence_refs))
        if self.status == "complete" and self.missing_event_types:
            missing = ", ".join(self.missing_event_types)
            raise ValueError(f"Complete trace evidence cannot be missing event types: {missing}")

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    @property
    def missing_event_types(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.expected_event_types) - set(self.observed_event_types)))

    def proves_channel_closed(self, channel: TraceInstrumentationChannel) -> bool:
        """Whether closure evidence proves one instrumentation channel complete."""

        return self.closure_digest is not None and channel in self.closed_channels

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_refs": list(self.evidence_refs),
            "closure_digest": self.closure_digest,
            "closed_channels": list(self.closed_channels),
            "expected_event_types": list(self.expected_event_types),
            "missing_event_types": list(self.missing_event_types),
            "observed_event_types": list(self.observed_event_types),
            "reason": self.reason,
            "run_id": self.run_id,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def assess_trace_evidence(
    trace: NormalizedTrace,
    expected_event_types: Collection[str],
    *,
    closure: TraceClosureEvidence | None = None,
    run_id: str | None = None,
) -> TraceEvidenceAssessment:
    """Assess event-family occurrence and explicit identity-bound run closure.

    Event-family occurrence is diagnostic only. Negative claims require a
    complete closure object covering their instrumentation channel.
    """

    expected = _normalized_event_types(expected_event_types)
    selected = _select_run(trace, run_id)
    selected_run_id = selected.run_ids[0]
    if closure is not None:
        validate_trace_closure(selected, closure)
        if closure.context.run_id != selected_run_id:
            raise TraceClosureError("Trace closure run_id does not match the selected run")
    observed = tuple(sorted({event.event_type for event in selected.events}))
    observed_expected = tuple(item for item in expected if item in observed)
    evidence_refs = tuple(
        sorted(
            {
                f"trace-event:{event.event_id}"
                for event in selected.events
                if event.event_type in expected
            }
            | {
                reference
                for event in selected.events
                if event.event_type in expected
                for reference in event.evidence_refs
            }
        )
    )
    closure_refs = closure.evidence_refs if closure is not None else ()
    closure_digest = closure.digest if closure is not None and closure.complete else None
    closed_channels = closure.channels if closure is not None and closure.complete else ()
    combined_refs = tuple(sorted(set(evidence_refs) | set(closure_refs)))
    if not expected:
        return TraceEvidenceAssessment(
            run_id=selected_run_id,
            status="unverified",
            reason="No expected event types were declared for this run.",
            observed_event_types=observed,
            closure_digest=closure_digest,
            closed_channels=closed_channels,
            evidence_refs=combined_refs,
        )
    missing = tuple(sorted(set(expected) - set(observed_expected)))
    if missing:
        return TraceEvidenceAssessment(
            run_id=selected_run_id,
            status="incomplete" if closure is not None and closure.status == "complete" else "unverified",
            reason=f"Expected event types were not observed: {', '.join(missing)}.",
            expected_event_types=expected,
            observed_event_types=observed,
            closure_digest=closure_digest,
            closed_channels=closed_channels,
            evidence_refs=combined_refs,
        )
    if closure is None:
        return TraceEvidenceAssessment(
            run_id=selected_run_id,
            status="unverified",
            reason="Expected event families were observed, but run-closure evidence was not supplied.",
            expected_event_types=expected,
            observed_event_types=observed,
            evidence_refs=evidence_refs,
        )
    if not closure.complete:
        return TraceEvidenceAssessment(
            run_id=selected_run_id,
            status=closure.status,
            reason=f"Expected event families were observed, but trace closure is {closure.status}: {closure.reason}",
            expected_event_types=expected,
            observed_event_types=observed,
            evidence_refs=combined_refs,
        )
    return TraceEvidenceAssessment(
        run_id=selected_run_id,
        status="complete",
        reason="All expected event types were observed and instrumentation closure was verified.",
        expected_event_types=expected,
        observed_event_types=observed,
        closure_digest=closure.digest,
        closed_channels=closure.channels,
        evidence_refs=combined_refs,
    )


def _select_run(trace: NormalizedTrace, run_id: str | None) -> NormalizedTrace:
    if run_id is not None:
        return trace.for_run(run_id)
    if len(trace.run_ids) != 1:
        raise ValueError("Trace contains multiple runs; pass run_id explicitly")
    return trace


def _normalized_event_types(values: Collection[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Expected event types must be non-empty strings")
        normalized.add(value)
    return tuple(sorted(normalized))


def _normalized_references(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Trace evidence references must be non-empty strings")
        normalized.add(value)
    return tuple(sorted(normalized))


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


__all__ = [
    "TraceEvidenceAssessment",
    "TraceEvidenceStatus",
    "assess_trace_evidence",
]
