"""Typed compiler artifact models."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from contract4agents.guards import GuardPlanItem

JsonSchema = dict[str, Any]
CapabilityStatus = Literal["supported", "partial", "emulated", "unsupported"]
MANAGED_ARTIFACT_DIRS = (
    "schemas",
    "types",
    "manifests",
    "instructions",
    "evals",
    "monitors",
    "guards",
    "run-specs",
    "adapters",
    "docs",
)


class ManifestUse(TypedDict):
    name: str
    module: str
    permission: str


class ManifestHostedTool(TypedDict):
    name: str
    provider: str
    tool: str
    config: dict[str, str]
    permission: str


class ManifestDatasource(TypedDict):
    name: str
    python: str
    produces: str
    requires: list[str]
    render: str
    cache: str


class ManifestInput(TypedDict):
    name: str
    type: str
    required: bool
    python_ref: str | None


class ManifestHostContext(TypedDict):
    type: str
    python_ref: str | None


class ManifestOutput(TypedDict):
    type: str
    schema_ref: str
    python_ref: str | None


class AgentManifest(TypedDict):
    agent: str
    source_path: str
    description: str
    goal: str
    inputs: list[ManifestInput]
    output: ManifestOutput
    host_context: list[ManifestHostContext]
    tools: list[ManifestUse]
    hosted_tools: list[ManifestHostedTool]
    agents: list[ManifestUse]
    datasources: list[ManifestDatasource]
    policy: list[str]
    success: list[str]
    routes: list[str]
    composition: list[str]
    guards: list[str]
    assertions: list[str]


class EvalPack(TypedDict):
    name: str
    agent: str
    givens: dict[str, str]
    expects: list[str]
    semantic_expects: list[str]


class MonitorPack(TypedDict):
    name: str
    agent: str
    severity: str
    when: str
    expect: str


class RunSpecStage(TypedDict):
    name: str
    agent: str
    output_type: str
    cardinality: str
    manifest_ref: str
    schema_ref: str


class RunSpecArtifact(TypedDict):
    name: str
    source_path: str
    stages: list[RunSpecStage]
    assertions: list[str]


class CapabilityEntry(TypedDict):
    status: CapabilityStatus
    caveats: list[str]


CapabilityMatrix = dict[str, dict[str, CapabilityEntry]]


class TypeBinding(TypedDict):
    type: str
    source: Literal["native", "python"]
    python_ref: str | None
    schema_ref: str
    schema_hash: str


class CompilerArtifacts(TypedDict):
    schemas: dict[str, JsonSchema]
    type_bindings: list[TypeBinding]
    manifests: dict[str, AgentManifest]
    instructions: dict[str, str]
    evals: list[EvalPack]
    monitors: list[MonitorPack]
    run_specs: list[RunSpecArtifact]
    guard_plan: list[GuardPlanItem]
    adapter_capability_matrix: CapabilityMatrix
    docs: dict[str, str]


__all__ = [
    "AgentManifest",
    "CapabilityEntry",
    "CapabilityMatrix",
    "CapabilityStatus",
    "CompilerArtifacts",
    "EvalPack",
    "GuardPlanItem",
    "JsonSchema",
    "MANAGED_ARTIFACT_DIRS",
    "ManifestDatasource",
    "ManifestHostedTool",
    "ManifestHostContext",
    "ManifestInput",
    "ManifestOutput",
    "ManifestUse",
    "MonitorPack",
    "RunSpecArtifact",
    "RunSpecStage",
    "TypeBinding",
]
