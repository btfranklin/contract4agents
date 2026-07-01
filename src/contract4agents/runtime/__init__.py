"""Public runtime API for Contract4Agents."""

from __future__ import annotations

from contract4agents.runtime._datasources import (
    ContextValue,
    DatasourceCallable,
    DatasourceContext,
    DatasourceRegistry,
    DatasourceSpec,
    RuntimeContext,
    RuntimeStateValue,
    datasource,
)
from contract4agents.runtime._errors import (
    AmbiguousDatasource,
    ContractRuntimeError,
    DatasourceExecutionFailed,
    DatasourcePermissionDenied,
    DatasourceResolutionCycle,
    MissingContextSlot,
    ToolExecutionFailed,
    ToolPermissionDenied,
)
from contract4agents.runtime._tools import FakeToolRegistry, ToolCallable, ToolSpec
from contract4agents.runtime._trace import (
    KNOWN_TRACE_EVENT_TYPES,
    TRACE_SCHEMA_VERSION,
    TraceEvent,
    TraceRecorder,
    TraceScopeError,
    scope_trace,
)
from contract4agents.runtime._trace_io import (
    TraceDiagnostic,
    TraceDiagnosticSeverity,
    TraceFileError,
    TraceLoadResult,
    load_trace_jsonl,
    load_trace_jsonl_with_diagnostics,
)
from contract4agents.runtime._utils import load_python_ref, run_async

__all__ = [
    "AmbiguousDatasource",
    "ContextValue",
    "ContractRuntimeError",
    "DatasourceCallable",
    "DatasourceContext",
    "DatasourceExecutionFailed",
    "DatasourcePermissionDenied",
    "DatasourceRegistry",
    "DatasourceResolutionCycle",
    "DatasourceSpec",
    "FakeToolRegistry",
    "KNOWN_TRACE_EVENT_TYPES",
    "MissingContextSlot",
    "RuntimeContext",
    "RuntimeStateValue",
    "TRACE_SCHEMA_VERSION",
    "ToolCallable",
    "ToolExecutionFailed",
    "ToolPermissionDenied",
    "ToolSpec",
    "TraceDiagnostic",
    "TraceDiagnosticSeverity",
    "TraceEvent",
    "TraceFileError",
    "TraceLoadResult",
    "TraceRecorder",
    "TraceScopeError",
    "datasource",
    "load_trace_jsonl",
    "load_trace_jsonl_with_diagnostics",
    "load_python_ref",
    "run_async",
    "scope_trace",
]
