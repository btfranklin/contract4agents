"""Post-run assessment for host-executed run-spec workflows."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from contract4agents.assurance._models import AssessorIdentity, AssuranceStatus
from contract4agents.compiler import build_artifacts
from contract4agents.expressions._grammar import parse_contract_expression
from contract4agents.expressions._model import ConditionalExpression, ExpressionError, ParsedExpression
from contract4agents.expressions._trace_evaluation import assess_trace_expression
from contract4agents.ir import (
    CanonicalIR,
    FrozenJsonValue,
    FrozenMap,
    NamedTypeRef,
    RunSpecIR,
    RunSpecStageIR,
    SemanticId,
    freeze_json,
)
from contract4agents.planning import MaterializationPlan
from contract4agents.run_specs import derived_value_collection_member_type
from contract4agents.tracing import (
    NormalizedTrace,
    TraceClosureEvidence,
    TraceCompletenessResult,
    TraceEvent,
    assess_trace_completeness,
    validate_trace_conformance,
)

RunSpecEvidenceStatus = Literal["complete", "incomplete", "unverified"]
_EVIDENCE_STATUSES = frozenset({"complete", "incomplete", "unverified"})
_ASSESSOR = AssessorIdentity("contract4agents.run_specs", "1")
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


@dataclass(frozen=True)
class RunSpecStageObservation:
    """One host-observed output for a declared run-spec stage."""

    observation_id: str
    stage: str
    agent_id: SemanticId
    output: object
    evidence_event_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("observation_id", self.observation_id)
        _require_text("stage", self.stage)
        self.agent_id.require_kind("agent")
        object.__setattr__(self, "output", freeze_json(self.output))
        object.__setattr__(self, "evidence_event_ids", _references("Evidence event ID", self.evidence_event_ids))
        object.__setattr__(self, "evidence_refs", _references("Evidence reference", self.evidence_refs))
        if not self.evidence_event_ids and not self.evidence_refs:
            raise ValueError("A stage observation requires an evidence event ID or evidence reference")

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": str(self.agent_id),
            "evidence_event_ids": list(self.evidence_event_ids),
            "evidence_refs": list(self.evidence_refs),
            "observation_id": self.observation_id,
            "output": _thaw(cast(FrozenJsonValue, self.output)),
            "stage": self.stage,
        }

    @classmethod
    def from_dict(cls, value: object) -> RunSpecStageObservation:
        payload = _object("run-spec stage observation", value)
        _keys(
            "run-spec stage observation",
            payload,
            {"agent_id", "evidence_event_ids", "evidence_refs", "observation_id", "output", "stage"},
        )
        return cls(
            observation_id=_string("observation_id", payload["observation_id"]),
            stage=_string("stage", payload["stage"]),
            agent_id=SemanticId.parse(_string("agent_id", payload["agent_id"])),
            output=payload["output"],
            evidence_event_ids=_strings("evidence_event_ids", payload["evidence_event_ids"]),
            evidence_refs=_strings("evidence_refs", payload["evidence_refs"]),
        )


@dataclass(frozen=True)
class RunSpecEvidence:
    """Host-supplied stage evidence and an explicit workflow-completeness claim."""

    status: RunSpecEvidenceStatus
    reason: str
    stage_observations: tuple[RunSpecStageObservation, ...] = ()
    derived_values: FrozenMap[str, FrozenJsonValue] = field(default_factory=FrozenMap)
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in _EVIDENCE_STATUSES:
            raise ValueError(f"Unsupported run-spec evidence status `{self.status}`")
        _require_text("reason", self.reason)
        observations = tuple(self.stage_observations)
        identifiers = [item.observation_id for item in observations]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Run-spec stage observation IDs must be unique")
        object.__setattr__(self, "stage_observations", observations)
        values = FrozenMap((name, freeze_json(value)) for name, value in self.derived_values.items())
        for name in values:
            _require_text("derived-value name", name)
        object.__setattr__(self, "derived_values", values)
        object.__setattr__(self, "evidence_refs", _references("Evidence reference", self.evidence_refs))
        if self.status == "complete" and not self.evidence_refs:
            raise ValueError("Complete run-spec evidence requires a completeness evidence reference")

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    def to_dict(self) -> dict[str, object]:
        return {
            "derived_values": {name: _thaw(value) for name, value in self.derived_values.items()},
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "stage_observations": [item.to_dict() for item in self.stage_observations],
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: object) -> RunSpecEvidence:
        payload = _object("run-spec evidence", value)
        _keys(
            "run-spec evidence",
            payload,
            {"derived_values", "evidence_refs", "reason", "stage_observations", "status"},
        )
        derived = _object("derived_values", payload["derived_values"])
        return cls(
            status=cast(RunSpecEvidenceStatus, _string("status", payload["status"])),
            reason=_string("reason", payload["reason"]),
            stage_observations=tuple(
                RunSpecStageObservation.from_dict(item)
                for item in _array("stage_observations", payload["stage_observations"])
            ),
            derived_values=FrozenMap((name, freeze_json(item)) for name, item in derived.items()),
            evidence_refs=_strings("evidence_refs", payload["evidence_refs"]),
        )


@dataclass(frozen=True)
class RunSpecSelection:
    """Host attestation selecting one run spec, or none, for a logical run."""

    run_id: str
    run_spec_id: str | None
    reason: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text("run_id", self.run_id)
        if self.run_spec_id is not None:
            _require_text("run_spec_id", self.run_spec_id)
        _require_text("reason", self.reason)
        object.__setattr__(
            self,
            "evidence_refs",
            _references("Evidence reference", self.evidence_refs),
        )
        if not self.evidence_refs:
            raise ValueError("Run-spec selection requires an evidence reference")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "run_id": self.run_id,
            "run_spec_id": self.run_spec_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> RunSpecSelection:
        payload = _object("run-spec selection", value)
        _keys("run-spec selection", payload, {"evidence_refs", "reason", "run_id", "run_spec_id"})
        run_spec_id = payload["run_spec_id"]
        if run_spec_id is not None and not isinstance(run_spec_id, str):
            raise TypeError("run_spec_id must be a string or null")
        return cls(
            run_id=_string("run_id", payload["run_id"]),
            run_spec_id=run_spec_id,
            reason=_string("reason", payload["reason"]),
            evidence_refs=_strings("evidence_refs", payload["evidence_refs"]),
        )


@dataclass(frozen=True)
class RunSpecStageResult:
    stage: str
    status: AssuranceStatus
    reason: str
    observation_ids: tuple[str, ...] = ()
    evidence_event_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("stage", self.stage)
        _validate_result(self.status, self.reason)
        object.__setattr__(self, "observation_ids", _references("Observation ID", self.observation_ids))
        object.__setattr__(self, "evidence_event_ids", _references("Evidence event ID", self.evidence_event_ids))
        object.__setattr__(self, "evidence_refs", _references("Evidence reference", self.evidence_refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_event_ids": list(self.evidence_event_ids),
            "evidence_refs": list(self.evidence_refs),
            "observation_ids": list(self.observation_ids),
            "reason": self.reason,
            "stage": self.stage,
            "status": self.status,
        }


@dataclass(frozen=True)
class RunSpecAssertionResult:
    assertion: str
    status: AssuranceStatus
    reason: str
    evidence_event_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("assertion", self.assertion)
        _validate_result(self.status, self.reason)
        object.__setattr__(self, "evidence_event_ids", _references("Evidence event ID", self.evidence_event_ids))
        object.__setattr__(self, "evidence_refs", _references("Evidence reference", self.evidence_refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "assertion": self.assertion,
            "evidence_event_ids": list(self.evidence_event_ids),
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "status": self.status,
        }


@dataclass(frozen=True)
class RunSpecResult:
    contract_digest: str
    plan_digest: str
    run_id: str
    run_spec_id: str
    evidence_digest: str
    status: AssuranceStatus
    reason: str
    assessor: AssessorIdentity
    stages: tuple[RunSpecStageResult, ...] = ()
    assertions: tuple[RunSpecAssertionResult, ...] = ()
    unexpected_observation_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_digest("contract_digest", self.contract_digest)
        _require_digest("plan_digest", self.plan_digest)
        _require_text("run_id", self.run_id)
        _require_text("run_spec_id", self.run_spec_id)
        _require_digest("evidence_digest", self.evidence_digest)
        _validate_result(self.status, self.reason)
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(self, "assertions", tuple(self.assertions))
        object.__setattr__(
            self,
            "unexpected_observation_ids",
            _references("Unexpected observation ID", self.unexpected_observation_ids),
        )
        object.__setattr__(self, "evidence_refs", _references("Evidence reference", self.evidence_refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "assertions": [item.to_dict() for item in self.assertions],
            "assessor": self.assessor.to_dict(),
            "contract_digest": self.contract_digest,
            "evidence_digest": self.evidence_digest,
            "evidence_refs": list(self.evidence_refs),
            "plan_digest": self.plan_digest,
            "reason": self.reason,
            "run_id": self.run_id,
            "run_spec_id": self.run_spec_id,
            "stages": [item.to_dict() for item in self.stages],
            "status": self.status,
            "unexpected_observation_ids": list(self.unexpected_observation_ids),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def assess_run_spec(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    trace: NormalizedTrace,
    run_spec: str | SemanticId,
    evidence: RunSpecEvidence,
    *,
    closure: TraceClosureEvidence | None = None,
    run_id: str | None = None,
) -> RunSpecResult:
    """Assess one declared run spec without executing or controlling its workflow."""

    selected = _select_run(trace, run_id)
    validate_trace_conformance(ir, plan, selected)
    declaration = _resolve_run_spec(ir, run_spec)
    trace_completeness = assess_trace_completeness(
        selected,
        plan.expected_telemetry,
        closure=closure,
        run_id=selected.run_ids[0],
    )
    observations_by_stage: dict[str, tuple[RunSpecStageObservation, ...]] = {
        stage.name: tuple(item for item in evidence.stage_observations if item.stage == stage.name)
        for stage in declaration.stages
    }
    declared_stages = set(observations_by_stage)
    unexpected = tuple(item.observation_id for item in evidence.stage_observations if item.stage not in declared_stages)
    schemas = build_artifacts(ir).schemas
    stages = tuple(
        _assess_stage(stage, observations_by_stage[stage.name], evidence, schemas, selected)
        for stage in declaration.stages
    )
    assertions = tuple(
        _assess_assertion(
            assertion,
            ir,
            selected,
            evidence,
            observations_by_stage,
            trace_completeness=trace_completeness,
        )
        for assertion in declaration.assertions
    )
    derived_status, derived_reason = _assess_derived_values(declaration, evidence)
    statuses: list[AssuranceStatus] = [item.status for item in stages]
    statuses.extend(item.status for item in assertions)
    statuses.append(derived_status)
    if unexpected:
        statuses.append("violated")
    status = _combined_status(statuses)
    reasons: list[str] = []
    if unexpected:
        reasons.append("Evidence contains observations for undeclared stages.")
    if derived_status != "passed":
        reasons.append(derived_reason)
    if status == "passed":
        reason = "Stage cardinality, output schemas, derived values, and assertions satisfy the run spec."
    elif status == "violated":
        reason = " ".join(reasons) or "At least one stage or assertion violates the run spec."
    else:
        reason = " ".join(reasons) or "Available evidence is insufficient to verify the run spec."
    return RunSpecResult(
        contract_digest=plan.contract_digest,
        plan_digest=plan.plan_digest,
        run_id=selected.run_ids[0],
        run_spec_id=str(declaration.id),
        evidence_digest=_digest_json(evidence.to_dict()),
        status=status,
        reason=reason,
        assessor=_ASSESSOR,
        stages=stages,
        assertions=assertions,
        unexpected_observation_ids=unexpected,
        evidence_refs=evidence.evidence_refs,
    )


def _assess_stage(
    stage: RunSpecStageIR,
    observations: tuple[RunSpecStageObservation, ...],
    evidence: RunSpecEvidence,
    schemas: Mapping[str, dict[str, object]],
    trace: NormalizedTrace,
) -> RunSpecStageResult:
    count = len(observations)
    refs = tuple(reference for item in observations for reference in item.evidence_refs)
    event_ids = tuple(event_id for item in observations for event_id in item.evidence_event_ids)
    identifiers = tuple(item.observation_id for item in observations)
    events_by_id = {event.event_id: event for event in trace.events}
    missing_event_ids = sorted(set(event_ids) - set(events_by_id))
    if missing_event_ids:
        return _stage_result(
            stage,
            "unverified",
            f"Stage observations reference missing trace events: {', '.join(missing_event_ids)}.",
            identifiers,
            event_ids,
            refs,
        )
    for observation in observations:
        linked_agents = {
            events_by_id[event_id].semantic.agent_id
            for event_id in observation.evidence_event_ids
            if events_by_id[event_id].semantic.agent_id is not None
        }
        if observation.evidence_event_ids and not linked_agents:
            return _stage_result(
                stage,
                "unverified",
                f"Stage observation `{observation.observation_id}` has no linked event with agent identity.",
                identifiers,
                event_ids,
                refs,
            )
        if any(agent_id != observation.agent_id for agent_id in linked_agents):
            return _stage_result(
                stage,
                "violated",
                f"Stage observation `{observation.observation_id}` conflicts with its linked event agent identity.",
                identifiers,
                event_ids,
                refs,
            )
    if any(item.agent_id != stage.agent_id for item in observations):
        return _stage_result(
            stage,
            "violated",
            "A stage observation names the wrong agent.",
            identifiers,
            event_ids,
            refs,
        )
    if stage.cardinality in {"one", "optional"} and count > 1:
        return _stage_result(
            stage,
            "violated",
            f"Stage `{stage.name}` has {count} outputs; at most one is allowed.",
            identifiers,
            event_ids,
            refs,
        )
    if evidence.complete and stage.cardinality in {"one", "many"} and count == 0:
        return _stage_result(
            stage,
            "violated",
            f"Required stage `{stage.name}` has no observed output.",
            identifiers,
            event_ids,
            refs,
        )
    schema_name = _schema_name(stage)
    schema = schemas.get(schema_name)
    if schema is None:
        return _stage_result(
            stage,
            "unverified",
            f"No portable schema is available for `{schema_name}`.",
            identifiers,
            event_ids,
            refs,
        )
    for observation in observations:
        try:
            Draft202012Validator(schema, format_checker=_FORMAT_CHECKER).validate(
                _thaw(cast(FrozenJsonValue, observation.output))
            )
        except ValidationError as exc:
            return _stage_result(
                stage,
                "violated",
                f"Stage `{stage.name}` output `{observation.observation_id}` does not conform "
                f"to `{schema_name}`: {exc.message}",
                identifiers,
                event_ids,
                refs,
            )
    if not evidence.complete:
        return _stage_result(
            stage,
            "unverified",
            "The host has not attested that workflow stage evidence is complete.",
            identifiers,
            event_ids,
            refs,
        )
    return _stage_result(
        stage,
        "passed",
        f"Stage `{stage.name}` satisfies its cardinality and output schema.",
        identifiers,
        event_ids,
        refs,
    )


def _stage_result(
    stage: RunSpecStageIR,
    status: AssuranceStatus,
    reason: str,
    identifiers: tuple[str, ...],
    event_ids: tuple[str, ...],
    refs: tuple[str, ...],
) -> RunSpecStageResult:
    return RunSpecStageResult(stage.name, status, reason, identifiers, event_ids, refs)


def _assess_derived_values(
    run_spec: RunSpecIR,
    evidence: RunSpecEvidence,
) -> tuple[AssuranceStatus, str]:
    declared = {item.name: item for item in run_spec.derived_values}
    extras = sorted(set(evidence.derived_values) - set(declared))
    if extras:
        return "violated", f"Evidence supplies undeclared derived values: {', '.join(extras)}."
    missing = sorted(set(declared) - set(evidence.derived_values))
    if missing:
        status: AssuranceStatus = "violated" if evidence.complete else "unverified"
        return status, f"Evidence omits declared derived values: {', '.join(missing)}."
    for name, declaration in declared.items():
        if not _derived_value_matches(declaration.type_name, evidence.derived_values[name]):
            return "violated", f"Derived value `value.{name}` does not match `{declaration.type_name}`."
    if not evidence.complete and declared:
        return "unverified", "The host has not attested that derived-value evidence is complete."
    return "passed", "Derived values match their declarations."


def _derived_value_matches(type_name: str, value: FrozenJsonValue) -> bool:
    member = derived_value_collection_member_type(type_name)
    if member is not None:
        return isinstance(value, tuple) and all(_derived_scalar_matches(member, item) for item in value)
    return _derived_scalar_matches(type_name, value)


def _derived_scalar_matches(type_name: str, value: FrozenJsonValue) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return type(value) is int
    if type_name == "float":
        return type(value) is float
    if type_name == "boolean":
        return type(value) is bool
    return False


def _assess_assertion(
    assertion: str,
    ir: CanonicalIR,
    trace: NormalizedTrace,
    evidence: RunSpecEvidence,
    observations_by_stage: Mapping[str, tuple[RunSpecStageObservation, ...]],
    *,
    trace_completeness: TraceCompletenessResult,
) -> RunSpecAssertionResult:
    if not evidence.complete:
        return RunSpecAssertionResult(
            assertion,
            "unverified",
            "Workflow completeness is insufficient to assess the assertion.",
            evidence_refs=evidence.evidence_refs,
        )
    try:
        parsed_items = parse_contract_expression(assertion)
    except ExpressionError as exc:
        return RunSpecAssertionResult(assertion, "unverified", str(exc))
    all_events: dict[str, TraceEvent] = {}
    statuses: list[AssuranceStatus] = []
    reasons: list[str] = []
    for item in parsed_items:
        status, reason, events = _evaluate_expression(
            item,
            ir,
            trace,
            evidence,
            observations_by_stage,
            trace_completeness,
        )
        statuses.append(status)
        reasons.append(reason)
        all_events.update((event.event_id, event) for event in events)
    ordered = tuple(all_events[name] for name in sorted(all_events))
    status = _combined_status(statuses)
    return RunSpecAssertionResult(
        assertion,
        status,
        "The complete evidence satisfies the assertion."
        if status == "passed"
        else next(reason for result, reason in zip(statuses, reasons, strict=True) if result == status),
        tuple(event.event_id for event in ordered),
        tuple(reference for event in ordered for reference in event.evidence_refs) + evidence.evidence_refs,
    )


def _evaluate_expression(
    parsed: ParsedExpression | ConditionalExpression,
    ir: CanonicalIR,
    trace: NormalizedTrace,
    evidence: RunSpecEvidence,
    observations_by_stage: Mapping[str, tuple[RunSpecStageObservation, ...]],
    completeness: TraceCompletenessResult,
) -> tuple[AssuranceStatus, str, tuple[TraceEvent, ...]]:
    if isinstance(parsed, ConditionalExpression):
        condition = _evaluate_parsed(
            parsed.condition,
            ir,
            trace,
            evidence,
            observations_by_stage,
            completeness,
        )
        if condition[0] == "violated":
            return "passed", "The assertion condition was proven false and did not apply.", condition[2]
        if condition[0] == "unverified":
            return "unverified", "The assertion condition could not be established.", condition[2]
        expectation = _evaluate_parsed(
            parsed.expectation,
            ir,
            trace,
            evidence,
            observations_by_stage,
            completeness,
        )
        return expectation[0], expectation[1], condition[2] + expectation[2]
    return _evaluate_parsed(parsed, ir, trace, evidence, observations_by_stage, completeness)


def _evaluate_parsed(
    parsed: ParsedExpression,
    ir: CanonicalIR,
    trace: NormalizedTrace,
    evidence: RunSpecEvidence,
    observations_by_stage: Mapping[str, tuple[RunSpecStageObservation, ...]],
    completeness: TraceCompletenessResult,
) -> tuple[AssuranceStatus, str, tuple[TraceEvent, ...]]:
    if parsed.kind == "trace":
        stage_events = {
            stage: tuple(
                event_id
                for observation in observations
                for event_id in observation.evidence_event_ids
            )
            for stage, observations in observations_by_stage.items()
        }
        result = assess_trace_expression(
            parsed,
            ir=ir,
            trace=trace,
            completeness=completeness,
            stage_event_ids=stage_events,
        )
        return result.status, result.reason, result.events
    if parsed.kind == "data_relation":
        passed, reason = _evaluate_data_relation(parsed, evidence.derived_values)
        return ("passed" if passed else "violated"), reason, ()
    return "unverified", f"Unsupported run-spec assertion `{parsed.expression}`.", ()


def _evaluate_data_relation(
    parsed: ParsedExpression,
    values: Mapping[str, FrozenJsonValue],
) -> tuple[bool, str]:
    assert parsed.left_ref is not None and parsed.right_ref is not None and parsed.operator is not None
    left = _derived_set(parsed.left_ref, values)
    right = _derived_set(parsed.right_ref, values)
    if isinstance(left, str):
        return False, left
    if isinstance(right, str):
        return False, right
    if parsed.operator == "subset_of":
        passed = left <= right
    elif parsed.operator == "contains_all":
        passed = left >= right
    elif parsed.operator == "equals_set":
        passed = left == right
    elif parsed.operator == "intersects":
        passed = bool(left & right)
    else:
        passed = not bool(left & right)
    return passed, f"Derived-value relation `{parsed.expression}` is not satisfied."


def _derived_set(name: str, values: Mapping[str, FrozenJsonValue]) -> set[object] | str:
    if name not in values:
        return f"Unknown derived value `value.{name}`."
    value = values[name]
    if isinstance(value, FrozenMap):
        return f"Derived value `value.{name}` must be a scalar or sequence of scalars."
    items: Sequence[object] | Set[object] = value if isinstance(value, tuple) else (value,)
    if any(not _is_scalar(item) for item in items):
        return f"Derived value `value.{name}` contains a non-scalar item."
    return set(items)


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _resolve_run_spec(ir: CanonicalIR, selector: str | SemanticId) -> RunSpecIR:
    if isinstance(selector, SemanticId):
        selector.require_kind("run_spec")
        selected = ir.run_specs.get(selector)
    else:
        selected = next(
            (item for item in ir.run_specs.values() if item.name == selector or str(item.id) == selector),
            None,
        )
    if selected is None:
        raise ValueError(f"Contract does not declare run spec `{selector}`")
    return selected


def _schema_name(stage: RunSpecStageIR) -> str:
    if not isinstance(stage.output_type, NamedTypeRef):
        raise ValueError(f"Run-spec stage `{stage.name}` must use a named output type")
    return stage.output_type.type_id.parts[0]


def _select_run(trace: NormalizedTrace, run_id: str | None) -> NormalizedTrace:
    if run_id is not None:
        return trace.for_run(run_id)
    if len(trace.run_ids) != 1:
        raise ValueError("Trace contains multiple runs; pass run_id explicitly")
    return trace


def _combined_status(statuses: Sequence[AssuranceStatus]) -> AssuranceStatus:
    if "violated" in statuses:
        return "violated"
    if "unverified" in statuses:
        return "unverified"
    return "passed"


def _validate_result(status: AssuranceStatus, reason: str) -> None:
    if status not in {"passed", "violated", "unverified"}:
        raise ValueError(f"Unsupported assurance status `{status}`")
    _require_text("reason", reason)


def _references(label: str, values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        normalized.add(value)
    return tuple(sorted(normalized))


def _object(label: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} must be an object with string keys")
    return cast(Mapping[str, object], value)


def _array(label: str, value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    return value


def _string(label: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    return value


def _strings(label: str, value: object) -> tuple[str, ...]:
    return tuple(_string(label, item) for item in _array(label, value))


def _keys(label: str, payload: Mapping[str, object], required: set[str]) -> None:
    missing = sorted(required - set(payload))
    unknown = sorted(set(payload) - required)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise ValueError(f"Invalid {label} keys: {'; '.join(details)}")


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _require_digest(label: str, value: str) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical sha256 digest")


def _digest_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _thaw(value: FrozenJsonValue) -> object:
    if isinstance(value, FrozenMap):
        return {name: _thaw(child) for name, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


__all__ = [
    "RunSpecAssertionResult",
    "RunSpecEvidence",
    "RunSpecEvidenceStatus",
    "RunSpecResult",
    "RunSpecSelection",
    "RunSpecStageResult",
    "RunSpecStageObservation",
    "assess_run_spec",
]
