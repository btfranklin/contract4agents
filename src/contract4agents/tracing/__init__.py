"""Normalized trace schema, deterministic JSONL, and evidence completeness."""

from contract4agents.tracing._completeness import (
    TraceCompletenessResult,
    TraceCompletenessStatus,
    assess_trace_completeness,
)
from contract4agents.tracing._conformance import (
    TraceConformanceError,
    TraceConformanceIssue,
    validate_trace_conformance,
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
    TraceAttempt,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
)
from contract4agents.tracing._openai import (
    OpenAINormalizedTraceProcessor,
    normalize_openai_exception_responses,
    normalize_openai_response_events,
    resolve_provider_tool_grant,
)
from contract4agents.tracing._opentelemetry import (
    OpenTelemetrySpan,
    OpenTelemetryTracer,
    export_open_telemetry,
)
from contract4agents.tracing._sinks import (
    AtomicTraceFileSink,
    NoOpNormalizedTraceSink,
    NormalizedTraceSink,
    RecordingNormalizedTraceSink,
)

__all__ = [
    "TRACE_SCHEMA_VERSION",
    "AtomicTraceFileSink",
    "NormalizedTrace",
    "NormalizedTraceSink",
    "NoOpNormalizedTraceSink",
    "OpenTelemetrySpan",
    "OpenTelemetryTracer",
    "OpenAINormalizedTraceProcessor",
    "ProviderCorrelation",
    "RecordingNormalizedTraceSink",
    "RedactionMetadata",
    "RedactionRule",
    "RedactionState",
    "TraceAttempt",
    "TraceEvent",
    "TraceCompletenessResult",
    "TraceCompletenessStatus",
    "TraceLoadError",
    "TraceConformanceError",
    "TraceConformanceIssue",
    "TraceRunContext",
    "TraceSemanticRefs",
    "assess_trace_completeness",
    "dumps_trace_jsonl",
    "export_open_telemetry",
    "load_trace_jsonl",
    "loads_trace_jsonl",
    "normalize_openai_exception_responses",
    "normalize_openai_response_events",
    "resolve_provider_tool_grant",
    "validate_trace_conformance",
    "write_trace_jsonl",
]
