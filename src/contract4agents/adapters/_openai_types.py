"""Shared OpenAI adapter model types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from contract4agents.assertions import RunEvaluationResult
from contract4agents.compiler import AgentManifest, CompilerArtifacts, ManifestDatasource, ManifestInput
from contract4agents.guards import GuardPlanItem
from contract4agents.runtime import TraceRecorder


@dataclass(frozen=True)
class OpenAIAdapterResult:
    final_output: Any
    last_agent: str | None
    raw_result: Any


@dataclass(frozen=True)
class OpenAIAgentFactoryCaveat:
    agent: str
    kind: str
    message: str


@dataclass(frozen=True)
class OpenAIToolRegistration:
    value: Any
    raw_callable: bool = False
    description: str | None = None


@dataclass(frozen=True)
class OpenAIToolPlan:
    agent: str
    name: str
    permission: str
    sdk_name: str
    tool: Any
    source: str
    wrapped: bool = False
    requires_approval: bool = False


@dataclass(frozen=True)
class OpenAIHostedToolPlan:
    agent: str
    name: str
    provider: str
    tool_name: str
    config: dict[str, str]
    permission: str
    tool: Any


@dataclass(frozen=True)
class OpenAICompositionPlan:
    agent: str
    target_agent: str
    mode: Literal["agent_as_tool", "handoff", "unsupported", "unwired"]
    sdk_object: Any | None = None
    source: str = "undeclared"


@dataclass(frozen=True)
class OpenAIAgentPlan:
    agent: str
    manifest: AgentManifest
    source_path: str
    instruction_ref: str
    instructions: str
    model: Any
    output_type_name: str
    output_schema_ref: str
    output_type: Any
    tools: list[OpenAIToolPlan] = field(default_factory=list)
    hosted_tools: list[OpenAIHostedToolPlan] = field(default_factory=list)
    composition: list[OpenAICompositionPlan] = field(default_factory=list)
    inputs: list[ManifestInput] = field(default_factory=list)
    datasources: list[ManifestDatasource] = field(default_factory=list)
    guards: list[GuardPlanItem] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    caveats: list[OpenAIAgentFactoryCaveat] = field(default_factory=list)


@dataclass(frozen=True)
class OpenAIAdapterPlan:
    artifacts: CompilerArtifacts
    agents: dict[str, OpenAIAgentPlan]
    caveats: list[OpenAIAgentFactoryCaveat]


@dataclass(frozen=True)
class OpenAIAgentFactoryResult:
    agents: dict[str, Any]
    caveats: list[OpenAIAgentFactoryCaveat]
    plan: OpenAIAdapterPlan


@dataclass(frozen=True)
class OpenAIApprovalRequest:
    tool: str
    approved: bool | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


ApprovalCallback = Callable[[OpenAIApprovalRequest], bool | Awaitable[bool]]


@dataclass(frozen=True)
class OpenAIContractRunResult:
    adapter_result: OpenAIAdapterResult
    assertion_result: RunEvaluationResult
    trace: TraceRecorder
    approvals: list[OpenAIApprovalRequest] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.assertion_result.passed


class OpenAIAdapterUnavailable(RuntimeError):
    pass


class OpenAIAgentFactoryError(ValueError):
    pass


__all__ = [
    "ApprovalCallback",
    "OpenAIAdapterPlan",
    "OpenAIAdapterResult",
    "OpenAIAdapterUnavailable",
    "OpenAIAgentFactoryCaveat",
    "OpenAIAgentFactoryError",
    "OpenAIAgentFactoryResult",
    "OpenAIAgentPlan",
    "OpenAIApprovalRequest",
    "OpenAICompositionPlan",
    "OpenAIContractRunResult",
    "OpenAIHostedToolPlan",
    "OpenAIToolPlan",
    "OpenAIToolRegistration",
]
