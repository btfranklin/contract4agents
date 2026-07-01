"""Trace JSONL loading helpers for Contract4Agents internals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from contract4agents.runtime._trace import (
    KNOWN_TRACE_EVENT_TYPES,
    TRACE_ENVELOPE_INDEX_FIELDS,
    TRACE_SCHEMA_VERSION,
    TraceEvent,
    TraceRecorder,
)

TraceDiagnosticSeverity = Literal["fatal", "warning"]


@dataclass(frozen=True)
class TraceDiagnostic:
    severity: TraceDiagnosticSeverity
    code: str
    message: str
    path: Path | None = None
    line_number: int | None = None
    event_id: str | None = None
    event_type: str | None = None

    def format(self) -> str:
        location = ""
        if self.path is not None:
            location = str(self.path)
            if self.line_number is not None:
                location = f"{location}:{self.line_number}"
            location = f"{location}: "
        context = []
        if self.event_id is not None:
            context.append(f"event_id={self.event_id}")
        if self.event_type is not None:
            context.append(f"event_type={self.event_type}")
        suffix = f" ({', '.join(context)})" if context else ""
        return f"{location}{self.severity.upper()} {self.code}: {self.message}{suffix}"


@dataclass(frozen=True)
class TraceLoadResult:
    trace: TraceRecorder
    diagnostics: list[TraceDiagnostic]

    @property
    def ok(self) -> bool:
        return not any(item.severity == "fatal" for item in self.diagnostics)


class TraceFileError(ValueError):
    """Raised when a trace JSONL file cannot be read as Contract4Agents trace events."""


def load_trace_jsonl_with_diagnostics(path: Path) -> TraceLoadResult:
    trace = TraceRecorder()
    diagnostics: list[TraceDiagnostic] = []
    try:
        with path.open() as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                event, line_diagnostics = _trace_event(line, path, line_number)
                diagnostics.extend(line_diagnostics)
                if event is not None:
                    trace.events.append(event)
    except OSError as exc:
        diagnostics.append(
            TraceDiagnostic(
                "fatal",
                "TRACE001",
                f"Could not read trace file: {exc}",
                path=path,
            )
        )
    return TraceLoadResult(trace, diagnostics)


def load_trace_jsonl(path: Path) -> TraceRecorder:
    result = load_trace_jsonl_with_diagnostics(path)
    if not result.ok:
        fatal_diagnostics = [item.format() for item in result.diagnostics if item.severity == "fatal"]
        raise TraceFileError("\n".join(fatal_diagnostics))
    return result.trace


def _trace_event(line: str, path: Path, line_number: int) -> tuple[TraceEvent | None, list[TraceDiagnostic]]:
    diagnostics: list[TraceDiagnostic] = []
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        return (
            None,
            [
                TraceDiagnostic(
                    "fatal",
                    "TRACE002",
                    f"Invalid trace JSON: {exc}",
                    path=path,
                    line_number=line_number,
                )
            ],
        )

    if not isinstance(payload, dict):
        return (
            None,
            [
                TraceDiagnostic(
                    "fatal",
                    "TRACE003",
                    "Trace JSONL lines must be JSON objects",
                    path=path,
                    line_number=line_number,
                )
            ],
        )

    event_id = payload.get("event_id") if isinstance(payload.get("event_id"), str) else None
    event_type = payload.get("event_type") if isinstance(payload.get("event_type"), str) else None
    diagnostics.extend(_validate_envelope(payload, path, line_number, event_id, event_type))
    if any(item.severity == "fatal" for item in diagnostics):
        return None, diagnostics

    assert event_type is not None
    assert event_id is not None
    timestamp = float(payload["timestamp"])
    event = TraceEvent(event_type, timestamp, _event_data(payload))
    if event_type not in KNOWN_TRACE_EVENT_TYPES:
        diagnostics.append(
            TraceDiagnostic(
                "warning",
                "TRACE015",
                f"Unknown trace event type `{event_type}`",
                path=path,
                line_number=line_number,
                event_id=event_id,
                event_type=event_type,
            )
        )
    return event, diagnostics


def _validate_envelope(
    payload: dict[str, Any],
    path: Path,
    line_number: int,
    event_id: str | None,
    event_type: str | None,
) -> list[TraceDiagnostic]:
    diagnostics: list[TraceDiagnostic] = []
    if "type" in payload:
        diagnostics.append(
            TraceDiagnostic(
                "fatal",
                "TRACE004",
                "Legacy top-level `type` is not supported; use `event_type`",
                path=path,
                line_number=line_number,
                event_id=event_id,
                event_type=event_type,
            )
        )

    required_string_fields = ("schema_version", "event_id", "event_type")
    for field_name in required_string_fields:
        if field_name not in payload:
            diagnostics.append(_fatal(path, line_number, event_id, event_type, "TRACE005", f"Missing `{field_name}`"))
        elif not isinstance(payload[field_name], str) or not payload[field_name]:
            diagnostics.append(
                _fatal(path, line_number, event_id, event_type, "TRACE006", f"`{field_name}` must be a string")
            )

    if isinstance(payload.get("schema_version"), str) and payload["schema_version"] != TRACE_SCHEMA_VERSION:
        diagnostics.append(
            _fatal(
                path,
                line_number,
                event_id,
                event_type,
                "TRACE007",
                f"Unsupported trace schema_version `{payload['schema_version']}`",
            )
        )

    if "timestamp" not in payload:
        diagnostics.append(_fatal(path, line_number, event_id, event_type, "TRACE008", "Missing `timestamp`"))
    elif isinstance(payload["timestamp"], bool) or not isinstance(payload["timestamp"], int | float):
        diagnostics.append(_fatal(path, line_number, event_id, event_type, "TRACE009", "`timestamp` must be a number"))

    if "data" in payload and not isinstance(payload["data"], dict):
        diagnostics.append(_fatal(path, line_number, event_id, event_type, "TRACE010", "`data` must be an object"))
    if "provider" in payload and not isinstance(payload["provider"], dict):
        diagnostics.append(_fatal(path, line_number, event_id, event_type, "TRACE011", "`provider` must be an object"))

    for field_name in ("run_id", *TRACE_ENVELOPE_INDEX_FIELDS):
        if field_name in payload and not isinstance(payload[field_name], str):
            diagnostics.append(
                _fatal(path, line_number, event_id, event_type, "TRACE012", f"`{field_name}` must be a string")
            )
    return diagnostics


def _fatal(
    path: Path,
    line_number: int,
    event_id: str | None,
    event_type: str | None,
    code: str,
    message: str,
) -> TraceDiagnostic:
    return TraceDiagnostic(
        "fatal",
        code,
        message,
        path=path,
        line_number=line_number,
        event_id=event_id,
        event_type=event_type,
    )


def _event_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data", {}))
    for field_name in (
        "schema_version",
        "run_id",
        "event_id",
        "event_type",
        *TRACE_ENVELOPE_INDEX_FIELDS,
    ):
        if field_name in payload:
            data[field_name] = payload[field_name]
    data["data"] = dict(payload.get("data", {}))
    data["provider"] = dict(payload.get("provider", {}))
    return data


__all__ = [
    "TRACE_SCHEMA_VERSION",
    "TraceDiagnostic",
    "TraceDiagnosticSeverity",
    "TraceFileError",
    "TraceLoadResult",
    "load_trace_jsonl",
    "load_trace_jsonl_with_diagnostics",
]
