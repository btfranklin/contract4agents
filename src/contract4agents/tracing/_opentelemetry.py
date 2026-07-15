"""Dependency-free OpenTelemetry bridge over normalized traces."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from contract4agents.ir import Audience
from contract4agents.tracing._models import NormalizedTrace


class OpenTelemetrySpan(Protocol):
    def set_attribute(self, key: str, value: str | int | float | bool) -> object: ...

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, str | int | float | bool] | None = None,
        timestamp: int | None = None,
    ) -> object: ...

    def end(self, end_time: int | None = None) -> object: ...


class OpenTelemetryTracer(Protocol):
    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, str | int | float | bool] | None = None,
        start_time: int | None = None,
    ) -> OpenTelemetrySpan: ...


def export_open_telemetry(
    trace: NormalizedTrace,
    tracer: OpenTelemetryTracer,
    *,
    audience: Audience = "host",
) -> tuple[OpenTelemetrySpan, ...]:
    """Export one normalized run span per run without requiring an OTel dependency.

    A real ``opentelemetry.trace.Tracer`` satisfies this small structural
    protocol. Event payloads pass through the normalized trace's audience
    redaction before export.
    """

    spans: list[OpenTelemetrySpan] = []
    for run_id in trace.run_ids:
        run = trace.for_run(run_id)
        first = min(event.timestamp for event in run.events)
        last = max(event.timestamp for event in run.events)
        context = run.events[0].context
        span = tracer.start_span(
            "contract4agents.run",
            attributes={
                "contract4agents.run_id": run_id,
                "contract4agents.thread_id": context.thread_id,
                "contract4agents.contract_digest": context.contract_digest,
                "contract4agents.plan_digest": context.plan_digest,
            },
            start_time=_nanoseconds(first),
        )
        for event in run.events:
            payload = event.to_dict(audience=audience)
            semantic = payload["semantic"]
            assert isinstance(semantic, dict)
            attributes: dict[str, str | int | float | bool] = {
                "contract4agents.event_id": event.event_id,
                "contract4agents.provider": event.provider.name,
                "contract4agents.data": json.dumps(
                    payload["data"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            }
            if event.parent_event_id is not None:
                attributes["contract4agents.parent_event_id"] = event.parent_event_id
            for name in (
                "agent_id",
                "capability_id",
                "composition_id",
                "context_id",
                "grant_id",
                "isolation_id",
                "quality_id",
            ):
                value = semantic.get(name)
                if isinstance(value, str):
                    attributes[f"contract4agents.{name}"] = value
            control_ids = semantic.get("control_ids")
            if isinstance(control_ids, list):
                attributes["contract4agents.control_ids"] = ",".join(str(item) for item in control_ids)
            span.add_event(event.event_type, attributes=attributes, timestamp=_nanoseconds(event.timestamp))
        span.end(end_time=_nanoseconds(last))
        spans.append(span)
    return tuple(spans)


def _nanoseconds(timestamp: float) -> int:
    return int(timestamp * 1_000_000_000)


__all__ = ["OpenTelemetrySpan", "OpenTelemetryTracer", "export_open_telemetry"]
