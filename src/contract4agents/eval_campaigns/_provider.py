"""Provider boundary for deterministic and live eval campaign execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from contract4agents.eval_campaigns._models import EvalInventory, TrialMetrics
from contract4agents.ir import EvalIR, FrozenJsonValue, FrozenMap, SemanticId, freeze_json
from contract4agents.tracing import NormalizedTrace, TraceClosureEvidence


class EvalProviderError(RuntimeError):
    """An eval provider could not resolve or execute a deterministic trial."""


@dataclass(frozen=True)
class EvalExecutionRequest:
    case: EvalIR
    trial_id: str
    trial_index: int
    inputs: Mapping[str, object]
    contract_digest: str
    plan_digest: str
    inventory: EvalInventory

    def __post_init__(self) -> None:
        frozen = freeze_json(self.inputs)
        if not isinstance(frozen, FrozenMap):
            raise TypeError("Eval execution inputs must be a JSON object")
        object.__setattr__(self, "inputs", frozen)


@dataclass(frozen=True)
class EvalExecution:
    output: Mapping[str, object]
    trace: NormalizedTrace
    trace_closure: TraceClosureEvidence
    metrics: TrialMetrics = field(default_factory=TrialMetrics)

    def __post_init__(self) -> None:
        frozen = freeze_json(self.output)
        if not isinstance(frozen, FrozenMap):
            raise TypeError("Eval execution output must be a JSON object")
        object.__setattr__(self, "output", frozen)


@dataclass(frozen=True)
class ApprovalRequest:
    case_id: SemanticId
    trial_id: str
    capability_id: SemanticId
    arguments: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.case_id.require_kind("eval")
        self.capability_id.require_kind("tool", "datasource")
        object.__setattr__(self, "arguments", _frozen_object("Approval arguments", self.arguments))


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text("Approval reason", self.reason)
        object.__setattr__(self, "evidence_refs", _references(self.evidence_refs))


@dataclass(frozen=True)
class JudgeRequest:
    case_id: SemanticId
    trial_id: str
    quality_id: SemanticId
    rubric: str
    output: Mapping[str, object]
    trace: NormalizedTrace

    def __post_init__(self) -> None:
        self.case_id.require_kind("eval")
        self.quality_id.require_kind("quality")
        _require_text("Judge rubric", self.rubric)
        object.__setattr__(self, "output", _frozen_object("Judge output", self.output))


@dataclass(frozen=True)
class JudgeDecision:
    passed: bool
    reason: str
    score: float | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    provider: str = "unknown"
    version: str = "unknown"

    def __post_init__(self) -> None:
        _require_text("Judge reason", self.reason)
        _require_text("Judge provider", self.provider)
        _require_text("Judge version", self.version)
        if self.score is not None and not 0 <= self.score <= 1:
            raise ValueError("Judge score must be between zero and one")
        object.__setattr__(self, "evidence_refs", _references(self.evidence_refs))


class EvalProvider(Protocol):
    """Everything target-specific needed to run a portable eval campaign."""

    async def resolve_inputs(self, case: EvalIR, *, trial_index: int) -> Mapping[str, object]: ...

    async def execute(self, request: EvalExecutionRequest) -> EvalExecution: ...

    async def approve(self, request: ApprovalRequest) -> ApprovalDecision | None: ...

    async def judge(self, request: JudgeRequest) -> JudgeDecision | None: ...


def thaw_json(value: FrozenJsonValue) -> object:
    if isinstance(value, FrozenMap):
        return {key: thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(child) for child in value]
    return value


def _frozen_object(label: str, value: Mapping[str, object]) -> FrozenMap[str, FrozenJsonValue]:
    frozen = freeze_json(value)
    if not isinstance(frozen, FrozenMap):  # pragma: no cover - Mapping input guarantees this
        raise TypeError(f"{label} must be a JSON object")
    return frozen


def _references(values: tuple[str, ...]) -> tuple[str, ...]:
    for value in values:
        _require_text("Evidence reference", value)
    return tuple(sorted(set(values)))


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "EvalExecution",
    "EvalExecutionRequest",
    "EvalProvider",
    "EvalProviderError",
    "JudgeDecision",
    "JudgeRequest",
]
