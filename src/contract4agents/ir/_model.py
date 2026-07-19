"""Immutable provider-neutral Contract4Agents intermediate representation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal, Protocol, TypeVar

from contract4agents.ir._collections import FrozenJsonValue, FrozenMap, freeze_json
from contract4agents.ir._ids import SemanticId, SemanticKind
from contract4agents.ir._type_refs import TypeRef

IR_VERSION = "3"

Audience = Literal["model", "adapter", "host", "evaluator", "reviewer"]
CapabilityKind = Literal["tool", "datasource"]
Availability = Literal["enabled", "denied"]
Authorization = Literal["preapproved", "approval_required"]
ExecutionBoundary = Literal["host", "provider_hosted", "remote"] | str
ContextOrigin = Literal["invocation", "parent", "handoff", "stage", "datasource", "external"]
CompositionMode = Literal["delegate", "handoff"]
HistoryMode = Literal["none", "summary", "full"]
AssessmentMode = Literal["static", "adapter", "runtime", "host_attested", "post_run", "semantic", "advisory"]
Severity = Literal["low", "medium", "high", "critical"]
IsolationContext = Literal["explicit_only", "inherited"]
IsolationCapabilities = Literal["declared_only", "inherited"]
IsolationState = Literal["fresh", "shared"]
IsolationFilesystem = Literal["none", "ephemeral", "inherited_read_only", "inherited"]
IsolationNetwork = Literal["denied", "allowlisted", "inherited"]
IsolationSecrets = Literal["none", "declared_only", "inherited"]
IsolationReturn = Literal["final_output_only", "full_trace"]
StageCardinality = Literal["one", "optional", "many"]


@dataclass(frozen=True)
class SourceSpan:
    """A repository-relative diagnostic location excluded from semantic identity."""

    path: str
    line: int
    column: int = 1
    end_line: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        normalized = self.path.replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if not normalized or parsed.is_absolute() or ".." in parsed.parts:
            raise ValueError("IR source paths must be repository-relative")
        if str(parsed) != normalized or normalized == ".":
            raise ValueError("IR source paths must use normalized POSIX spelling")
        if self.line < 1 or self.column < 1:
            raise ValueError("IR source-span lines and columns are one-based")
        if self.end_line is not None and self.end_line < self.line:
            raise ValueError("IR source-span end line cannot precede its start")
        if self.end_column is not None and self.end_column < 1:
            raise ValueError("IR source-span end column must be positive")
        object.__setattr__(self, "path", normalized)


@dataclass(frozen=True)
class TypeFieldIR:
    name: str
    type_ref: TypeRef
    has_default: bool = False
    default: FrozenJsonValue = None
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _require_name(self.name, "type field")
        if not self.has_default and self.default is not None:
            raise ValueError(f"Field `{self.name}` has a default value but `has_default` is false")
        object.__setattr__(self, "default", freeze_json(self.default))


@dataclass(frozen=True)
class ParameterIR:
    name: str
    type_ref: TypeRef
    required: bool = True
    has_default: bool = False
    default: FrozenJsonValue = None
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _require_name(self.name, "parameter")
        if self.required and self.has_default:
            raise ValueError(f"Required parameter `{self.name}` cannot declare a default")
        if not self.has_default and self.default is not None:
            raise ValueError(f"Parameter `{self.name}` has a default value but `has_default` is false")
        object.__setattr__(self, "default", freeze_json(self.default))


@dataclass(frozen=True)
class TypeIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    fields: tuple[TypeFieldIR, ...]
    description: str = ""
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _validate_identity(self.id, "type", self.name)
        _unique_names((item.name for item in self.fields), f"type `{self.name}` fields")


@dataclass(frozen=True)
class EnumIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    values: tuple[str, ...]
    description: str = ""
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _validate_identity(self.id, "type", self.name)
        if not self.values:
            raise ValueError(f"Enum `{self.name}` must declare at least one value")
        if any(not value for value in self.values):
            raise ValueError(f"Enum `{self.name}` values cannot be empty")
        _unique_names(self.values, f"enum `{self.name}` values")


TypeDeclarationIR = TypeIR | EnumIR


@dataclass(frozen=True)
class CapabilityIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    kind: CapabilityKind
    parameters: tuple[ParameterIR, ...]
    output_type: TypeRef
    description: str
    side_effect: bool | None = None
    render: str | None = None
    cache: str | None = None
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _validate_identity(self.id, self.kind, self.name)
        _unique_names((item.name for item in self.parameters), f"capability `{self.name}` parameters")
        if self.kind == "tool":
            if self.side_effect is None:
                raise ValueError(f"Tool `{self.name}` must declare side_effect")
            if self.render is not None or self.cache is not None:
                raise ValueError(f"Tool `{self.name}` cannot declare datasource render/cache settings")
        elif self.side_effect is not None:
            raise ValueError(f"Datasource `{self.name}` cannot declare tool side-effect metadata")


@dataclass(frozen=True)
class ExternalContextIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    output_type: TypeRef
    description: str
    sensitivity: str
    render: str
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _validate_identity(self.id, "external", self.name)


@dataclass(frozen=True)
class ContextRequirementIR:
    id: SemanticId = field(metadata={"canonical": False})
    agent_id: SemanticId
    name: str
    type_ref: TypeRef
    origin: ContextOrigin
    origin_id: SemanticId | None = None
    input_mappings: FrozenMap[str, str] = field(default_factory=FrozenMap)
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        self.id.require_kind("context")
        self.agent_id.require_kind("agent")
        _require_name(self.name, "context requirement")
        if self.origin in {"datasource", "external"} and self.origin_id is None:
            raise ValueError(f"Context `{self.name}` requires an origin semantic ID")
        if self.origin_id is not None:
            if self.origin == "datasource":
                expected: tuple[SemanticKind, ...] = ("datasource",)
            elif self.origin == "external":
                expected = ("external",)
            elif self.origin == "handoff":
                expected = ("edge",)
            elif self.origin == "stage":
                expected = ("run_spec",)
            else:
                expected = ("agent", "edge", "run_spec")
            self.origin_id.require_kind(*expected)
        for parameter, expression in self.input_mappings.items():
            _require_name(parameter, "context input mapping")
            if not expression:
                raise ValueError(f"Context mapping `{parameter}` cannot be empty")


@dataclass(frozen=True)
class GuidanceIR:
    text: str
    audience: tuple[Audience, ...] = ("model", "reviewer")

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("Guidance text cannot be empty")
        _unique_names(self.audience, "guidance audiences")


@dataclass(frozen=True)
class AgentIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    parameters: tuple[ParameterIR, ...]
    output_type: TypeRef
    goal: str
    description: str = ""
    guidance: tuple[GuidanceIR, ...] = ()
    grant_ids: tuple[SemanticId, ...] = ()
    context_ids: tuple[SemanticId, ...] = ()
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _validate_identity(self.id, "agent", self.name)
        _unique_names((item.name for item in self.parameters), f"agent `{self.name}` parameters")
        _require_id_kinds(self.grant_ids, "grant")
        _require_id_kinds(self.context_ids, "context")


@dataclass(frozen=True)
class GrantIR:
    id: SemanticId = field(metadata={"canonical": False})
    agent_id: SemanticId
    capability_id: SemanticId
    availability: Availability
    authorization: Authorization | None = None
    execution: ExecutionBoundary | None = None
    isolation_id: SemanticId | None = None
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        self.id.require_kind("grant")
        self.agent_id.require_kind("agent")
        self.capability_id.require_kind("tool", "datasource")
        if self.availability == "denied" and (self.authorization is not None or self.execution is not None):
            raise ValueError("A denied grant cannot declare authorization or execution")
        if self.availability == "enabled" and self.capability_id.kind == "tool" and self.authorization is None:
            raise ValueError("An enabled tool grant must declare authorization")
        if self.isolation_id is not None:
            self.isolation_id.require_kind("isolation")


@dataclass(frozen=True)
class CompositionEdgeIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    source_agent_id: SemanticId
    target_agent_id: SemanticId
    mode: CompositionMode
    description: str
    history: HistoryMode
    input_mappings: FrozenMap[str, str] = field(default_factory=FrozenMap)
    isolation_id: SemanticId | None = None
    audience: tuple[Audience, ...] = ("model", "adapter", "host", "reviewer")
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _validate_identity(self.id, "edge", self.name)
        self.source_agent_id.require_kind("agent")
        self.target_agent_id.require_kind("agent")
        if self.isolation_id is not None:
            self.isolation_id.require_kind("isolation")
        _unique_names(self.audience, "composition audiences")
        for parameter, expression in self.input_mappings.items():
            _require_name(parameter, "composition input")
            if not expression:
                raise ValueError(f"Composition input mapping `{parameter}` cannot be empty")


@dataclass(frozen=True)
class IsolationProfileIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    context: IsolationContext | None = None
    capabilities: IsolationCapabilities | None = None
    state: IsolationState | None = None
    filesystem: IsolationFilesystem | None = None
    network: IsolationNetwork | None = None
    secrets: IsolationSecrets | None = None
    return_channel: IsolationReturn | None = None
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _validate_identity(self.id, "isolation", self.name)


@dataclass(frozen=True)
class ControlIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    agent_id: SemanticId
    severity: Severity
    required: bool
    audience: tuple[Audience, ...]
    assessment: AssessmentMode
    condition: str | None = None
    requirement: str | None = None
    derived_from: SemanticId | None = None
    expected_evidence: tuple[str, ...] = ()
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        self.id.require_kind("control")
        self.agent_id.require_kind("agent")
        _require_name(self.name, "control")
        _unique_names(self.audience, "control audiences")
        if self.requirement is None and self.derived_from is None:
            raise ValueError(f"Control `{self.name}` needs a requirement or derived source")


@dataclass(frozen=True)
class QualityIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    agent_id: SemanticId
    rubric: str
    audience: tuple[Audience, ...] = ("evaluator", "reviewer")
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        self.id.require_kind("quality")
        self.agent_id.require_kind("agent")
        _require_name(self.name, "quality rubric")
        _unique_names(self.audience, "quality audiences")


@dataclass(frozen=True)
class OperationalControlIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    agent_id: SemanticId
    severity: Severity
    requirement: str
    window: str | None = None
    audience: tuple[Audience, ...] = ("evaluator", "reviewer")
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        self.id.require_kind("operational")
        self.agent_id.require_kind("agent")
        _require_name(self.name, "operational control")
        _unique_names(self.audience, "operational-control audiences")


@dataclass(frozen=True)
class EvalIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    agent_id: SemanticId
    givens: FrozenMap[str, FrozenJsonValue] = field(default_factory=FrozenMap)
    expectations: tuple[str, ...] = ()
    quality_ids: tuple[SemanticId, ...] = ()
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        self.id.require_kind("eval")
        self.agent_id.require_kind("agent")
        _require_name(self.name, "eval")
        _require_id_kinds(self.quality_ids, "quality")
        for name, value in self.givens.items():
            _require_name(name, "eval given")
            freeze_json(value)


@dataclass(frozen=True)
class RunSpecStageIR:
    name: str
    agent_id: SemanticId
    output_type: TypeRef
    cardinality: StageCardinality = "one"

    def __post_init__(self) -> None:
        _require_name(self.name, "run-spec stage")
        self.agent_id.require_kind("agent")


@dataclass(frozen=True)
class RunSpecDerivedValueIR:
    name: str
    type_name: str

    def __post_init__(self) -> None:
        _require_name(self.name, "run-spec derived value")
        if not self.type_name:
            raise ValueError("Run-spec derived-value type cannot be empty")


@dataclass(frozen=True)
class RunSpecIR:
    id: SemanticId = field(metadata={"canonical": False})
    name: str
    stages: tuple[RunSpecStageIR, ...]
    derived_values: tuple[RunSpecDerivedValueIR, ...] = ()
    assertions: tuple[str, ...] = ()
    span: SourceSpan | None = field(default=None, metadata={"canonical": False})

    def __post_init__(self) -> None:
        _validate_identity(self.id, "run_spec", self.name)
        _unique_names((stage.name for stage in self.stages), f"run spec `{self.name}` stages")
        _unique_names(
            (value.name for value in self.derived_values),
            f"run spec `{self.name}` derived values",
        )


class _Entity(Protocol):
    @property
    def id(self) -> SemanticId: ...


EntityT = TypeVar("EntityT", bound=_Entity)


@dataclass(frozen=True)
class CanonicalIR:
    """The complete immutable semantic graph before target planning."""

    ir_version: str = field(default=IR_VERSION, init=False)
    types: FrozenMap[SemanticId, TypeDeclarationIR] = field(default_factory=FrozenMap)
    capabilities: FrozenMap[SemanticId, CapabilityIR] = field(default_factory=FrozenMap)
    external_contexts: FrozenMap[SemanticId, ExternalContextIR] = field(default_factory=FrozenMap)
    contexts: FrozenMap[SemanticId, ContextRequirementIR] = field(default_factory=FrozenMap)
    agents: FrozenMap[SemanticId, AgentIR] = field(default_factory=FrozenMap)
    grants: FrozenMap[SemanticId, GrantIR] = field(default_factory=FrozenMap)
    composition: FrozenMap[SemanticId, CompositionEdgeIR] = field(default_factory=FrozenMap)
    controls: FrozenMap[SemanticId, ControlIR] = field(default_factory=FrozenMap)
    qualities: FrozenMap[SemanticId, QualityIR] = field(default_factory=FrozenMap)
    operational_controls: FrozenMap[SemanticId, OperationalControlIR] = field(default_factory=FrozenMap)
    isolation_profiles: FrozenMap[SemanticId, IsolationProfileIR] = field(default_factory=FrozenMap)
    evals: FrozenMap[SemanticId, EvalIR] = field(default_factory=FrozenMap)
    run_specs: FrozenMap[SemanticId, RunSpecIR] = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        _validate_entity_map(self.types, "type")
        _validate_entity_map(self.capabilities, "tool", "datasource")
        _validate_entity_map(self.external_contexts, "external")
        _validate_entity_map(self.contexts, "context")
        _validate_entity_map(self.agents, "agent")
        _validate_entity_map(self.grants, "grant")
        _validate_entity_map(self.composition, "edge")
        _validate_entity_map(self.controls, "control")
        _validate_entity_map(self.qualities, "quality")
        _validate_entity_map(self.operational_controls, "operational")
        _validate_entity_map(self.isolation_profiles, "isolation")
        _validate_entity_map(self.evals, "eval")
        _validate_entity_map(self.run_specs, "run_spec")

    @classmethod
    def create(
        cls,
        *,
        types: Iterable[TypeDeclarationIR] = (),
        capabilities: Iterable[CapabilityIR] = (),
        external_contexts: Iterable[ExternalContextIR] = (),
        contexts: Iterable[ContextRequirementIR] = (),
        agents: Iterable[AgentIR] = (),
        grants: Iterable[GrantIR] = (),
        composition: Iterable[CompositionEdgeIR] = (),
        controls: Iterable[ControlIR] = (),
        qualities: Iterable[QualityIR] = (),
        operational_controls: Iterable[OperationalControlIR] = (),
        isolation_profiles: Iterable[IsolationProfileIR] = (),
        evals: Iterable[EvalIR] = (),
        run_specs: Iterable[RunSpecIR] = (),
    ) -> CanonicalIR:
        """Build entity maps keyed by their stable semantic IDs."""

        return cls(
            types=_entity_map(types),
            capabilities=_entity_map(capabilities),
            external_contexts=_entity_map(external_contexts),
            contexts=_entity_map(contexts),
            agents=_entity_map(agents),
            grants=_entity_map(grants),
            composition=_entity_map(composition),
            controls=_entity_map(controls),
            qualities=_entity_map(qualities),
            operational_controls=_entity_map(operational_controls),
            isolation_profiles=_entity_map(isolation_profiles),
            evals=_entity_map(evals),
            run_specs=_entity_map(run_specs),
        )


def _entity_map(values: Iterable[EntityT]) -> FrozenMap[SemanticId, EntityT]:
    return FrozenMap((value.id, value) for value in values)


def _validate_entity_map(values: FrozenMap[SemanticId, EntityT], *kinds: SemanticKind) -> None:
    for key, value in values.items():
        key.require_kind(*kinds)
        if key != value.id:
            raise ValueError(f"IR map key `{key}` does not match entity ID `{value.id}`")


def _validate_identity(identifier: SemanticId, kind: SemanticKind, name: str) -> None:
    identifier.require_kind(kind)
    _require_name(name, kind)
    if identifier.parts != (name,):
        raise ValueError(f"Semantic ID `{identifier}` does not match {kind} name `{name}`")


def _require_name(name: str, label: str) -> None:
    if not name or name != name.strip() or ":" in name:
        raise ValueError(f"Invalid {label} name `{name}`")


def _unique_names(names: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ValueError(f"Duplicate {label} entry `{name}`")
        seen.add(name)


def _require_id_kinds(values: Iterable[SemanticId], *kinds: SemanticKind) -> None:
    seen: set[SemanticId] = set()
    for value in values:
        value.require_kind(*kinds)
        if value in seen:
            raise ValueError(f"Duplicate semantic reference `{value}`")
        seen.add(value)


__all__ = [
    "AgentIR",
    "AssessmentMode",
    "Audience",
    "Availability",
    "Authorization",
    "CanonicalIR",
    "CapabilityIR",
    "CapabilityKind",
    "CompositionEdgeIR",
    "CompositionMode",
    "ContextOrigin",
    "ContextRequirementIR",
    "ControlIR",
    "EvalIR",
    "EnumIR",
    "ExecutionBoundary",
    "ExternalContextIR",
    "GrantIR",
    "GuidanceIR",
    "HistoryMode",
    "IR_VERSION",
    "IsolationProfileIR",
    "OperationalControlIR",
    "ParameterIR",
    "QualityIR",
    "RunSpecIR",
    "RunSpecDerivedValueIR",
    "RunSpecStageIR",
    "Severity",
    "SourceSpan",
    "StageCardinality",
    "TypeFieldIR",
    "TypeDeclarationIR",
    "TypeIR",
]
