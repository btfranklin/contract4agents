"""Contract-first framework-native materialization."""

from contract4agents.materialization._context import (
    ContextResolutionError,
    ContextRuntime,
    ResolvedContextValue,
)
from contract4agents.materialization._entrypoint import materialize
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue
from contract4agents.materialization._google_adk import (
    ADKSDK,
    GoogleADKMaterializationProvider,
    GoogleADKNativeAgentDescription,
    GoogleADKNativeToolDescription,
    GoogleADKSDK,
)
from contract4agents.materialization._models import (
    GraphValidationEvidence,
    MaterializationProvider,
    MaterializationResult,
    NativeAgentGraph,
    SchemaConformanceEvidence,
)
from contract4agents.materialization._openai import (
    AgentsSDK,
    NativeAgentDescription,
    NativeToolDescription,
    OpenAIMaterializationProvider,
    OpenAISDK,
)
from contract4agents.materialization._strands import (
    NativeStrandsAgentDescription,
    NativeStrandsToolDescription,
    StrandsAgentsSDK,
    StrandsMaterializationProvider,
    StrandsSDK,
)
from contract4agents.materialization._tracing import (
    MaterializationTraceEvent,
    MaterializationTraceSink,
    NoOpMaterializationTraceSink,
    RecordingMaterializationTraceSink,
)

__all__ = [
    "ADKSDK",
    "AgentsSDK",
    "ContextResolutionError",
    "ContextRuntime",
    "GoogleADKMaterializationProvider",
    "GoogleADKNativeAgentDescription",
    "GoogleADKNativeToolDescription",
    "GoogleADKSDK",
    "GraphValidationEvidence",
    "MaterializationError",
    "MaterializationIssue",
    "MaterializationProvider",
    "MaterializationResult",
    "MaterializationTraceEvent",
    "MaterializationTraceSink",
    "NativeAgentDescription",
    "NativeAgentGraph",
    "NativeToolDescription",
    "SchemaConformanceEvidence",
    "NativeStrandsAgentDescription",
    "NativeStrandsToolDescription",
    "NoOpMaterializationTraceSink",
    "OpenAIMaterializationProvider",
    "OpenAISDK",
    "RecordingMaterializationTraceSink",
    "ResolvedContextValue",
    "StrandsAgentsSDK",
    "StrandsMaterializationProvider",
    "StrandsSDK",
    "materialize",
]
