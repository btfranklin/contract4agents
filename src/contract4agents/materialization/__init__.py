"""Contract-first framework-native materialization."""

from contract4agents.materialization._context import (
    ContextResolutionError,
    ContextRuntime,
    NoOpRuntimeTraceSink,
    RecordingRuntimeTraceSink,
    ResolvedContextValue,
    RuntimeTraceSink,
)
from contract4agents.materialization._entrypoint import materialize
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue
from contract4agents.materialization._models import (
    GraphValidationEvidence,
    MaterializationProvider,
    MaterializationResult,
    NativeAgentGraph,
)
from contract4agents.materialization._openai import (
    AgentsSDK,
    NativeAgentDescription,
    OpenAIMaterializationProvider,
    OpenAISDK,
)
from contract4agents.materialization._tracing import (
    MaterializationTraceEvent,
    NoOpTraceSink,
    RecordingTraceSink,
    TraceSink,
)

__all__ = [
    "AgentsSDK",
    "ContextResolutionError",
    "ContextRuntime",
    "GraphValidationEvidence",
    "MaterializationError",
    "MaterializationIssue",
    "MaterializationProvider",
    "MaterializationResult",
    "MaterializationTraceEvent",
    "NativeAgentDescription",
    "NativeAgentGraph",
    "NoOpTraceSink",
    "NoOpRuntimeTraceSink",
    "OpenAIMaterializationProvider",
    "OpenAISDK",
    "RecordingTraceSink",
    "RecordingRuntimeTraceSink",
    "ResolvedContextValue",
    "RuntimeTraceSink",
    "TraceSink",
    "materialize",
]
