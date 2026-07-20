"""Normalized traces, instrumentation closure, and evidence assessment."""

from contract4agents.tracing._closure import (
    TRACE_CLOSURE_MANIFEST_VERSION,
    TRACE_INSTRUMENTATION_CHANNELS,
    TraceAttemptClosure,
    TraceCaptureSnapshot,
    TraceClosureError,
    TraceClosureEvidence,
    TraceClosureManifest,
    TraceClosureStatus,
    TraceFrontier,
    TraceInstrumentationChannel,
    validate_trace_closure,
)
from contract4agents.tracing._conformance import (
    TraceConformanceError,
    TraceConformanceIssue,
    validate_trace_conformance,
)
from contract4agents.tracing._evidence import (
    TraceEvidenceAssessment,
    TraceEvidenceStatus,
    assess_trace_evidence,
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
    OpenAINormalizedTraceRouter,
    OpenAINormalizedTraceSession,
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
    "TRACE_CLOSURE_MANIFEST_VERSION",
    "TRACE_INSTRUMENTATION_CHANNELS",
    "TRACE_SCHEMA_VERSION",
    "AtomicTraceFileSink",
    "NormalizedTrace",
    "NormalizedTraceSink",
    "NoOpNormalizedTraceSink",
    "OpenTelemetrySpan",
    "OpenTelemetryTracer",
    "OpenAINormalizedTraceRouter",
    "OpenAINormalizedTraceSession",
    "ProviderCorrelation",
    "RecordingNormalizedTraceSink",
    "RedactionMetadata",
    "RedactionRule",
    "RedactionState",
    "TraceAttempt",
    "TraceAttemptClosure",
    "TraceCaptureSnapshot",
    "TraceClosureEvidence",
    "TraceClosureError",
    "TraceClosureManifest",
    "TraceClosureStatus",
    "TraceInstrumentationChannel",
    "TraceEvent",
    "TraceFrontier",
    "TraceEvidenceAssessment",
    "TraceEvidenceStatus",
    "TraceLoadError",
    "TraceConformanceError",
    "TraceConformanceIssue",
    "TraceRunContext",
    "TraceSemanticRefs",
    "assess_trace_evidence",
    "dumps_trace_jsonl",
    "export_open_telemetry",
    "load_trace_jsonl",
    "loads_trace_jsonl",
    "normalize_openai_exception_responses",
    "normalize_openai_response_events",
    "resolve_provider_tool_grant",
    "validate_trace_conformance",
    "validate_trace_closure",
    "write_trace_jsonl",
]
