"""Identity-bound instrumentation closure at an exact normalized-trace frontier."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, cast

from contract4agents._strict_json import (
    json_array,
    json_object,
    json_string,
    json_strings,
    require_exact_keys,
)
from contract4agents.ir import SemanticId
from contract4agents.tracing._models import (
    NormalizedTrace,
    TraceAttempt,
    TraceEvent,
    TraceRunContext,
)

TraceClosureStatus = Literal["complete", "incomplete", "unverified"]
TraceInstrumentationChannel = Literal[
    "agent",
    "approval",
    "composition",
    "datasource",
    "guardrail",
    "handoff",
    "output",
    "provider_response",
    "tool",
]
TRACE_INSTRUMENTATION_CHANNELS: tuple[TraceInstrumentationChannel, ...] = (
    "agent",
    "approval",
    "composition",
    "datasource",
    "guardrail",
    "handoff",
    "output",
    "provider_response",
    "tool",
)
TRACE_CLOSURE_MANIFEST_VERSION = "1"

_STATUSES = frozenset({"complete", "incomplete", "unverified"})
_CHANNELS = frozenset(TRACE_INSTRUMENTATION_CHANNELS)


class TraceClosureError(ValueError):
    """Trace closure identities do not match normalized evidence."""


@dataclass(frozen=True)
class TraceFrontier:
    """Exact ordered normalized-trace prefix attested by closure evidence."""

    event_count: int
    prefix_digest: str

    def __post_init__(self) -> None:
        if isinstance(self.event_count, bool) or not isinstance(self.event_count, int):
            raise TypeError("Trace frontier event_count must be an integer")
        if self.event_count < 0:
            raise ValueError("Trace frontier event_count cannot be negative")
        if not _canonical_digest(self.prefix_digest):
            raise ValueError("Trace frontier prefix_digest must be a canonical sha256 digest")

    @classmethod
    def from_events(cls, events: Iterable[TraceEvent]) -> Self:
        selected = tuple(events)
        payload = "".join(
            json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for event in selected
        )
        return cls(
            event_count=len(selected),
            prefix_digest=f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}",
        )

    @classmethod
    def from_trace(cls, trace: NormalizedTrace) -> Self:
        return cls.from_events(trace.events)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_count": self.event_count,
            "prefix_digest": self.prefix_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        payload = json_object("trace frontier", value)
        require_exact_keys("trace frontier", payload, {"event_count", "prefix_digest"})
        event_count = payload["event_count"]
        if isinstance(event_count, bool) or not isinstance(event_count, int):
            raise TypeError("Trace frontier event_count must be an integer")
        return cls(
            event_count=event_count,
            prefix_digest=json_string("prefix_digest", payload["prefix_digest"]),
        )


@dataclass(frozen=True)
class TraceAttemptClosure:
    """Closure evidence for one host-owned runner attempt."""

    attempt: TraceAttempt
    agent_id: SemanticId
    lifecycle_status: TraceClosureStatus
    response_status: TraceClosureStatus
    provider_trace_ids: tuple[str, ...] = ()
    response_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    reason: str = "Attempt instrumentation was assessed."

    def __post_init__(self) -> None:
        self.agent_id.require_kind("agent")
        _status("lifecycle_status", self.lifecycle_status)
        _status("response_status", self.response_status)
        _text("reason", self.reason)
        object.__setattr__(self, "provider_trace_ids", _references("Provider trace ID", self.provider_trace_ids))
        object.__setattr__(self, "response_ids", _references("Provider response ID", self.response_ids))
        object.__setattr__(self, "evidence_refs", _references("Evidence reference", self.evidence_refs))
        if self.lifecycle_status == "complete" and not self.provider_trace_ids and not self.evidence_refs:
            raise ValueError("Complete attempt lifecycle closure requires a provider trace ID or evidence reference")
        if self.response_status == "complete" and not self.evidence_refs:
            raise ValueError("Complete response closure requires an evidence reference")

    @property
    def complete(self) -> bool:
        return self.lifecycle_status == self.response_status == "complete"

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": str(self.agent_id),
            "attempt": self.attempt.to_dict(),
            "evidence_refs": list(self.evidence_refs),
            "lifecycle_status": self.lifecycle_status,
            "provider_trace_ids": list(self.provider_trace_ids),
            "reason": self.reason,
            "response_ids": list(self.response_ids),
            "response_status": self.response_status,
        }

    @classmethod
    def from_dict(cls, value: object) -> TraceAttemptClosure:
        payload = json_object("attempt closure", value)
        require_exact_keys(
            "attempt closure",
            payload,
            {
                "agent_id",
                "attempt",
                "evidence_refs",
                "lifecycle_status",
                "provider_trace_ids",
                "reason",
                "response_ids",
                "response_status",
            },
        )
        return cls(
            attempt=TraceAttempt.from_dict(payload["attempt"]),
            agent_id=SemanticId.parse(json_string("agent_id", payload["agent_id"])),
            lifecycle_status=cast(TraceClosureStatus, json_string("lifecycle_status", payload["lifecycle_status"])),
            response_status=cast(TraceClosureStatus, json_string("response_status", payload["response_status"])),
            provider_trace_ids=json_strings("provider_trace_ids", payload["provider_trace_ids"]),
            response_ids=json_strings("response_ids", payload["response_ids"]),
            evidence_refs=json_strings("evidence_refs", payload["evidence_refs"]),
            reason=json_string("reason", payload["reason"]),
        )


@dataclass(frozen=True)
class TraceClosureEvidence:
    """Host- or adapter-attested closure for every invocation in one trace run."""

    context: TraceRunContext
    status: TraceClosureStatus
    reason: str
    frontier: TraceFrontier
    channels: tuple[TraceInstrumentationChannel, ...]
    attempts: tuple[TraceAttemptClosure, ...]
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _status("status", self.status)
        _text("reason", self.reason)
        channels = tuple(sorted(set(self.channels)))
        unknown = sorted(set(channels) - _CHANNELS)
        if unknown:
            raise ValueError(f"Unsupported instrumentation channels: {', '.join(unknown)}")
        object.__setattr__(self, "channels", channels)
        attempts = tuple(self.attempts)
        attempt_ids = [item.attempt.attempt_id for item in attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("Trace closure attempt IDs must be unique")
        invocation_numbers = [(item.attempt.invocation_id, item.attempt.number) for item in attempts]
        if len(invocation_numbers) != len(set(invocation_numbers)):
            raise ValueError("Trace closure attempts must be unique per invocation number")
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(self, "evidence_refs", _references("Evidence reference", self.evidence_refs))
        if self.status == "complete":
            if self.frontier.event_count == 0:
                raise ValueError("Complete trace closure requires a non-empty trace frontier")
            if not channels:
                raise ValueError("Complete trace closure requires at least one instrumentation channel")
            if not attempts:
                raise ValueError("Complete trace closure requires at least one attempt")
            if not self.evidence_refs:
                raise ValueError("Complete trace closure requires an evidence reference")
            if any(not item.complete for item in attempts):
                raise ValueError("Complete trace closure cannot contain an incomplete attempt")
        _validate_retry_chains(attempts)

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

    def covers(self, channel: TraceInstrumentationChannel) -> bool:
        return self.complete and channel in self.channels

    def to_dict(self) -> dict[str, object]:
        return {
            "attempts": [item.to_dict() for item in self.attempts],
            "channels": list(self.channels),
            "contract_digest": self.context.contract_digest,
            "evidence_refs": list(self.evidence_refs),
            "frontier": self.frontier.to_dict(),
            "plan_digest": self.context.plan_digest,
            "reason": self.reason,
            "run_id": self.context.run_id,
            "status": self.status,
            "thread_id": self.context.thread_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> TraceClosureEvidence:
        payload = json_object("trace closure", value)
        require_exact_keys(
            "trace closure",
            payload,
            {
                "attempts",
                "channels",
                "contract_digest",
                "evidence_refs",
                "frontier",
                "plan_digest",
                "reason",
                "run_id",
                "status",
                "thread_id",
            },
        )
        return cls(
            context=TraceRunContext(
                json_string("run_id", payload["run_id"]),
                json_string("thread_id", payload["thread_id"]),
                json_string("contract_digest", payload["contract_digest"]),
                json_string("plan_digest", payload["plan_digest"]),
            ),
            status=cast(TraceClosureStatus, json_string("status", payload["status"])),
            reason=json_string("reason", payload["reason"]),
            frontier=TraceFrontier.from_dict(payload["frontier"]),
            channels=cast(tuple[TraceInstrumentationChannel, ...], json_strings("channels", payload["channels"])),
            attempts=tuple(TraceAttemptClosure.from_dict(item) for item in json_array("attempts", payload["attempts"])),
            evidence_refs=json_strings("evidence_refs", payload["evidence_refs"]),
        )

    @classmethod
    def from_json(cls, source: str) -> TraceClosureEvidence:
        try:
            payload = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid trace-closure JSON: {exc}") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True)
class TraceCaptureSnapshot:
    """One internally consistent normalized-trace and closure snapshot."""

    trace: NormalizedTrace
    closure: TraceClosureEvidence

    def __post_init__(self) -> None:
        validate_trace_closure(self.trace, self.closure)


@dataclass(frozen=True)
class TraceClosureManifest:
    """Versioned closure evidence for every run in a normalized trace artifact."""

    closures: tuple[TraceClosureEvidence, ...]
    version: str = TRACE_CLOSURE_MANIFEST_VERSION

    def __post_init__(self) -> None:
        if self.version != TRACE_CLOSURE_MANIFEST_VERSION:
            raise ValueError(
                f"Unsupported trace-closure manifest version `{self.version}`; "
                f"expected `{TRACE_CLOSURE_MANIFEST_VERSION}`"
            )
        run_ids = [item.context.run_id for item in self.closures]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("Trace-closure manifest run IDs must be unique")

    def to_dict(self) -> dict[str, object]:
        return {"closures": [item.to_dict() for item in self.closures], "version": self.version}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, value: object) -> TraceClosureManifest:
        payload = json_object("trace-closure manifest", value)
        require_exact_keys("trace-closure manifest", payload, {"closures", "version"})
        version = json_string("version", payload["version"])
        if version != TRACE_CLOSURE_MANIFEST_VERSION:
            raise ValueError(
                f"Unsupported trace-closure manifest version `{version}`; "
                f"expected `{TRACE_CLOSURE_MANIFEST_VERSION}`"
            )
        return cls(
            closures=tuple(
                TraceClosureEvidence.from_dict(item) for item in json_array("closures", payload["closures"])
            ),
            version=version,
        )

    @classmethod
    def from_json(cls, source: str) -> TraceClosureManifest:
        try:
            payload = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid trace-closure manifest JSON: {exc}") from exc
        return cls.from_dict(payload)

    @classmethod
    def load(cls, path: Path | str) -> TraceClosureManifest:
        return cls.from_json(Path(path).read_text())


def validate_trace_closure(trace: NormalizedTrace, closure: TraceClosureEvidence) -> None:
    """Reject closure evidence whose identities do not match its normalized run."""

    try:
        selected = trace.for_run(closure.context.run_id)
    except ValueError as exc:
        raise TraceClosureError(str(exc)) from exc
    context = selected.events[0].context
    if context != closure.context:
        raise TraceClosureError("Trace closure context does not match the normalized trace")
    frontier = TraceFrontier.from_trace(selected)
    if closure.frontier != frontier:
        raise TraceClosureError(
            "Trace closure frontier does not match the complete normalized trace"
        )
    trace_attempts = {
        TraceAttempt.from_dict(event.data["attempt"]).attempt_id
        for event in selected.events
        if event.data.get("attempt") is not None
    }
    closure_attempts = {item.attempt.attempt_id for item in closure.attempts}
    if closure.complete and trace_attempts != closure_attempts:
        raise TraceClosureError("Complete trace closure must cover exactly the attempts in the normalized trace")
    if not trace_attempts.issubset(closure_attempts):
        raise TraceClosureError("Trace closure is missing an attempt observed in the normalized trace")
    for item in closure.attempts:
        attempt_events = tuple(
            event
            for event in selected.events
            if event.data.get("attempt") is not None
            and TraceAttempt.from_dict(event.data["attempt"]).attempt_id == item.attempt.attempt_id
        )
        if closure.complete and not attempt_events:
            raise TraceClosureError(f"Trace closure attempt `{item.attempt.attempt_id}` has no normalized events")
        observed_trace_ids = {
            event.provider.trace_id for event in attempt_events if event.provider.trace_id is not None
        }
        if item.lifecycle_status == "complete" and observed_trace_ids != set(item.provider_trace_ids):
            raise TraceClosureError(
                f"Attempt `{item.attempt.attempt_id}` provider trace IDs do not match normalized evidence"
            )
        observed_response_ids = {
            str(event.data["response_identity"])
            for event in attempt_events
            if event.event_type == "provider.response.normalized" and "response_identity" in event.data
        }
        if item.response_status == "complete" and observed_response_ids != set(item.response_ids):
            raise TraceClosureError(
                f"Attempt `{item.attempt.attempt_id}` response IDs do not match normalization receipts"
            )


def _validate_retry_chains(attempts: tuple[TraceAttemptClosure, ...]) -> None:
    by_id = {item.attempt.attempt_id: item.attempt for item in attempts}
    for item in attempts:
        attempt = item.attempt
        if attempt.retry_of is None:
            continue
        parent = by_id.get(attempt.retry_of)
        if parent is None:
            raise ValueError(f"Trace closure attempt `{attempt.attempt_id}` references a missing retry parent")
        if parent.invocation_id != attempt.invocation_id or parent.number + 1 != attempt.number:
            raise ValueError(f"Trace closure attempt `{attempt.attempt_id}` does not follow its retry parent")


def _status(label: str, value: str) -> None:
    if value not in _STATUSES:
        raise ValueError(f"Unsupported {label} `{value}`")


def _text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _canonical_digest(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def _references(label: str, values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        _text(label, value)
        normalized.add(value)
    return tuple(sorted(normalized))


__all__ = [
    "TRACE_CLOSURE_MANIFEST_VERSION",
    "TRACE_INSTRUMENTATION_CHANNELS",
    "TraceAttemptClosure",
    "TraceCaptureSnapshot",
    "TraceClosureEvidence",
    "TraceClosureError",
    "TraceClosureManifest",
    "TraceClosureStatus",
    "TraceInstrumentationChannel",
    "TraceFrontier",
    "validate_trace_closure",
]
