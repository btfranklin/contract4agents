"""Immutable provider-neutral normalized trace values."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, cast

from contract4agents.ir import Audience, FrozenJsonValue, FrozenMap, SemanticId, freeze_json

TRACE_SCHEMA_VERSION = "1"

RedactionState = Literal["safe", "sensitive", "redacted"]

_AUDIENCES = frozenset({"model", "adapter", "host", "evaluator", "reviewer"})
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REDACTABLE_ROOTS = frozenset({"data", "evidence_refs", "provenance", "provider"})
_REDACTION_STATES = frozenset({"safe", "sensitive", "redacted"})


@dataclass(frozen=True)
class TraceRunContext:
    """Identity that must remain invariant throughout one normalized run."""

    run_id: str
    thread_id: str
    contract_digest: str
    plan_digest: str

    def __post_init__(self) -> None:
        _require_text("run_id", self.run_id)
        _require_text("thread_id", self.thread_id)
        _require_digest("contract_digest", self.contract_digest)
        _require_digest("plan_digest", self.plan_digest)


@dataclass(frozen=True, order=True)
class TraceAttempt:
    """Portable identity for one host-owned attempt within a logical run."""

    invocation_id: str
    attempt_id: str
    number: int
    retry_of: str | None = None

    def __post_init__(self) -> None:
        _require_text("attempt.invocation_id", self.invocation_id)
        _require_text("attempt.attempt_id", self.attempt_id)
        if isinstance(self.number, bool) or not isinstance(self.number, int):
            raise TypeError("attempt.number must be an integer")
        if self.number < 1:
            raise ValueError("attempt.number must be at least 1")
        if self.retry_of is not None:
            _require_text("attempt.retry_of", self.retry_of)
            if self.retry_of == self.attempt_id:
                raise ValueError("attempt.retry_of must identify an earlier attempt")
        if self.number == 1 and self.retry_of is not None:
            raise ValueError("The first attempt cannot retry an earlier attempt")
        if self.number > 1 and self.retry_of is None:
            raise ValueError("A retry attempt must identify retry_of")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "attempt_id": self.attempt_id,
            "invocation_id": self.invocation_id,
            "number": self.number,
        }
        if self.retry_of is not None:
            result["retry_of"] = self.retry_of
        return result

    @classmethod
    def from_dict(cls, value: object) -> TraceAttempt:
        payload = _require_object("attempt", value)
        _require_exact_keys(
            "attempt",
            payload,
            required={"attempt_id", "invocation_id", "number"},
            optional={"retry_of"},
        )
        number = payload["number"]
        if isinstance(number, bool) or not isinstance(number, int):
            raise TypeError("attempt.number must be an integer")
        return cls(
            invocation_id=_require_string("attempt.invocation_id", payload["invocation_id"]),
            attempt_id=_require_string("attempt.attempt_id", payload["attempt_id"]),
            number=number,
            retry_of=_optional_string("attempt.retry_of", payload.get("retry_of")),
        )


@dataclass(frozen=True)
class TraceSemanticRefs:
    """Stable contract identities associated with an event."""

    agent_id: SemanticId | None = None
    capability_id: SemanticId | None = None
    grant_id: SemanticId | None = None
    control_ids: tuple[SemanticId, ...] = field(default_factory=tuple)
    composition_id: SemanticId | None = None
    context_id: SemanticId | None = None
    isolation_id: SemanticId | None = None
    quality_id: SemanticId | None = None

    def __post_init__(self) -> None:
        if self.agent_id is not None:
            self.agent_id.require_kind("agent")
        if self.capability_id is not None:
            self.capability_id.require_kind("tool", "datasource")
        if self.grant_id is not None:
            self.grant_id.require_kind("grant")
        if self.composition_id is not None:
            self.composition_id.require_kind("edge")
        if self.context_id is not None:
            self.context_id.require_kind("context")
        if self.isolation_id is not None:
            self.isolation_id.require_kind("isolation")
        if self.quality_id is not None:
            self.quality_id.require_kind("quality")
        controls = tuple(sorted(set(self.control_ids)))
        for control_id in controls:
            control_id.require_kind("control")
        object.__setattr__(self, "control_ids", controls)

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": str(self.agent_id) if self.agent_id is not None else None,
            "capability_id": str(self.capability_id) if self.capability_id is not None else None,
            "composition_id": str(self.composition_id) if self.composition_id is not None else None,
            "context_id": str(self.context_id) if self.context_id is not None else None,
            "control_ids": [str(control_id) for control_id in self.control_ids],
            "grant_id": str(self.grant_id) if self.grant_id is not None else None,
            "isolation_id": str(self.isolation_id) if self.isolation_id is not None else None,
            "quality_id": str(self.quality_id) if self.quality_id is not None else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> TraceSemanticRefs:
        payload = _require_object("semantic", value)
        _require_exact_keys(
            "semantic",
            payload,
            required={
                "agent_id",
                "capability_id",
                "composition_id",
                "context_id",
                "grant_id",
                "isolation_id",
                "quality_id",
                "control_ids",
            },
        )
        controls = _require_array("semantic.control_ids", payload["control_ids"])
        return cls(
            agent_id=_optional_semantic_id("semantic.agent_id", payload["agent_id"]),
            capability_id=_optional_semantic_id("semantic.capability_id", payload["capability_id"]),
            composition_id=_optional_semantic_id("semantic.composition_id", payload["composition_id"]),
            context_id=_optional_semantic_id("semantic.context_id", payload["context_id"]),
            grant_id=_optional_semantic_id("semantic.grant_id", payload["grant_id"]),
            isolation_id=_optional_semantic_id("semantic.isolation_id", payload["isolation_id"]),
            quality_id=_optional_semantic_id("semantic.quality_id", payload["quality_id"]),
            control_ids=tuple(_semantic_id("semantic.control_ids", item) for item in controls),
        )


@dataclass(frozen=True)
class ProviderCorrelation:
    """Provider identity and common provider-native correlation identifiers."""

    name: str
    run_id: str | None = None
    span_id: str | None = None
    trace_id: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        _require_text("provider.name", self.name)
        for field_name in ("run_id", "span_id", "trace_id", "request_id"):
            value = getattr(self, field_name)
            if value is not None:
                _require_text(f"provider.{field_name}", value)

    def to_dict(self) -> dict[str, str]:
        result = {"name": self.name}
        for field_name in ("request_id", "run_id", "span_id", "trace_id"):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        return result

    @classmethod
    def from_dict(cls, value: object) -> ProviderCorrelation:
        payload = _require_object("provider", value)
        _require_exact_keys(
            "provider",
            payload,
            required={"name"},
            optional={"request_id", "run_id", "span_id", "trace_id"},
        )
        return cls(
            name=_require_string("provider.name", payload["name"]),
            request_id=_optional_string("provider.request_id", payload.get("request_id")),
            run_id=_optional_string("provider.run_id", payload.get("run_id")),
            span_id=_optional_string("provider.span_id", payload.get("span_id")),
            trace_id=_optional_string("provider.trace_id", payload.get("trace_id")),
        )


@dataclass(frozen=True, order=True)
class RedactionRule:
    """One JSON Pointer whose value is visible only to named audiences."""

    path: str
    visible_to: tuple[Audience, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_redactable_pointer(self.path)
        audiences = tuple(sorted(set(self.visible_to)))
        for audience in audiences:
            if audience not in _AUDIENCES:
                raise ValueError(f"Unsupported redaction audience `{audience}`")
        object.__setattr__(self, "visible_to", audiences)

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "visible_to": list(self.visible_to)}

    @classmethod
    def from_dict(cls, value: object) -> RedactionRule:
        payload = _require_object("redaction rule", value)
        _require_exact_keys("redaction rule", payload, required={"path", "visible_to"})
        audience_values = _require_array("redaction rule.visible_to", payload["visible_to"])
        audiences: list[Audience] = []
        for value in audience_values:
            audience = _require_string("redaction rule.visible_to", value)
            if audience not in _AUDIENCES:
                raise ValueError(f"Unsupported redaction audience `{audience}`")
            audiences.append(cast(Audience, audience))
        return cls(
            path=_require_string("redaction rule.path", payload["path"]),
            visible_to=tuple(audiences),
        )


@dataclass(frozen=True)
class RedactionMetadata:
    """Redaction state plus rules or already-applied redactions."""

    state: RedactionState = "safe"
    applied: tuple[str, ...] = field(default_factory=tuple)
    rules: tuple[RedactionRule, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.state not in _REDACTION_STATES:
            raise ValueError(f"Unsupported redaction state `{self.state}`")
        applied = _normalized_text_set("redaction.applied", self.applied)
        for path in applied:
            _require_redactable_pointer(path)
        rules = tuple(sorted(set(self.rules)))
        if self.state == "safe" and (applied or rules):
            raise ValueError("Safe redaction metadata cannot contain rules or applied paths")
        if self.state == "sensitive" and not rules:
            raise ValueError("Sensitive redaction metadata requires rules")
        if self.state == "redacted" and (not applied or rules):
            raise ValueError("Redacted metadata requires applied paths and cannot retain rules")
        object.__setattr__(self, "applied", applied)
        object.__setattr__(self, "rules", rules)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"applied": list(self.applied), "state": self.state}
        if self.rules:
            result["rules"] = [rule.to_dict() for rule in self.rules]
        return result

    @classmethod
    def from_dict(cls, value: object) -> RedactionMetadata:
        payload = _require_object("redaction", value)
        _require_exact_keys("redaction", payload, required={"state", "applied"}, optional={"rules"})
        state = _require_string("redaction.state", payload["state"])
        if state not in _REDACTION_STATES:
            raise ValueError(f"Unsupported redaction state `{state}`")
        applied = tuple(
            _require_string("redaction.applied", item)
            for item in _require_array("redaction.applied", payload["applied"])
        )
        rules = tuple(
            RedactionRule.from_dict(item)
            for item in _require_array("redaction.rules", payload.get("rules", []))
        )
        return cls(cast(RedactionState, state), applied, rules)


@dataclass(frozen=True)
class TraceEvent:
    """One canonical schema-version-2 normalized trace event."""

    context: TraceRunContext
    event_id: str
    parent_event_id: str | None
    event_type: str
    timestamp: float
    semantic: TraceSemanticRefs
    data: Mapping[str, object] = field(default_factory=dict)
    provider: ProviderCorrelation = field(default_factory=lambda: ProviderCorrelation("contract4agents"))
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    provenance: Mapping[str, object] = field(default_factory=dict)
    redaction: RedactionMetadata = field(default_factory=RedactionMetadata)

    def __post_init__(self) -> None:
        _require_text("event_id", self.event_id)
        if self.parent_event_id is not None:
            _require_text("parent_event_id", self.parent_event_id)
        _require_text("event_type", self.event_type)
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int | float):
            raise TypeError("timestamp must be a number")
        if not math.isfinite(self.timestamp) or self.timestamp < 0:
            raise ValueError("timestamp must be a finite non-negative number")
        object.__setattr__(self, "timestamp", float(self.timestamp))
        attempt = self.data.get("attempt")
        if attempt is not None:
            TraceAttempt.from_dict(attempt)
        if self.event_type == "attempt.selected":
            if attempt is None:
                raise ValueError("attempt.selected requires attempt identity")
            if self.data.get("outcome") not in {"succeeded", "failed"}:
                raise ValueError("attempt.selected requires a succeeded or failed outcome")
        object.__setattr__(self, "data", _freeze_object("data", self.data))
        object.__setattr__(self, "provenance", _freeze_object("provenance", self.provenance))
        object.__setattr__(self, "evidence_refs", _normalized_text_set("evidence_refs", self.evidence_refs))
        raw = self.to_dict()
        for rule in self.redaction.rules:
            if not _pointer_exists(raw, rule.path):
                raise ValueError(f"Redaction path `{rule.path}` does not exist in event `{self.event_id}`")
        for path in self.redaction.applied:
            if not _pointer_exists(raw, path) or _pointer_value(raw, path) != "[redacted]":
                raise ValueError(
                    f"Applied redaction path `{path}` is not redacted in event `{self.event_id}`"
                )

    def to_dict(self, *, audience: Audience | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "contract_digest": self.context.contract_digest,
            "data": _thaw_json(self.data),
            "event_id": self.event_id,
            "event_type": self.event_type,
            "evidence_refs": list(self.evidence_refs),
            "parent_event_id": self.parent_event_id,
            "plan_digest": self.context.plan_digest,
            "provenance": _thaw_json(self.provenance),
            "provider": self.provider.to_dict(),
            "redaction": self.redaction.to_dict(),
            "run_id": self.context.run_id,
            "schema_version": TRACE_SCHEMA_VERSION,
            "semantic": self.semantic.to_dict(),
            "thread_id": self.context.thread_id,
            "timestamp": self.timestamp,
        }
        if audience is not None:
            if audience not in _AUDIENCES:
                raise ValueError(f"Unsupported trace audience `{audience}`")
            result = _redact_for_audience(result, self.redaction, audience)
        return result

    @classmethod
    def from_dict(cls, value: object) -> TraceEvent:
        payload = _require_object("trace event", value)
        _require_exact_keys(
            "trace event",
            payload,
            required={
                "contract_digest",
                "data",
                "event_id",
                "event_type",
                "evidence_refs",
                "parent_event_id",
                "plan_digest",
                "provenance",
                "provider",
                "redaction",
                "run_id",
                "schema_version",
                "semantic",
                "thread_id",
                "timestamp",
            },
        )
        schema_version = _require_string("schema_version", payload["schema_version"])
        if schema_version != TRACE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported trace schema_version `{schema_version}`")
        timestamp = payload["timestamp"]
        if isinstance(timestamp, bool) or not isinstance(timestamp, int | float):
            raise TypeError("timestamp must be a number")
        parent_event_id = _optional_string("parent_event_id", payload["parent_event_id"])
        evidence_refs = tuple(
            _require_string("evidence_refs", item)
            for item in _require_array("evidence_refs", payload["evidence_refs"])
        )
        return cls(
            context=TraceRunContext(
                run_id=_require_string("run_id", payload["run_id"]),
                thread_id=_require_string("thread_id", payload["thread_id"]),
                contract_digest=_require_string("contract_digest", payload["contract_digest"]),
                plan_digest=_require_string("plan_digest", payload["plan_digest"]),
            ),
            event_id=_require_string("event_id", payload["event_id"]),
            parent_event_id=parent_event_id,
            event_type=_require_string("event_type", payload["event_type"]),
            timestamp=float(timestamp),
            semantic=TraceSemanticRefs.from_dict(payload["semantic"]),
            data=_require_object("data", payload["data"]),
            provider=ProviderCorrelation.from_dict(payload["provider"]),
            evidence_refs=evidence_refs,
            provenance=_require_object("provenance", payload["provenance"]),
            redaction=RedactionMetadata.from_dict(payload["redaction"]),
        )


@dataclass(frozen=True)
class NormalizedTrace:
    """A complete collection of validated normalized trace events."""

    events: tuple[TraceEvent, ...]

    def __post_init__(self) -> None:
        events = tuple(self.events)
        if not events:
            raise ValueError("A normalized trace requires at least one event")
        object.__setattr__(self, "events", events)
        _validate_trace(events)

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(sorted({event.context.run_id for event in self.events}))

    def for_run(self, run_id: str) -> NormalizedTrace:
        events = tuple(event for event in self.events if event.context.run_id == run_id)
        if not events:
            raise ValueError(f"Trace contains no events for run_id `{run_id}`")
        return NormalizedTrace(events)


def _validate_trace(events: tuple[TraceEvent, ...]) -> None:
    by_id: dict[str, TraceEvent] = {}
    contexts: dict[str, TraceRunContext] = {}
    attempts: dict[tuple[str, str], TraceAttempt] = {}
    numbers: dict[tuple[str, str, int], str] = {}
    observed_attempts: set[tuple[str, str]] = set()
    selections: dict[tuple[str, str], list[TraceAttempt]] = {}
    for event in events:
        if event.event_id in by_id:
            raise ValueError(f"Duplicate trace event_id `{event.event_id}`")
        by_id[event.event_id] = event
        existing = contexts.setdefault(event.context.run_id, event.context)
        if event.context.contract_digest != existing.contract_digest:
            raise ValueError(f"Run `{event.context.run_id}` contains mixed contract digests")
        if event.context.plan_digest != existing.plan_digest:
            raise ValueError(f"Run `{event.context.run_id}` contains mixed plan digests")
        if event.context.thread_id != existing.thread_id:
            raise ValueError(f"Run `{event.context.run_id}` contains mixed thread IDs")
        attempt_payload = event.data.get("attempt")
        if attempt_payload is not None:
            attempt = TraceAttempt.from_dict(attempt_payload)
            attempt_key = (event.context.run_id, attempt.attempt_id)
            previous = attempts.setdefault(attempt_key, attempt)
            if previous != attempt:
                raise ValueError(
                    f"Attempt `{attempt.attempt_id}` has inconsistent identity within the trace"
                )
            number_key = (event.context.run_id, attempt.invocation_id, attempt.number)
            numbered = numbers.setdefault(number_key, attempt.attempt_id)
            if numbered != attempt.attempt_id:
                raise ValueError(
                    f"Invocation `{attempt.invocation_id}` has multiple attempt IDs for number {attempt.number}"
                )
            if event.event_type == "attempt.selected":
                selection_key = (event.context.run_id, attempt.invocation_id)
                selections.setdefault(selection_key, []).append(attempt)
            else:
                observed_attempts.add(attempt_key)

    for (run_id, _), attempt in attempts.items():
        if attempt.retry_of is None:
            continue
        parent = attempts.get((run_id, attempt.retry_of))
        if parent is None:
            raise ValueError(
                f"Attempt `{attempt.attempt_id}` references missing retry_of `{attempt.retry_of}`"
            )
        if parent.invocation_id != attempt.invocation_id or parent.number + 1 != attempt.number:
            raise ValueError(
                f"Attempt `{attempt.attempt_id}` must retry the preceding attempt in its invocation"
            )
    for (run_id, invocation_id), selected in selections.items():
        if len(selected) != 1:
            raise ValueError(f"Invocation `{invocation_id}` selects multiple terminal attempts")
        if (run_id, selected[0].attempt_id) not in observed_attempts:
            raise ValueError(
                f"Invocation `{invocation_id}` selects an attempt without observed execution evidence"
            )

    for event in events:
        if event.parent_event_id is None:
            continue
        parent_event = by_id.get(event.parent_event_id)
        if parent_event is None:
            raise ValueError(
                f"Event `{event.event_id}` references missing parent_event_id `{event.parent_event_id}`"
            )
        if parent_event.context.run_id != event.context.run_id:
            raise ValueError(
                f"Event `{event.event_id}` references a parent from a different run"
            )
        _reject_parent_cycle(event, by_id)


def _reject_parent_cycle(event: TraceEvent, by_id: Mapping[str, TraceEvent]) -> None:
    seen = {event.event_id}
    parent_id = event.parent_event_id
    while parent_id is not None:
        if parent_id in seen:
            raise ValueError(f"Event `{event.event_id}` belongs to a parent reference cycle")
        seen.add(parent_id)
        parent_id = by_id[parent_id].parent_event_id


def _redact_for_audience(
    payload: dict[str, object],
    redaction: RedactionMetadata,
    audience: Audience,
) -> dict[str, object]:
    if redaction.state != "sensitive":
        return payload
    applied: list[str] = []
    for rule in redaction.rules:
        if audience not in rule.visible_to:
            _replace_pointer(payload, rule.path, "[redacted]")
            applied.append(rule.path)
    if applied:
        remaining = tuple(rule for rule in redaction.rules if audience in rule.visible_to)
        if remaining:
            payload["redaction"] = RedactionMetadata(
                "sensitive",
                applied=tuple(applied),
                rules=remaining,
            ).to_dict()
        else:
            payload["redaction"] = RedactionMetadata("redacted", applied=tuple(applied)).to_dict()
    return payload


def _pointer_segments(pointer: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise ValueError(f"Invalid redaction JSON Pointer `{pointer}`")
    segments: list[str] = []
    for raw in pointer[1:].split("/"):
        index = 0
        decoded = ""
        while index < len(raw):
            if raw[index] != "~":
                decoded += raw[index]
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                raise ValueError(f"Invalid redaction JSON Pointer `{pointer}`")
            decoded += "~" if raw[index + 1] == "0" else "/"
            index += 2
        segments.append(decoded)
    return tuple(segments)


def _pointer_exists(payload: object, pointer: str) -> bool:
    current = payload
    for segment in _pointer_segments(pointer):
        if isinstance(current, Mapping):
            if segment not in current:
                return False
            current = current[segment]
        elif isinstance(current, Sequence) and not isinstance(current, str | bytes):
            try:
                index = int(segment)
            except ValueError:
                return False
            if index < 0 or index >= len(current):
                return False
            current = current[index]
        else:
            return False
    return True


def _pointer_value(payload: object, pointer: str) -> object:
    current = payload
    for segment in _pointer_segments(pointer):
        if isinstance(current, Mapping):
            current = current[segment]
        elif isinstance(current, Sequence) and not isinstance(current, str | bytes):
            current = current[int(segment)]
        else:  # pragma: no cover - caller validates pointer existence first
            raise ValueError(f"Cannot traverse redaction path `{pointer}`")
    return current


def _replace_pointer(payload: object, pointer: str, replacement: object) -> None:
    segments = _pointer_segments(pointer)
    current = payload
    for segment in segments[:-1]:
        if isinstance(current, dict):
            current = current[segment]
        elif isinstance(current, list):
            current = current[int(segment)]
        else:  # pragma: no cover - existence validation prevents this path
            raise ValueError(f"Cannot traverse redaction path `{pointer}`")
    final = segments[-1]
    if isinstance(current, dict):
        current[final] = replacement
    elif isinstance(current, list):
        current[int(final)] = replacement
    else:  # pragma: no cover - existence validation prevents this path
        raise ValueError(f"Cannot apply redaction path `{pointer}`")


def _freeze_object(label: str, value: Mapping[str, object]) -> FrozenMap[str, FrozenJsonValue]:
    frozen = freeze_json(value)
    if not isinstance(frozen, FrozenMap):  # pragma: no cover - Mapping input guarantees this
        raise TypeError(f"{label} must be an object")
    return frozen


def _thaw_json(value: object) -> object:
    if isinstance(value, FrozenMap):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _require_object(label: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise TypeError(f"{label} keys must be strings")
    return cast(Mapping[str, object], value)


def _require_array(label: str, value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TypeError(f"{label} must be an array")
    return cast(Sequence[object], value)


def _require_exact_keys(
    label: str,
    payload: Mapping[str, object],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required - payload.keys())
    unknown = sorted(payload.keys() - required - optional)
    if missing:
        raise ValueError(f"{label} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _semantic_id(label: str, value: object) -> SemanticId:
    return SemanticId.parse(_require_string(label, value))


def _optional_semantic_id(label: str, value: object) -> SemanticId | None:
    return None if value is None else _semantic_id(label, value)


def _require_string(label: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    return value


def _optional_string(label: str, value: object) -> str | None:
    return None if value is None else _require_string(label, value)


def _normalized_text_set(label: str, values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_require_string(label, value) for value in values}))


def _require_text(label: str, value: str) -> None:
    _require_string(label, value)


def _require_digest(label: str, value: str) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical sha256 digest")


def _require_redactable_pointer(pointer: str) -> None:
    segments = _pointer_segments(pointer)
    if not segments or segments[0] not in _REDACTABLE_ROOTS:
        roots = ", ".join(sorted(_REDACTABLE_ROOTS))
        raise ValueError(f"Redaction path `{pointer}` must target one of: {roots}")


__all__ = [
    "TRACE_SCHEMA_VERSION",
    "NormalizedTrace",
    "ProviderCorrelation",
    "RedactionMetadata",
    "RedactionRule",
    "RedactionState",
    "TraceEvent",
    "TraceAttempt",
    "TraceRunContext",
    "TraceSemanticRefs",
]
