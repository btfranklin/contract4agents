"""Normalized trace schema, deterministic JSONL, and evidence completeness."""

from contract4agents.tracing._completeness import (
    TraceCompletenessResult,
    TraceCompletenessStatus,
    assess_trace_completeness,
)
from contract4agents.tracing._io import (
    TraceLoadError,
    dumps_trace_jsonl,
    load_trace_jsonl,
    loads_trace_jsonl,
    write_trace_jsonl,
)
from contract4agents.tracing._models import (
    TRACE_SCHEMA_VERSION,
    NormalizedTrace,
    ProviderCorrelation,
    RedactionMetadata,
    RedactionRule,
    RedactionState,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
)
from contract4agents.tracing._openai import OpenAINormalizedTraceProcessor
from contract4agents.tracing._opentelemetry import (
    OpenTelemetrySpan,
    OpenTelemetryTracer,
    export_open_telemetry,
)

__all__ = [
    "TRACE_SCHEMA_VERSION",
    "NormalizedTrace",
    "OpenTelemetrySpan",
    "OpenTelemetryTracer",
    "OpenAINormalizedTraceProcessor",
    "ProviderCorrelation",
    "RedactionMetadata",
    "RedactionRule",
    "RedactionState",
    "TraceEvent",
    "TraceCompletenessResult",
    "TraceCompletenessStatus",
    "TraceLoadError",
    "TraceRunContext",
    "TraceSemanticRefs",
    "assess_trace_completeness",
    "dumps_trace_jsonl",
    "export_open_telemetry",
    "load_trace_jsonl",
    "loads_trace_jsonl",
    "write_trace_jsonl",
]
