"""AST nodes for Contract4Agents source files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class SourceSpan:
    path: Path
    line: int
    column: int = 1

    def display(self) -> str:
        return f"{self.path}:{self.line}:{self.column}"


@dataclass(frozen=True)
class FieldDef:
    name: str
    type_name: str
    nullable: bool = False
    default: str | None = None
    span: SourceSpan | None = None

    @property
    def normalized_type(self) -> str:
        return self.type_name.strip()


@dataclass(frozen=True)
class TypeDef:
    name: str
    fields: list[FieldDef]
    span: SourceSpan


RunSpecStageCardinality = Literal["one", "optional", "many"]
Availability = Literal["enabled", "denied"]
Authorization = Literal["preapproved", "approval_required"]
ExecutionBoundary = Literal["host", "provider_hosted", "remote"]
ContextOrigin = Literal["invocation", "parent", "handoff", "stage", "datasource", "external"]


@dataclass(frozen=True)
class GrantDef:
    capability: str
    availability: Availability | str | None
    authorization: Authorization | str | None
    execution: ExecutionBoundary | str | None
    isolation: str | None = None
    span: SourceSpan | None = None


@dataclass(frozen=True)
class ContextRequirement:
    name: str
    type_name: str
    origin: ContextOrigin | str
    source: str | None = None
    mappings: dict[str, str] = field(default_factory=dict)
    span: SourceSpan | None = None


@dataclass(frozen=True)
class ToolDef:
    name: str
    parameters: list[FieldDef]
    return_type: str
    description: str
    side_effect: bool | None
    span: SourceSpan


@dataclass(frozen=True)
class DatasourceDef:
    name: str
    parameters: list[FieldDef]
    return_type: str
    description: str
    render: str = "markdown"
    cache: str = "run"
    span: SourceSpan | None = None


@dataclass(frozen=True)
class ExternalContextDef:
    name: str
    type_name: str
    description: str
    sensitivity: str
    render: str
    span: SourceSpan


@dataclass(frozen=True)
class AgentDef:
    name: str
    parameters: list[FieldDef]
    return_type: str
    attributes: dict[str, Any]
    span: SourceSpan
    attribute_spans: dict[str, SourceSpan] = field(default_factory=dict)
    grants: list[GrantDef] = field(default_factory=list)
    context: list[ContextRequirement] = field(default_factory=list)

    def list_attr(self, key: str) -> list[str]:
        value = self.attributes.get(key, [])
        return value if isinstance(value, list) else []

    def text_attr(self, key: str) -> str:
        value = self.attributes.get(key, "")
        return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class EvalCase:
    name: str
    agent: str
    givens: dict[str, str]
    expects: list[str]
    semantic_expects: list[str]
    span: SourceSpan


@dataclass(frozen=True)
class CompositionDef:
    name: str
    source_agent: str
    target_agent: str
    mode: str
    description: str
    history: str
    mappings: dict[str, str]
    isolation: str | None
    span: SourceSpan


@dataclass(frozen=True)
class IsolationDef:
    name: str
    dimensions: dict[str, str]
    span: SourceSpan


@dataclass(frozen=True)
class ControlDef:
    name: str
    agent: str
    attributes: dict[str, Any]
    span: SourceSpan


@dataclass(frozen=True)
class QualityDef:
    name: str
    agent: str
    rubric: str
    audiences: list[str]
    span: SourceSpan


@dataclass(frozen=True)
class OperationalControlDef:
    name: str
    agent: str
    attributes: dict[str, Any]
    span: SourceSpan


@dataclass(frozen=True)
class RunSpecDef:
    name: str
    stages: list[str]
    assertions: list[str]
    attributes: dict[str, Any]
    span: SourceSpan
    attribute_spans: dict[str, SourceSpan] = field(default_factory=dict)


@dataclass
class ContractModule:
    path: Path
    types: list[TypeDef] = field(default_factory=list)
    datasources: list[DatasourceDef] = field(default_factory=list)
    agents: list[AgentDef] = field(default_factory=list)
    evals: list[EvalCase] = field(default_factory=list)
    run_specs: list[RunSpecDef] = field(default_factory=list)
    tools: list[ToolDef] = field(default_factory=list)
    external_contexts: list[ExternalContextDef] = field(default_factory=list)
    compositions: list[CompositionDef] = field(default_factory=list)
    isolations: list[IsolationDef] = field(default_factory=list)
    controls: list[ControlDef] = field(default_factory=list)
    qualities: list[QualityDef] = field(default_factory=list)
    operational_controls: list[OperationalControlDef] = field(default_factory=list)


@dataclass
class ContractProject:
    root: Path
    modules: list[ContractModule]

    @property
    def types(self) -> dict[str, TypeDef]:
        return {item.name: item for module in self.modules for item in module.types}

    @property
    def datasources(self) -> dict[str, DatasourceDef]:
        return {item.name: item for module in self.modules for item in module.datasources}

    @property
    def agents(self) -> dict[str, AgentDef]:
        return {item.name: item for module in self.modules for item in module.agents}

    @property
    def evals(self) -> list[EvalCase]:
        return [item for module in self.modules for item in module.evals]

    @property
    def run_specs(self) -> dict[str, RunSpecDef]:
        return {item.name: item for module in self.modules for item in module.run_specs}

    @property
    def tools(self) -> dict[str, ToolDef]:
        return {item.name: item for module in self.modules for item in module.tools}

    @property
    def external_contexts(self) -> dict[str, ExternalContextDef]:
        return {item.name: item for module in self.modules for item in module.external_contexts}

    @property
    def compositions(self) -> dict[str, CompositionDef]:
        return {item.name: item for module in self.modules for item in module.compositions}

    @property
    def isolations(self) -> dict[str, IsolationDef]:
        return {item.name: item for module in self.modules for item in module.isolations}

    @property
    def controls(self) -> list[ControlDef]:
        return [item for module in self.modules for item in module.controls]

    @property
    def qualities(self) -> list[QualityDef]:
        return [item for module in self.modules for item in module.qualities]

    @property
    def operational_controls(self) -> list[OperationalControlDef]:
        return [item for module in self.modules for item in module.operational_controls]
