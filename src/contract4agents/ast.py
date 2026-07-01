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
        return self.type_name.rstrip("?").strip()


@dataclass(frozen=True)
class TypeDef:
    name: str
    fields: list[FieldDef]
    span: SourceSpan
    source: Literal["native", "python"] = "native"
    python_ref: str | None = None


Permission = Literal["available", "preapproved", "requires_approval", "denied", "sandboxed"]
UseKind = Literal["tool", "agent", "datasource", "hosted_tool"]


@dataclass(frozen=True)
class UseDecl:
    kind: UseKind
    name: str
    source: str
    permission: Permission = "available"
    span: SourceSpan | None = None
    config: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasourceDef:
    name: str
    python: str
    requires: list[str]
    produces: str
    render: str = "markdown"
    cache: str = "run"
    span: SourceSpan | None = None


@dataclass(frozen=True)
class AgentDef:
    name: str
    parameters: list[FieldDef]
    return_type: str
    uses: list[UseDecl]
    attributes: dict[str, Any]
    span: SourceSpan

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
class MonitorDef:
    name: str
    agent: str
    severity: str
    condition: str
    expectation: str
    span: SourceSpan


@dataclass
class ContractModule:
    path: Path
    types: list[TypeDef] = field(default_factory=list)
    datasources: list[DatasourceDef] = field(default_factory=list)
    agents: list[AgentDef] = field(default_factory=list)
    evals: list[EvalCase] = field(default_factory=list)
    monitors: list[MonitorDef] = field(default_factory=list)


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
    def monitors(self) -> list[MonitorDef]:
        return [item for module in self.modules for item in module.monitors]
