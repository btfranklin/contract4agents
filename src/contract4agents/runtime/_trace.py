"""Trace event recording for Contract4Agents runtime internals."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TraceEvent:
    type: str
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)


class TraceRecorder:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.events: list[TraceEvent] = []

    def record(self, event_type: str, **data: Any) -> TraceEvent:
        event = TraceEvent(event_type, time.time(), data)
        self.events.append(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")
        return event

    def count(self, event_type: str, target: str | None = None) -> int:
        return sum(
            1
            for event in self.events
            if event.type == event_type and (target is None or target in event.data.values())
        )


__all__ = ["TraceEvent", "TraceRecorder"]
