"""Normalize available Harness evidence without raw customer content."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping

from contract4agents.ir import SemanticId
from contract4agents.tracing._models import (
    ProviderCorrelation,
    TraceAttempt,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
)
from contract4agents.tracing._sinks import NormalizedTraceSink


class HarnessTraceEmitter:
    """Write events into the existing normalized trace sink.

    A response stream does not prove instrumentation closure for AWS internals.
    Applications must retain that assurance limit.
    """

    def __init__(
        self,
        *,
        context: TraceRunContext,
        attempt: TraceAttempt,
        agent_id: SemanticId,
        deployment: Mapping[str, object],
        sink: NormalizedTraceSink | None,
        parent_event_id: str | None = None,
    ) -> None:
        self.context = context
        self.attempt = attempt
        self.agent_id = agent_id
        self.deployment = deployment
        self.sink = sink
        self.parent_event_id = parent_event_id

    def emit(
        self,
        event_type: str,
        *,
        data: Mapping[str, object] | None = None,
        semantic: TraceSemanticRefs | None = None,
        request_id: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        event_id: str | None = None,
        span_id: str | None = None,
    ) -> str:
        identifier = event_id or f"agentcore_harness:{uuid.uuid4()}"
        payload = dict(data or {})
        payload["attempt"] = self.attempt.to_dict()
        event = TraceEvent(
            context=self.context,
            event_id=identifier,
            parent_event_id=self.parent_event_id,
            event_type=event_type,
            timestamp=time.time(),
            semantic=semantic or TraceSemanticRefs(agent_id=self.agent_id),
            data=payload,
            provider=ProviderCorrelation("agentcore_harness", request_id=request_id, span_id=span_id),
            provenance={"source": "agentcore-harness-adapter", "deployment": dict(self.deployment)},
            evidence_refs=evidence_refs,
        )
        if self.sink is not None:
            self.sink.emit(event)
        return identifier
