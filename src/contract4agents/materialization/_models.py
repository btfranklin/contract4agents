"""Public result and provider models for materialization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast, runtime_checkable

from pydantic import ValidationError

from contract4agents.compiler import CompilerArtifacts, artifact_digests
from contract4agents.ir import CanonicalIR, FrozenJsonValue, FrozenMap, SemanticId, freeze_json
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue
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
        if not isinstance(self.declared_schema, FrozenMap) or not isinstance(self.materialized_schema, FrozenMap):
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


ConfigurationStatus = Literal["passed", "violated", "unverified"]
ConfigurationObservationSource = Literal[
    "native_readback",
    "native_schema",
    "adapter_boundary",
    "generated_wrapper",
]


@dataclass(frozen=True)
class ConfigurationConformanceEvidence:
    """Evidence for one planned native configuration property.

    Values are canonical JSON when safe.  Adapters must use the digest fields
    for arbitrary provider payloads so credentials and provider request bodies
    never enter an evidence artifact.  ``planned_present`` and
    ``observed_present`` preserve the difference between an omitted option and
    an explicit JSON null.
    """

    semantic_id: SemanticId
    property_path: str
    planned_value: object = None
    observed_value: object = None
    status: ConfigurationStatus = "unverified"
    observation_source: ConfigurationObservationSource = "native_readback"
    reason: str | None = None
    required: bool = True
    planned_present: bool = True
    observed_present: bool = True
    planned_digest: str | None = None
    observed_digest: str | None = None
    planned_redacted: bool = False
    observed_redacted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.semantic_id, SemanticId):
            raise TypeError("Configuration semantic ID must be a SemanticId")
        self.semantic_id.require_kind(
            "agent",
            "grant",
            "edge",
            "control",
            "tool",
            "datasource",
            "external",
        )
        if not isinstance(self.property_path, str):
            raise TypeError("Configuration property path must be a string")
        if not self.property_path or self.property_path != self.property_path.strip():
            raise ValueError("Configuration property path cannot be empty")
        if not isinstance(self.status, str) or self.status not in {"passed", "violated", "unverified"}:
            raise ValueError("Configuration status is invalid")
        if not isinstance(self.observation_source, str) or self.observation_source not in {
            "native_readback",
            "native_schema",
            "adapter_boundary",
            "generated_wrapper",
        }:
            raise ValueError("Configuration observation source is invalid")
        if not isinstance(self.required, bool):
            raise TypeError("Configuration evidence required flag must be boolean")
        if not isinstance(self.planned_present, bool) or not isinstance(self.observed_present, bool):
            raise TypeError("Configuration evidence presence flags must be boolean")
        if not isinstance(self.planned_redacted, bool) or not isinstance(self.observed_redacted, bool):
            raise TypeError("Configuration evidence redaction flags must be boolean")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValueError("Configuration evidence reason must be a non-empty string")
        planned = freeze_json(self.planned_value)
        observed = freeze_json(self.observed_value)
        object.__setattr__(self, "planned_value", planned)
        object.__setattr__(self, "observed_value", observed)
        if self.planned_digest is None:
            object.__setattr__(self, "planned_digest", _configuration_digest(self.planned_present, planned))
        elif not _valid_digest(self.planned_digest):
            raise ValueError("Planned configuration digest is invalid")
        elif not self.planned_redacted and self.planned_digest != _configuration_digest(self.planned_present, planned):
            raise ValueError("Planned configuration digest is inconsistent")
        if self.observed_digest is None:
            object.__setattr__(self, "observed_digest", _configuration_digest(self.observed_present, observed))
        elif not _valid_digest(self.observed_digest):
            raise ValueError("Observed configuration digest is invalid")
        elif not self.observed_redacted and self.observed_digest != _configuration_digest(
            self.observed_present, observed
        ):
            raise ValueError("Observed configuration digest is inconsistent")
        if self.status == "passed" and (
            self.planned_present != self.observed_present or self.planned_digest != self.observed_digest
        ):
            raise ValueError("Passed configuration evidence does not match")
        if self.status == "violated" and (
            self.planned_present == self.observed_present and self.planned_digest == self.observed_digest
        ):
            raise ValueError("Violated configuration evidence matches")

    @property
    def matches(self) -> bool:
        return self.planned_present == self.observed_present and self.planned_digest == self.observed_digest

    def to_dict(self) -> dict[str, object]:
        return {
            "observed_digest": self.observed_digest,
            "observed_present": self.observed_present,
            "observed_value": _thaw(cast(FrozenJsonValue, self.observed_value)),
            "observed_redacted": self.observed_redacted,
            "observation_source": self.observation_source,
            "planned_digest": self.planned_digest,
            "planned_present": self.planned_present,
            "planned_value": _thaw(cast(FrozenJsonValue, self.planned_value)),
            "planned_redacted": self.planned_redacted,
            "property_path": self.property_path,
            "reason": self.reason,
            "required": self.required,
            "semantic_id": str(self.semantic_id),
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: object) -> ConfigurationConformanceEvidence:
        if not isinstance(value, dict):
            raise TypeError("Configuration conformance evidence must be an object")
        required = {
            "observed_digest",
            "observed_present",
            "observed_value",
            "observed_redacted",
            "observation_source",
            "planned_digest",
            "planned_present",
            "planned_value",
            "planned_redacted",
            "property_path",
            "reason",
            "required",
            "semantic_id",
            "status",
        }
        if set(value) != required:
            raise ValueError("Configuration conformance evidence has unexpected keys")
        if not isinstance(value["observed_present"], bool) or not isinstance(value["planned_present"], bool):
            raise TypeError("Configuration evidence presence flags must be boolean")
        if not isinstance(value["required"], bool):
            raise TypeError("Configuration evidence required flag must be boolean")
        if value["reason"] is not None and not isinstance(value["reason"], str):
            raise TypeError("Configuration evidence reason must be a string or null")
        evidence = cls(
            semantic_id=SemanticId.parse(_required_string(value, "semantic_id")),
            property_path=_required_string(value, "property_path"),
            planned_value=value["planned_value"],
            observed_value=value["observed_value"],
            status=cast(ConfigurationStatus, _required_string(value, "status")),
            observation_source=cast(
                ConfigurationObservationSource,
                _required_string(value, "observation_source"),
            ),
            reason=value["reason"],
            required=value["required"],
            planned_present=value["planned_present"],
            observed_present=value["observed_present"],
            planned_digest=cast(str | None, value["planned_digest"]),
            observed_digest=cast(str | None, value["observed_digest"]),
            planned_redacted=cast(bool, value["planned_redacted"]),
            observed_redacted=cast(bool, value["observed_redacted"]),
        )
        if evidence.to_dict() != value:
            raise ValueError("Configuration conformance evidence is inconsistent")
        return evidence

    @classmethod
    def from_json(cls, source: str) -> ConfigurationConformanceEvidence:
        try:
            value: object = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid configuration conformance evidence JSON: {exc}") from exc
        return cls.from_dict(value)


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
    configuration_conformance: tuple[ConfigurationConformanceEvidence, ...] = ()

    def __post_init__(self) -> None:
        keys = [(item.semantic_id, item.property_path) for item in self.configuration_conformance]
        if len(keys) != len(set(keys)):
            raise ValueError("Configuration conformance evidence has duplicate property records")
        inventory = set(self.agent_ids) | set(self.grant_ids) | set(self.composition_ids)
        if any(item.semantic_id not in inventory for item in self.configuration_conformance):
            raise ValueError("Configuration conformance evidence references an unknown semantic ID")
        allowed = {
            "agent.name",
            "agent.identity",
            "agent.model",
            "agent.model_options",
            "agent.output_type",
            "agent.output_mode",
            "agent.tools",
            "agent.handoffs",
            "agent.approval_required",
            "agent.retry_strategy",
            "agent.session_manager",
            "grant.identity",
            "grant.approval",
            "edge.identity",
            "edge.schema",
        }
        if any(
            item.property_path not in allowed
            and not item.property_path.startswith("agent.model_settings.")
            and not item.property_path.startswith("agent.model_options.")
            for item in self.configuration_conformance
        ):
            raise ValueError("Configuration conformance evidence has an unexpected property path")

    @property
    def complete(self) -> bool:
        if not self.schema_conformance or not all(item.matches for item in self.schema_conformance):
            return False
        required = (
            {(identifier, "agent.name") for identifier in self.agent_ids}
            | {(identifier, "agent.identity") for identifier in self.agent_ids}
            | {(identifier, "agent.model") for identifier in self.agent_ids}
            | {(identifier, "agent.model_options") for identifier in self.agent_ids}
            | {(identifier, "agent.output_type") for identifier in self.agent_ids}
            | {(identifier, "agent.output_mode") for identifier in self.agent_ids}
            | {(identifier, "agent.tools") for identifier in self.agent_ids}
            | {(identifier, "agent.handoffs") for identifier in self.agent_ids}
            | {(identifier, "grant.identity") for identifier in self.grant_ids}
            | {(identifier, "grant.approval") for identifier in self.grant_ids}
            | {(identifier, "edge.identity") for identifier in self.composition_ids}
            | {(identifier, "edge.schema") for identifier in self.composition_ids}
        )
        records = {(item.semantic_id, item.property_path): item for item in self.configuration_conformance}
        if not required.issubset(records):
            return False
        if any(records[key].status != "passed" for key in required):
            return False
        if any(item.required and item.status != "passed" for item in self.configuration_conformance):
            return False
        return True

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
            "configuration_conformance": [item.to_dict() for item in self.configuration_conformance],
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
            "configuration_conformance",
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
                SchemaConformanceEvidence.from_dict(item) for item in _required_list(value, "schema_conformance")
            ),
            configuration_conformance=tuple(
                ConfigurationConformanceEvidence.from_dict(item)
                for item in _required_list(value, "configuration_conformance")
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
    input_types: FrozenMap[SemanticId, type[object] | None]
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
    artifacts: CompilerArtifacts

    def __post_init__(self) -> None:
        """Keep the returned compilation, plan, and graph evidence joined."""

        if self.graph.context.ir is not self.artifacts.ir:
            raise ValueError("Materialization graph context must use the returned compiler IR")
        if set(self.graph.input_types) != set(self.plan.agents):
            raise ValueError("Materialization graph input types must cover the planned agents")
        if any(
            self.plan.agents[identifier].parameters != self.artifacts.ir.agents[identifier].parameters
            for identifier in self.plan.agents
        ):
            raise ValueError("Materialization plan inputs must use the returned compiler IR")
        if self.plan.contract_digest != self.artifacts.contract_digest:
            raise ValueError("Materialization plan must use the returned compiler contract digest")
        if self.plan.artifact_digests != artifact_digests(self.artifacts):
            raise ValueError("Materialization plan must use the returned compiler artifact digests")
        if self.graph.validation.contract_digest != self.artifacts.contract_digest:
            raise ValueError("Graph validation must use the returned compiler contract digest")
        if self.graph.validation.plan_digest != self.plan.plan_digest:
            raise ValueError("Graph validation must use the returned materialization plan digest")

    @property
    def agents(self) -> FrozenMap[str, object]:
        """Return native agents by the contract names users wrote."""

        return FrozenMap((identifier.parts[0], native_agent) for identifier, native_agent in self.graph.agents.items())

    @property
    def context(self) -> ContextRuntime:
        """Return the typed context resolver wired into the native graph."""

        return self.graph.context

    @property
    def agent_input_types(self) -> FrozenMap[str, type[object] | None]:
        """Return strict invocation-input types by contract agent name."""

        return FrozenMap(
            (identifier.parts[0], input_type)
            for identifier, input_type in self.graph.input_types.items()
        )

    def validate_agent_input(
        self,
        agent_name: str,
        value: Mapping[str, object],
    ) -> object | None:
        """Validate one root-agent invocation against its contract signature."""

        input_type = self._agent_input_type(agent_name)
        if not isinstance(value, Mapping):
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT206",
                        f"Input for agent `{agent_name}` must be an object",
                    ),
                )
            )
        if input_type is None:
            if value:
                raise MaterializationError(
                    (
                        MaterializationIssue(
                            "MAT206",
                            f"Agent `{agent_name}` does not declare invocation inputs",
                        ),
                    )
                )
            return None
        try:
            return cast(object, cast(Any, input_type).model_validate(value))
        except ValidationError as exc:
            raise MaterializationError(
                (
                    MaterializationIssue(
                        "MAT206",
                        f"Input for agent `{agent_name}` does not satisfy its contract: {exc}",
                    ),
                )
            ) from exc

    def serialize_agent_input(
        self,
        agent_name: str,
        value: Mapping[str, object],
    ) -> str:
        """Validate and serialize one root-agent invocation for an SDK runner."""

        validated = self.validate_agent_input(agent_name, value)
        if validated is None:
            return "{}"
        return cast(str, cast(Any, validated).model_dump_json())

    @property
    def structural_output_types(self) -> FrozenMap[str, type[object]]:
        """Return generated contract types that exclude application domain validators."""

        return self.graph.output_types

    def _agent_input_type(self, agent_name: str) -> type[object] | None:
        for identifier, input_type in self.graph.input_types.items():
            if identifier.parts[0] == agent_name:
                return input_type
        raise MaterializationError(
            (MaterializationIssue("MAT205", f"Unknown materialized agent `{agent_name}`"),)
        )


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
        input_types: FrozenMap[SemanticId, type[object] | None],
        output_types: FrozenMap[str, type[object]],
        context_runtime: ContextRuntime,
        environment: EnvironmentProvider | None,
        materialization_trace_sink: MaterializationTraceSink,
    ) -> NativeAgentGraph:
        """Construct and validate the complete native graph."""


__all__ = [
    "ConfigurationConformanceEvidence",
    "ConfigurationObservationSource",
    "ConfigurationStatus",
    "GraphValidationEvidence",
    "MaterializationProvider",
    "MaterializationResult",
    "NativeAgentGraph",
    "SchemaConformanceEvidence",
]


def _schema_digest(value: FrozenJsonValue) -> str:
    source = json.dumps(_thaw(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"


def _configuration_digest(present: bool, value: FrozenJsonValue) -> str:
    return _schema_digest(FrozenMap((("present", present), ("value", value))))


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


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
