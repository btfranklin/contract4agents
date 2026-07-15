"""Evidence completeness assessment for normalized traces."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Literal

from contract4agents.tracing._models import NormalizedTrace

TraceCompletenessStatus = Literal["complete", "incomplete", "unverified"]


@dataclass(frozen=True)
class TraceCompletenessResult:
    """Whether a run has enough expected telemetry to support trace claims."""

    run_id: str
    status: TraceCompletenessStatus
    reason: str
    expected_telemetry: tuple[str, ...] = field(default_factory=tuple)
    observed_telemetry: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text("run_id", self.run_id)
        _require_text("reason", self.reason)
        if self.status not in {"complete", "incomplete", "unverified"}:
            raise ValueError(f"Unsupported trace completeness status `{self.status}`")
        object.__setattr__(self, "expected_telemetry", _normalized_references(self.expected_telemetry))
        object.__setattr__(self, "observed_telemetry", _normalized_references(self.observed_telemetry))
        object.__setattr__(self, "evidence_refs", _normalized_references(self.evidence_refs))
        if self.status == "complete" and self.missing_telemetry:
            missing = ", ".join(self.missing_telemetry)
            raise ValueError(f"A complete trace cannot be missing expected telemetry: {missing}")

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    @property
    def missing_telemetry(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.expected_telemetry) - set(self.observed_telemetry)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_refs": list(self.evidence_refs),
            "expected_telemetry": list(self.expected_telemetry),
            "missing_telemetry": list(self.missing_telemetry),
            "observed_telemetry": list(self.observed_telemetry),
            "reason": self.reason,
            "run_id": self.run_id,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def assess_trace_completeness(
    trace: NormalizedTrace,
    expected_telemetry: Collection[str],
    *,
    run_id: str | None = None,
) -> TraceCompletenessResult:
    """Compare one run's event types with the plan's explicit telemetry set.

    The expected set should include lifecycle boundary events when a negative
    claim depends on proving that instrumentation covered the complete run.
    Missing evidence is `unverified`, never proof that behavior did not occur.
    """

    expected = _normalized_telemetry(expected_telemetry)
    selected = _select_run(trace, run_id)
    selected_run_id = selected.run_ids[0]
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
    if not expected:
        return TraceCompletenessResult(
            run_id=selected_run_id,
            status="unverified",
            reason="No expected telemetry was declared for this run.",
            observed_telemetry=observed,
        )
    missing = tuple(sorted(set(expected) - set(observed_expected)))
    if missing:
        return TraceCompletenessResult(
            run_id=selected_run_id,
            status="unverified",
            reason=f"Expected telemetry was not observed: {', '.join(missing)}.",
            expected_telemetry=expected,
            observed_telemetry=observed,
            evidence_refs=evidence_refs,
        )
    return TraceCompletenessResult(
        run_id=selected_run_id,
        status="complete",
        reason="All expected telemetry was observed.",
        expected_telemetry=expected,
        observed_telemetry=observed,
        evidence_refs=evidence_refs,
    )


def _select_run(trace: NormalizedTrace, run_id: str | None) -> NormalizedTrace:
    if run_id is not None:
        return trace.for_run(run_id)
    if len(trace.run_ids) != 1:
        raise ValueError("Trace contains multiple runs; pass run_id explicitly")
    return trace


def _normalized_telemetry(values: Collection[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Expected telemetry values must be non-empty strings")
        normalized.add(value)
    return tuple(sorted(normalized))


def _normalized_references(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Trace completeness references must be non-empty strings")
        normalized.add(value)
    return tuple(sorted(normalized))


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


__all__ = [
    "TraceCompletenessResult",
    "TraceCompletenessStatus",
    "assess_trace_completeness",
]
