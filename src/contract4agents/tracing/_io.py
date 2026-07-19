"""Canonical JSONL input and output for normalized traces."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from contract4agents.ir import Audience
from contract4agents.tracing._models import NormalizedTrace, TraceEvent


class TraceLoadError(ValueError):
    """Raised when normalized trace input fails schema or whole-trace validation."""


def dumps_trace_jsonl(trace: NormalizedTrace, *, audience: Audience | None = None) -> str:
    """Serialize a trace deterministically, optionally applying audience redaction."""

    return "".join(
        json.dumps(
            event.to_dict(audience=audience),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for event in trace.events
    )


def write_trace_jsonl(path: Path, trace: NormalizedTrace, *, audience: Audience | None = None) -> None:
    """Atomically write canonical JSONL, replacing any previous file contents."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(dumps_trace_jsonl(trace, audience=audience))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise


def loads_trace_jsonl(content: str) -> NormalizedTrace:
    """Load and validate a complete normalized trace from JSONL text."""

    events: list[TraceEvent] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TraceLoadError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        try:
            events.append(TraceEvent.from_dict(payload))
        except (TypeError, ValueError) as exc:
            raise TraceLoadError(f"line {line_number}: {exc}") from exc
    try:
        return NormalizedTrace(tuple(events))
    except (TypeError, ValueError) as exc:
        raise TraceLoadError(str(exc)) from exc


def load_trace_jsonl(path: Path) -> NormalizedTrace:
    """Load and validate a complete normalized trace file."""

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TraceLoadError(f"Could not read trace file `{path}`: {exc}") from exc
    return loads_trace_jsonl(content)


__all__ = [
    "TraceLoadError",
    "dumps_trace_jsonl",
    "load_trace_jsonl",
    "loads_trace_jsonl",
    "write_trace_jsonl",
]
