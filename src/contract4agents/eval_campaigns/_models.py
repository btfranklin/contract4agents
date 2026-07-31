"""Immutable deterministic models for provider-neutral eval campaigns."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from contract4agents.assurance import (
    AssessorIdentity,
    AssuranceStatus,
    ControlResult,
)
from contract4agents.ir import FrozenJsonValue, FrozenMap, freeze_json
from contract4agents.tracing import (
    NormalizedTrace,
    TraceClosureEvidence,
    TraceEvidenceAssessment,
    dumps_trace_jsonl,
)

TrialStatus = AssuranceStatus
ComparisonStatus = AssuranceStatus
ExecutionStatus = Literal["succeeded", "failed"]


@dataclass(frozen=True)
class _TrialDataChannel:
    values: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _freeze_object(self.values))

    def to_dict(self) -> dict[str, object]:
        thawed = _thaw(self.values)
        assert isinstance(thawed, dict)
        return thawed


class InvocationInputs(_TrialDataChannel):
    """Validated values approved for the entry-agent invocation."""

    @property
    def digest(self) -> str:
        encoded = canonical_json(self.to_dict()).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class HostFixtureContext(_TrialDataChannel):
    """Trial-scoped host data that is not automatically model-visible."""


class EvaluatorTruth(_TrialDataChannel):
    """Scorer-only expected answers that must never reach execution."""


class RedactedTrialView(_TrialDataChannel):
    """Explicit trial data approved for ordinary report serialization."""


@dataclass(frozen=True)
class ResolvedTrialData:
    invocation: InvocationInputs
    host_context: HostFixtureContext
    evaluator_truth: EvaluatorTruth
    report_view: RedactedTrialView


@dataclass(frozen=True)
class EvalInventory:
    agent_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    grant_ids: tuple[str, ...]
    control_ids: tuple[str, ...]
    expected_event_types: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "agent_ids",
            "capability_ids",
            "grant_ids",
            "control_ids",
            "expected_event_types",
        ):
            object.__setattr__(self, name, _text_set(name, getattr(self, name)))

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_ids": list(self.agent_ids),
            "capability_ids": list(self.capability_ids),
            "control_ids": list(self.control_ids),
            "expected_event_types": list(self.expected_event_types),
            "grant_ids": list(self.grant_ids),
        }


@dataclass(frozen=True)
class ExpectationResult:
    expression: str
    status: AssuranceStatus
    reason: str
    evidence_event_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text("expression", self.expression)
        _require_status(self.status)
        _require_text("expectation reason", self.reason)
        object.__setattr__(self, "evidence_event_ids", _text_set("evidence_event_ids", self.evidence_event_ids))

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_event_ids": list(self.evidence_event_ids),
            "expression": self.expression,
            "reason": self.reason,
            "status": self.status,
        }


@dataclass(frozen=True)
class QualityResult:
    quality_id: str
    status: AssuranceStatus
    reason: str
    assessor: AssessorIdentity
    score: float | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text("quality_id", self.quality_id)
        _require_status(self.status)
        _require_text("quality reason", self.reason)
        if self.score is not None and (not math.isfinite(self.score) or not 0 <= self.score <= 1):
            raise ValueError("Quality score must be between zero and one")
        object.__setattr__(self, "evidence_refs", _text_set("evidence_refs", self.evidence_refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "assessor": self.assessor.to_dict(),
            "evidence_refs": list(self.evidence_refs),
            "quality_id": self.quality_id,
            "reason": self.reason,
            "score": self.score,
            "status": self.status,
        }


@dataclass(frozen=True)
class TrialMetrics:
    latency_ms: float | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("latency_ms", "cost_usd"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite non-negative number")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def to_dict(self) -> dict[str, object]:
        return {
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "latency_ms": self.latency_ms,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class FinalizedTrialEvidence:
    execution_status: ExecutionStatus
    output: Mapping[str, object] | None
    trace: NormalizedTrace | None
    closure: TraceClosureEvidence | None
    metrics: TrialMetrics = field(default_factory=TrialMetrics)
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.execution_status not in {"succeeded", "failed"}:
            raise ValueError(f"Unsupported execution status `{self.execution_status}`")
        if self.execution_status == "succeeded" and (
            self.output is None or self.trace is None or self.closure is None
        ):
            raise ValueError("Succeeded trial evidence requires output, trace, and closure")
        if self.execution_status == "failed" and self.diagnostic is None:
            raise ValueError("Failed trial evidence requires an execution diagnostic")
        if self.output is not None:
            object.__setattr__(self, "output", _freeze_object(self.output))
        if self.diagnostic is not None:
            _require_text("execution diagnostic", self.diagnostic)


@dataclass(frozen=True)
class NumericSummary:
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    median: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "maximum": self.maximum,
            "mean": self.mean,
            "median": self.median,
            "minimum": self.minimum,
        }


@dataclass(frozen=True)
class UncertaintyInterval:
    lower: float
    upper: float
    confidence: float = 0.95

    def to_dict(self) -> dict[str, float]:
        return {"confidence": self.confidence, "lower": self.lower, "upper": self.upper}


@dataclass(frozen=True)
class RateSummary:
    total: int
    passed: int
    violated: int
    unverified: int
    pass_rate: float
    violation_rate: float
    pass_interval: UncertaintyInterval
    violation_interval: UncertaintyInterval

    def to_dict(self) -> dict[str, object]:
        return {
            "pass_interval": self.pass_interval.to_dict(),
            "pass_rate": self.pass_rate,
            "passed": self.passed,
            "total": self.total,
            "unverified": self.unverified,
            "violated": self.violated,
            "violation_interval": self.violation_interval.to_dict(),
            "violation_rate": self.violation_rate,
        }


@dataclass(frozen=True)
class MetricsSummary:
    latency_ms: NumericSummary
    cost_usd: NumericSummary
    input_tokens: NumericSummary
    output_tokens: NumericSummary
    total_tokens: NumericSummary

    def to_dict(self) -> dict[str, object]:
        return {
            "cost_usd": self.cost_usd.to_dict(),
            "input_tokens": self.input_tokens.to_dict(),
            "latency_ms": self.latency_ms.to_dict(),
            "output_tokens": self.output_tokens.to_dict(),
            "total_tokens": self.total_tokens.to_dict(),
        }


@dataclass(frozen=True)
class ResultSummary:
    rates: RateSummary
    metrics: MetricsSummary

    def to_dict(self) -> dict[str, object]:
        return {"metrics": self.metrics.to_dict(), "rates": self.rates.to_dict()}


@dataclass(frozen=True)
class ComparisonResult:
    name: str
    status: ComparisonStatus
    reason: str
    actual: float | None
    target: float
    operator: str

    def __post_init__(self) -> None:
        _require_text("comparison name", self.name)
        _require_status(self.status)
        _require_text("comparison reason", self.reason)

    def to_dict(self) -> dict[str, object]:
        return {
            "actual": self.actual,
            "name": self.name,
            "operator": self.operator,
            "reason": self.reason,
            "status": self.status,
            "target": self.target,
        }


@dataclass(frozen=True)
class CampaignThresholds:
    min_pass_rate: float | None = None
    max_violation_rate: float | None = None
    max_mean_latency_ms: float | None = None
    max_mean_cost_usd: float | None = None

    def __post_init__(self) -> None:
        for name in ("min_pass_rate", "max_violation_rate"):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        for name in ("max_mean_latency_ms", "max_mean_cost_usd"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite non-negative number")


@dataclass(frozen=True)
class BaselineSnapshot:
    digest: str
    pass_rate: float
    violation_rate: float
    mean_latency_ms: float | None = None
    mean_cost_usd: float | None = None

    def __post_init__(self) -> None:
        _require_text("baseline digest", self.digest)
        for name in ("pass_rate", "violation_rate"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"Baseline {name} must be between zero and one")
        for name in ("mean_latency_ms", "mean_cost_usd"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"Baseline {name} must be a finite non-negative number")


@dataclass(frozen=True)
class BaselineTolerance:
    max_pass_rate_drop: float = 0.0
    max_violation_rate_increase: float = 0.0
    max_latency_increase_ratio: float | None = None
    max_cost_increase_ratio: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_pass_rate_drop",
            "max_violation_rate_increase",
            "max_latency_increase_ratio",
            "max_cost_increase_ratio",
        ):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite non-negative number")


@dataclass(frozen=True)
class CampaignConfig:
    campaign_id: str
    trial_count: int = 1
    thresholds: CampaignThresholds = field(default_factory=CampaignThresholds)
    baseline: BaselineSnapshot | None = None
    baseline_tolerance: BaselineTolerance = field(default_factory=BaselineTolerance)

    def __post_init__(self) -> None:
        _require_text("campaign_id", self.campaign_id)
        if isinstance(self.trial_count, bool) or not isinstance(self.trial_count, int) or self.trial_count < 1:
            raise ValueError("trial_count must be a positive integer")


@dataclass(frozen=True)
class TrialResult:
    case_id: str
    trial_id: str
    status: TrialStatus
    invocation_digest: str | None
    report_view: RedactedTrialView
    output: Mapping[str, object] | None
    trace: NormalizedTrace | None
    expectations: tuple[ExpectationResult, ...]
    controls: tuple[ControlResult, ...]
    qualities: tuple[QualityResult, ...]
    trace_evidence: TraceEvidenceAssessment | None
    trace_closure: TraceClosureEvidence | None
    metrics: TrialMetrics
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        _require_text("case_id", self.case_id)
        _require_text("trial_id", self.trial_id)
        _require_status(self.status)
        if self.invocation_digest is not None:
            _require_text("invocation_digest", self.invocation_digest)
        if self.output is not None:
            object.__setattr__(self, "output", _freeze_object(self.output))
        object.__setattr__(self, "expectations", tuple(self.expectations))
        object.__setattr__(self, "controls", tuple(self.controls))
        object.__setattr__(self, "qualities", tuple(self.qualities))

    @property
    def trace_digest(self) -> str | None:
        if self.trace is None:
            return None
        encoded = dumps_trace_jsonl(self.trace).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "controls": [item.to_dict() for item in self.controls],
            "diagnostic": self.diagnostic,
            "expectations": [item.to_dict() for item in self.expectations],
            "invocation_digest": self.invocation_digest,
            "metrics": self.metrics.to_dict(),
            "output": _thaw(self.output) if self.output is not None else None,
            "qualities": [item.to_dict() for item in self.qualities],
            "report": self.report_view.to_dict(),
            "status": self.status,
            "trace_evidence": (
                self.trace_evidence.to_dict() if self.trace_evidence is not None else None
            ),
            "trace_digest": self.trace_digest,
            "trace_closure_digest": self.trace_closure.digest if self.trace_closure is not None else None,
            "trace_run_ids": list(self.trace.run_ids) if self.trace is not None else [],
            "trial_id": self.trial_id,
        }


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    name: str
    agent_id: str
    trials: tuple[TrialResult, ...]
    summary: ResultSummary

    def __post_init__(self) -> None:
        object.__setattr__(self, "trials", tuple(self.trials))

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "case_id": self.case_id,
            "name": self.name,
            "summary": self.summary.to_dict(),
            "trials": [trial.to_dict() for trial in self.trials],
        }


@dataclass(frozen=True)
class CampaignResult:
    campaign_id: str
    contract_digest: str
    plan_digest: str
    target: str
    profile: str
    inventory: EvalInventory
    cases: tuple[CaseResult, ...]
    summary: ResultSummary
    threshold_results: tuple[ComparisonResult, ...] = field(default_factory=tuple)
    baseline_digest: str | None = None
    regression_results: tuple[ComparisonResult, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cases", tuple(self.cases))
        object.__setattr__(self, "threshold_results", tuple(self.threshold_results))
        object.__setattr__(self, "regression_results", tuple(self.regression_results))

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_digest": self.baseline_digest,
            "campaign_id": self.campaign_id,
            "cases": [case.to_dict() for case in self.cases],
            "contract_digest": self.contract_digest,
            "inventory": self.inventory.to_dict(),
            "plan_digest": self.plan_digest,
            "profile": self.profile,
            "regression_results": [item.to_dict() for item in self.regression_results],
            "summary": self.summary.to_dict(),
            "target": self.target,
            "threshold_results": [item.to_dict() for item in self.threshold_results],
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @property
    def campaign_digest(self) -> str:
        return f"sha256:{hashlib.sha256(self.to_json().encode()).hexdigest()}"


def summarize_trials(trials: tuple[TrialResult, ...]) -> ResultSummary:
    statuses = tuple(trial.status for trial in trials)
    metrics = tuple(trial.metrics for trial in trials)
    return ResultSummary(
        rates=_rate_summary(statuses),
        metrics=MetricsSummary(
            latency_ms=_numeric_summary(tuple(item.latency_ms for item in metrics)),
            cost_usd=_numeric_summary(tuple(item.cost_usd for item in metrics)),
            input_tokens=_numeric_summary(tuple(item.input_tokens for item in metrics)),
            output_tokens=_numeric_summary(tuple(item.output_tokens for item in metrics)),
            total_tokens=_numeric_summary(tuple(item.total_tokens for item in metrics)),
        ),
    )


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _rate_summary(statuses: tuple[TrialStatus, ...]) -> RateSummary:
    total = len(statuses)
    passed = statuses.count("passed")
    violated = statuses.count("violated")
    unverified = statuses.count("unverified")
    pass_rate = _rounded(passed / total) if total else 0.0
    violation_rate = _rounded(violated / total) if total else 0.0
    return RateSummary(
        total,
        passed,
        violated,
        unverified,
        pass_rate,
        violation_rate,
        _wilson(passed, total),
        _wilson(violated, total),
    )


def _wilson(successes: int, total: int) -> UncertaintyInterval:
    if total == 0:
        return UncertaintyInterval(0.0, 1.0)
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total) / denominator
    return UncertaintyInterval(_rounded(max(0.0, centre - margin)), _rounded(min(1.0, centre + margin)))


def _numeric_summary(values: tuple[float | int | None, ...]) -> NumericSummary:
    present = sorted(float(value) for value in values if value is not None)
    if not present:
        return NumericSummary(0, None, None, None, None)
    midpoint = len(present) // 2
    median = (
        present[midpoint]
        if len(present) % 2
        else (present[midpoint - 1] + present[midpoint]) / 2
    )
    return NumericSummary(
        len(present),
        _rounded(present[0]),
        _rounded(present[-1]),
        _rounded(sum(present) / len(present)),
        _rounded(median),
    )


def _rounded(value: float) -> float:
    return round(value, 12)


def _freeze_object(value: Mapping[str, object]) -> FrozenMap[str, FrozenJsonValue]:
    frozen = freeze_json(value)
    if not isinstance(frozen, FrozenMap):  # pragma: no cover - Mapping input guarantees this
        raise TypeError("Expected a JSON object")
    return frozen


def _thaw(value: object) -> object:
    if isinstance(value, FrozenMap):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _text_set(label: str, values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        _require_text(label, value)
        normalized.add(value)
    return tuple(sorted(normalized))


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _require_status(status: str) -> None:
    if status not in {"passed", "violated", "unverified"}:
        raise ValueError(f"Unsupported assurance status `{status}`")


__all__ = [
    "BaselineSnapshot",
    "BaselineTolerance",
    "CampaignConfig",
    "CampaignResult",
    "CampaignThresholds",
    "CaseResult",
    "ComparisonResult",
    "EvaluatorTruth",
    "EvalInventory",
    "ExecutionStatus",
    "ExpectationResult",
    "FinalizedTrialEvidence",
    "HostFixtureContext",
    "InvocationInputs",
    "MetricsSummary",
    "NumericSummary",
    "QualityResult",
    "RateSummary",
    "RedactedTrialView",
    "ResolvedTrialData",
    "ResultSummary",
    "TrialMetrics",
    "TrialResult",
    "TrialStatus",
    "UncertaintyInterval",
    "canonical_json",
    "summarize_trials",
]
