"""Trace JSONL loading helpers for Contract4Agents internals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contract4agents.runtime._trace import TraceEvent, TraceRecorder


class TraceFileError(ValueError):
    """Raised when a trace JSONL file cannot be read as Contract4Agents trace events."""


def load_trace_jsonl(path: Path) -> TraceRecorder:
    trace = TraceRecorder()
    try:
        with path.open() as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = _trace_payload(line, path, line_number)
                trace.events.append(
                    TraceEvent(
                        type=payload["type"],
                        timestamp=float(payload.get("timestamp", 0.0)),
                        data=payload.get("data", {}),
                    )
                )
    except OSError as exc:
        raise TraceFileError(f"Could not read trace file {path}: {exc}") from exc
    return trace


def _trace_payload(line: str, path: Path, line_number: int) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise TraceFileError(f"Invalid trace JSON at {path}:{line_number}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        raise TraceFileError(f"Invalid trace event at {path}:{line_number}: missing string type")
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise TraceFileError(f"Invalid trace event at {path}:{line_number}: data must be an object")
    return payload


__all__ = ["TraceFileError", "load_trace_jsonl"]
