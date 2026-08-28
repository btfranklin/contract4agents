"""Public result and provider models for materialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from contract4agents.compiler import CompilerArtifacts
from contract4agents.ir import CanonicalIR, FrozenJsonValue, FrozenMap, SemanticId, freeze_json
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._tracing import MaterializationTraceSink
from contract4agents.planning import MaterializationPlan, PlannerCapabilities
from contract4agents.runtime import EnvironmentEnforcementEvidence, EnvironmentProvider
from contract4agents.target_bindings import TargetBinding


@dataclass(frozen=True)
class SchemaConformanceEvidence:
    semantic_id: SemanticId
    boundary: str
    declared_schema: object
    materialized_schema: object

    def __post_init__(self) -> None:
        if not self.boundary:
            raise ValueError("Schema conformance boundary cannot be empty")
        object.__setattr__(self, "declared_schema", freeze_json(self.declared_schema))
        object.__setattr__(self, "materialized_schema", freeze_json(self.materialized_schema))
        if not isinstance(self.declared_schema, FrozenMap) or not isinstance(
            self.materialized_schema, FrozenMap
        ):
            raise TypeError("Schema conformance evidence requires object schemas")

    @property
    def matches(self) -> bool:
        return self.declared_schema == self.materialized_schema

    @property
    def declared_digest(self) -> str:
        return _schema_digest(cast(FrozenJsonValue, self.declared_schema))

    @property
    def materialized_digest(self) -> str:
        return _schema_digest(cast(FrozenJsonValue, self.materialized_schema))

    def to_dict(self) -> dict[str, object]:
        return {
            "boundary": self.boundary,
            "declared_digest": self.declared_digest,
            "declared_schema": _thaw(cast(FrozenJsonValue, self.declared_schema)),
            "matches": self.matches,
            "materialized_digest": self.materialized_digest,
            "materialized_schema": _thaw(cast(FrozenJsonValue, self.materialized_schema)),
            "semantic_id": str(self.semantic_id),
        }

    @classmethod
    def from_dict(cls, value: object) -> SchemaConformanceEvidence:
        if not isinstance(value, dict):
            raise TypeError("Schema conformance evidence must be an object")
        required = {
            "boundary",
            "declared_digest",
            "declared_schema",
            "matches",
            "materialized_digest",
            "materialized_schema",
            "semantic_id",
        }
        if set(value) != required:
            raise ValueError("Schema conformance evidence has unexpected keys")
        evidence = cls(
            semantic_id=SemanticId.parse(_required_string(value, "semantic_id")),
            boundary=_required_string(value, "boundary"),
            declared_schema=freeze_json(value["declared_schema"]),
            materialized_schema=freeze_json(value["materialized_schema"]),
        )
        if not isinstance(value["matches"], bool) or value["matches"] != evidence.matches:
            raise ValueError("Schema conformance match status is inconsistent")
        if value["declared_digest"] != evidence.declared_digest:
            raise ValueError("Declared schema digest is inconsistent")
        if value["materialized_digest"] != evidence.materialized_digest:
            raise ValueError("Materialized schema digest is inconsistent")
        return evidence


@dataclass(frozen=True)
class GraphValidationEvidence:
    adapter: str
    adapter_version: str
    contract_digest: str
    plan_digest: str
    agent_ids: tuple[SemanticId, ...]
    grant_ids: tuple[SemanticId, ...]
    composition_ids: tuple[SemanticId, ...]
    schema_conformance: tuple[SchemaConformanceEvidence, ...]

    @property
    def complete(self) -> bool:
        return bool(self.schema_conformance) and all(item.matches for item in self.schema_conformance)

    def to_dict(self) -> dict[str, object]:
        return {
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "agent_ids": [str(item) for item in self.agent_ids],
            "complete": self.complete,
            "composition_ids": [str(item) for item in self.composition_ids],
            "contract_digest": self.contract_digest,
            "grant_ids": [str(item) for item in self.grant_ids],
            "plan_digest": self.plan_digest,
            "schema_conformance": [item.to_dict() for item in self.schema_conformance],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: object) -> GraphValidationEvidence:
        if not isinstance(value, dict):
            raise TypeError("Graph validation evidence must be an object")
        required = {
            "adapter",
            "adapter_version",
            "agent_ids",
            "complete",
            "composition_ids",
            "contract_digest",
            "grant_ids",
            "plan_digest",
            "schema_conformance",
        }
        if set(value) != required:
            raise ValueError("Graph validation evidence has unexpected keys")
        evidence = cls(
            adapter=_required_string(value, "adapter"),
            adapter_version=_required_string(value, "adapter_version"),
            contract_digest=_required_string(value, "contract_digest"),
            plan_digest=_required_string(value, "plan_digest"),
            agent_ids=_semantic_ids(value, "agent_ids"),
            grant_ids=_semantic_ids(value, "grant_ids"),
            composition_ids=_semantic_ids(value, "composition_ids"),
            schema_conformance=tuple(
                SchemaConformanceEvidence.from_dict(item)
                for item in _required_list(value, "schema_conformance")
            ),
        )
        if not isinstance(value["complete"], bool) or value["complete"] != evidence.complete:
            raise ValueError("Graph validation completeness is inconsistent")
        return evidence

    @classmethod
    def from_json(cls, source: str) -> GraphValidationEvidence:
        try:
            value: object = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid graph validation evidence JSON: {exc}") from exc
        return cls.from_dict(value)

    @classmethod
    def load(cls, path: Path | str) -> GraphValidationEvidence:
        return cls.from_json(Path(path).read_text())


@dataclass(frozen=True)
class NativeAgentGraph:
    """A framework-native graph and the resolved host implementations it uses."""

    agents: FrozenMap[SemanticId, object]
    output_types: FrozenMap[str, type[object]]
    implementations: FrozenMap[SemanticId, object]
    grant_objects: FrozenMap[SemanticId, object]
    composition_objects: FrozenMap[SemanticId, object]
    context: ContextRuntime
    environment_evidence: tuple[EnvironmentEnforcementEvidence, ...]
    validation: GraphValidationEvidence

    def agent(self, name: str) -> object:
        for identifier, native_agent in self.agents.items():
            if identifier.parts[0] == name:
                return native_agent
        raise KeyError(name)


@dataclass(frozen=True)
class MaterializationResult:
    graph: NativeAgentGraph
    plan: MaterializationPlan

    @property
    def agents(self) -> FrozenMap[str, object]:
        """Return native agents by the contract names users wrote."""

        return FrozenMap(
            (identifier.parts[0], native_agent)
            for identifier, native_agent in self.graph.agents.items()
        )

    @property
    def context(self) -> ContextRuntime:
        """Return the typed context resolver wired into the native graph."""

        return self.graph.context

    @property
    def structural_output_types(self) -> FrozenMap[str, type[object]]:
        """Return generated contract types that exclude application domain validators."""

        return self.graph.output_types


@runtime_checkable
class MaterializationProvider(Protocol):
    """Injectable adapter-specific native graph constructor."""

    adapter: str

    def planner_capabilities(
        self,
        environment: EnvironmentProvider | None,
    ) -> PlannerCapabilities:
        """Return the exact mappings implemented by this provider configuration."""

    def build_graph(
        self,
        *,
        ir: CanonicalIR,
        artifacts: CompilerArtifacts,
        target: TargetBinding,
        plan: MaterializationPlan,
        implementations: FrozenMap[SemanticId, object],
        output_types: FrozenMap[str, type[object]],
        context_runtime: ContextRuntime,
        environment: EnvironmentProvider | None,
        materialization_trace_sink: MaterializationTraceSink,
    ) -> NativeAgentGraph:
        """Construct and validate the complete native graph."""


__all__ = [
    "GraphValidationEvidence",
    "MaterializationProvider",
    "MaterializationResult",
    "NativeAgentGraph",
    "SchemaConformanceEvidence",
]


def _schema_digest(value: FrozenJsonValue) -> str:
    source = json.dumps(_thaw(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"


def _thaw(value: FrozenJsonValue) -> object:
    if isinstance(value, FrozenMap):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _required_string(value: dict[object, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise TypeError(f"{key} must be a non-empty string")
    return item


def _required_list(value: dict[object, object], key: str) -> list[object]:
    item = value.get(key)
    if not isinstance(item, list):
        raise TypeError(f"{key} must be an array")
    return item


def _semantic_ids(value: dict[object, object], key: str) -> tuple[SemanticId, ...]:
    items = _required_list(value, key)
    if any(not isinstance(item, str) for item in items):
        raise TypeError(f"{key} entries must be strings")
    return tuple(SemanticId.parse(cast(str, item)) for item in items)
