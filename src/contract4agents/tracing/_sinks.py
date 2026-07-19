"""Public sinks for normalized runtime evidence."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from contract4agents.tracing._io import load_trace_jsonl, write_trace_jsonl
from contract4agents.tracing._models import NormalizedTrace, TraceEvent, TraceRunContext


@runtime_checkable
class NormalizedTraceSink(Protocol):
    """A destination that accepts one validated normalized trace event."""

    def emit(self, event: TraceEvent) -> None:
        """Accept one normalized trace event."""


class NoOpNormalizedTraceSink:
    """Discard normalized events."""

    def emit(self, event: TraceEvent) -> None:
        del event


class RecordingNormalizedTraceSink:
    """Record normalized events in memory in emission order."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)


class AtomicTraceFileSink:
    """Persist a single-process normalized trace through atomic file replacement.

    ``append=True`` resumes an existing trace after validating that every event
    has the exact supplied run context. Each emission validates and writes the
    complete candidate trace before advancing in-memory state. Hosts remain
    responsible for multi-process coordination and transactions with business
    state.
    """

    def __init__(
        self,
        path: Path,
        context: TraceRunContext,
        *,
        append: bool = False,
    ) -> None:
        self.path = Path(path)
        self.context = context
        self._lock = threading.Lock()
        self._events: list[TraceEvent] = []
        if append and self.path.exists():
            trace = load_trace_jsonl(self.path)
            for event in trace.events:
                if event.context != context:
                    raise ValueError(
                        f"Trace file `{self.path}` does not match the requested run context"
                    )
            self._events.extend(trace.events)

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        """Return the events durably accepted by this sink."""

        with self._lock:
            return tuple(self._events)

    def emit(self, event: TraceEvent) -> None:
        if event.context != self.context:
            raise ValueError("Trace event does not match the sink run context")
        with self._lock:
            candidate = NormalizedTrace((*self._events, event))
            write_trace_jsonl(self.path, candidate)
            self._events.append(event)

    def normalized_trace(self) -> NormalizedTrace:
        """Return the complete durably accepted trace."""

        with self._lock:
            return NormalizedTrace(tuple(self._events))


__all__ = [
    "AtomicTraceFileSink",
    "NoOpNormalizedTraceSink",
    "NormalizedTraceSink",
    "RecordingNormalizedTraceSink",
]
