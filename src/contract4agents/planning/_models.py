"""Deeply immutable provider-neutral materialization plan models."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from contract4agents.ir import FrozenJsonValue, FrozenMap, SemanticId, TypeRef, freeze_json

PLAN_VERSION = "3"
MappingOutcome = Literal["exact", "host_enforced", "emulated", "degraded", "unsupported"]
BindingKind = Literal["tool", "datasource", "external"]
IsolationDimension = Literal["context", "capabilities", "state", "filesystem", "network", "secrets", "return"]


@dataclass(frozen=True)
class MappingSupport:
    outcome: MappingOutcome
    mechanism: str | None
    expected_event_types: tuple[str, ...] = ()
    host_obligation: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {"degraded", "unsupported"} and not self.mechanism:
            raise ValueError(f"Mapping outcome `{self.outcome}` requires a mechanism")
        if self.outcome == "unsupported" and self.mechanism is not None:
            raise ValueError("An unsupported mapping cannot claim a mechanism")
        _unique(self.expected_event_types, "expected event type")


@dataclass(frozen=True)
class PlannerCapabilities:
    """Adapter capability facts supplied to the generic planner."""

    adapter: str
    version: str
    approval: MappingSupport = field(default_factory=lambda: MappingSupport("unsupported", None))
    composition: FrozenMap[str, MappingSupport] = field(default_factory=FrozenMap)
    controls: FrozenMap[str, MappingSupport] = field(default_factory=FrozenMap)
    isolation: FrozenMap[str, MappingSupport] = field(default_factory=FrozenMap)
    expected_event_types: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        adapter: str,
        version: str,
        approval: MappingSupport | None = None,
        composition: Mapping[str, MappingSupport] | Iterable[tuple[str, MappingSupport]] = (),
        controls: Mapping[str, MappingSupport] | Iterable[tuple[str, MappingSupport]] = (),
        isolation: Mapping[str, MappingSupport] | Iterable[tuple[str, MappingSupport]] = (),
        expected_event_types: Iterable[str] = (),
    ) -> PlannerCapabilities:
        return cls(
            adapter=adapter,
            version=version,
            approval=approval or MappingSupport("unsupported", None),
            composition=FrozenMap(composition),
            controls=FrozenMap(controls),
            isolation=FrozenMap(isolation),
            expected_event_types=tuple(expected_event_types),
        )


def in_process_isolation_support() -> FrozenMap[str, MappingSupport]:
    """Honest baseline for an in-process environment with no OS security boundary."""

    return FrozenMap(
        {
            "context:explicit_only": MappingSupport("emulated", "in_process.fresh_context"),
            "capabilities:declared_only": MappingSupport("emulated", "in_process.capability_allowlist"),
            "state:fresh": MappingSupport("emulated", "in_process.fresh_session"),
            "return:final_output_only": MappingSupport("emulated", "in_process.final_output_filter"),
            "filesystem:none": MappingSupport("unsupported", None),
            "filesystem:ephemeral": MappingSupport("unsupported", None),
            "filesystem:inherited_read_only": MappingSupport("unsupported", None),
            "network:denied": MappingSupport("unsupported", None),
            "network:allowlisted": MappingSupport("unsupported", None),
            "secrets:none": MappingSupport("unsupported", None),
            "secrets:declared_only": MappingSupport("unsupported", None),
        }
    )


@dataclass(frozen=True)
class AdapterPlan:
    name: str
    version: str


@dataclass(frozen=True)
class AgentPlan:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    model: str
    model_options: FrozenMap[str, FrozenJsonValue]
    output_type: TypeRef


@dataclass(frozen=True)
class BindingPlan:
    id: SemanticId = field(metadata={"canonical": False})
    kind: BindingKind
    locator: FrozenMap[str, FrozenJsonValue]
    outcome: MappingOutcome
    mechanism: str
    execution: str


@dataclass(frozen=True)
class GrantMappingPlan:
    id: SemanticId = field(metadata={"canonical": False})
    agent_id: SemanticId
    capability_id: SemanticId
    availability: str
    authorization: str | None
    execution: str | None
    isolation_id: SemanticId | None
    outcome: MappingOutcome
    mechanism: str | None


@dataclass(frozen=True)
class CompositionMappingPlan:
    id: SemanticId = field(metadata={"canonical": False})
    source_agent_id: SemanticId
    target_agent_id: SemanticId
    mode: str
    description: str
    history: str
    input_mappings: FrozenMap[str, str]
    audience: tuple[str, ...]
    isolation_id: SemanticId | None
    outcome: MappingOutcome
    mechanism: str | None


@dataclass(frozen=True)
class ControlMappingPlan:
    id: SemanticId = field(metadata={"canonical": False})
    required: bool
    assessment: str
    outcome: MappingOutcome
    mechanism: str | None
    expected_evidence: tuple[str, ...]


@dataclass(frozen=True)
class IsolationDimensionPlan:
    requested: str
    outcome: MappingOutcome
    mechanism: str | None


@dataclass(frozen=True)
class IsolationMappingPlan:
    id: SemanticId = field(metadata={"canonical": False})
    environment: str
    provider: str
    dimensions: FrozenMap[str, IsolationDimensionPlan]


@dataclass(frozen=True)
class HostObligationPlan:
    code: str
    description: str
    semantic_id: SemanticId | None = None


@dataclass(frozen=True)
class MaterializationPlan:
    """A complete, native-object-free description of target materialization."""

    contract_digest: str
    target: str
    profile: str
    adapter: AdapterPlan
    agents: FrozenMap[SemanticId, AgentPlan]
    bindings: FrozenMap[SemanticId, BindingPlan]
    grants: FrozenMap[SemanticId, GrantMappingPlan]
    composition: FrozenMap[SemanticId, CompositionMappingPlan]
    controls: FrozenMap[SemanticId, ControlMappingPlan]
    isolation: FrozenMap[SemanticId, IsolationMappingPlan]
    artifact_digests: FrozenMap[str, str]
    host_obligations: tuple[HostObligationPlan, ...]
    expected_event_types: tuple[str, ...]
    plan_version: str = field(default=PLAN_VERSION, init=False)

    @property
    def plan_digest(self) -> str:
        from contract4agents.planning._serialization import compute_plan_digest

        return compute_plan_digest(self)


def frozen_json_mapping(values: Mapping[str, object]) -> FrozenMap[str, FrozenJsonValue]:
    frozen = freeze_json(values)
    if not isinstance(frozen, FrozenMap):
        raise TypeError("Expected a JSON object")
    return frozen


def _unique(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"Duplicate {label} entry `{value}`")
        seen.add(value)


__all__ = [
    "PLAN_VERSION",
    "AdapterPlan",
    "AgentPlan",
    "BindingKind",
    "BindingPlan",
    "CompositionMappingPlan",
    "ControlMappingPlan",
    "GrantMappingPlan",
    "HostObligationPlan",
    "IsolationDimension",
    "IsolationDimensionPlan",
    "IsolationMappingPlan",
    "MappingOutcome",
    "MappingSupport",
    "MaterializationPlan",
    "PlannerCapabilities",
    "frozen_json_mapping",
    "in_process_isolation_support",
]
