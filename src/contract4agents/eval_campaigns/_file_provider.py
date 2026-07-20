"""Deterministic file-backed eval provider for `eval-data.json`."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from contract4agents.eval_campaigns._models import TrialMetrics
from contract4agents.eval_campaigns._provider import (
    ApprovalDecision,
    ApprovalRequest,
    EvalExecution,
    EvalExecutionRequest,
    EvalProviderError,
    JudgeDecision,
    JudgeRequest,
    thaw_json,
)
from contract4agents.ir import EvalIR, FrozenMap, SemanticId, freeze_json
from contract4agents.tracing import (
    NormalizedTrace,
    ProviderCorrelation,
    RedactionMetadata,
    TraceAttempt,
    TraceAttemptClosure,
    TraceClosureEvidence,
    TraceClosureStatus,
    TraceEvent,
    TraceFrontier,
    TraceInstrumentationChannel,
    TraceRunContext,
    TraceSemanticRefs,
)

EVAL_DATA_VERSION = "1"


@dataclass(frozen=True)
class FileEvalProvider:
    """Serve repeatable trial outputs, traces, approvals, and judge results from JSON."""

    cases: Mapping[str, object]
    source_name: str = "eval-data.json"

    def __post_init__(self) -> None:
        frozen = freeze_json(self.cases)
        if not isinstance(frozen, FrozenMap):  # pragma: no cover - Mapping input guarantees this
            raise TypeError("Eval-data cases must be an object")
        object.__setattr__(self, "cases", frozen)
        if not self.source_name.strip():
            raise ValueError("Eval-data source name must be non-empty")

    @classmethod
    def load(cls, path: Path) -> FileEvalProvider:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvalProviderError(f"Could not load eval data `{path}`: {exc}") from exc
        root = _object("eval data", payload)
        if root.get("schema_version") != EVAL_DATA_VERSION:
            raise EvalProviderError(
                f"Unsupported eval-data schema_version `{root.get('schema_version')}`"
            )
        cases = _object("eval data cases", root.get("cases"))
        return cls(cases, path.name)

    async def resolve_inputs(self, case: EvalIR, *, trial_index: int) -> Mapping[str, object]:
        case_data = self._case(case.id)
        trial = self._trial(case.id, trial_index)
        resolved = {name: thaw_json(value) for name, value in case.givens.items()}
        resolved.update(_object("case inputs", case_data.get("inputs", {})))
        resolved.update(_object("trial inputs", trial.get("inputs", {})))
        return resolved

    async def execute(self, request: EvalExecutionRequest) -> EvalExecution:
        trial = self._trial(request.case.id, request.trial_index)
        output = _object("trial output", trial.get("output"))
        events = _array("trial events", trial.get("events"))
        if not events:
            raise EvalProviderError(f"Trial `{request.trial_id}` does not contain normalized trace events")
        trace, attempt_closures = _normalized_trace(request, events, self.source_name)
        closure_data = _object("trial closure", trial.get("closure"))
        closure = TraceClosureEvidence(
            context=trace.events[0].context,
            status=cast(TraceClosureStatus, _string("closure status", closure_data.get("status"))),
            reason=_string("closure reason", closure_data.get("reason")),
            frontier=TraceFrontier.from_trace(trace),
            channels=cast(
                tuple[TraceInstrumentationChannel, ...],
                _strings("closure channels", closure_data.get("channels")),
            ),
            attempts=attempt_closures,
            evidence_refs=_strings("closure evidence_refs", closure_data.get("evidence_refs")),
        )
        metrics = _metrics(_object("trial metrics", trial.get("metrics", {})))
        return EvalExecution(output, trace, closure, metrics)

    async def approve(self, request: ApprovalRequest) -> ApprovalDecision | None:
        trial = self._trial(request.case_id, _trial_index(request.trial_id))
        approvals = _object("trial approvals", trial.get("approvals", {}))
        value = approvals.get(str(request.capability_id), approvals.get(request.capability_id.parts[-1]))
        if value is None:
            return None
        if isinstance(value, bool):
            return ApprovalDecision(value, "Deterministic eval-data approval decision.")
        decision = _object("approval decision", value)
        approved = decision.get("approved")
        if not isinstance(approved, bool):
            raise EvalProviderError("Approval decision `approved` must be boolean")
        return ApprovalDecision(
            approved,
            _string("approval reason", decision.get("reason", "Deterministic eval-data approval decision.")),
            _strings("approval evidence_refs", decision.get("evidence_refs", [])),
        )

    async def judge(self, request: JudgeRequest) -> JudgeDecision | None:
        trial = self._trial(request.case_id, _trial_index(request.trial_id))
        judges = _object("trial judges", trial.get("judges", {}))
        value = judges.get(str(request.quality_id), judges.get(request.quality_id.parts[-1]))
        if value is None:
            return None
        decision = _object("judge decision", value)
        status = decision.get("status")
        if status == "error":
            raise EvalProviderError(_string("judge error", decision.get("reason", "Judge failed")))
        if status not in {"passed", "violated"}:
            raise EvalProviderError("Judge decision status must be `passed` or `violated`")
        score = decision.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, int | float)):
            raise EvalProviderError("Judge decision score must be numeric")
        return JudgeDecision(
            passed=status == "passed",
            reason=_string("judge reason", decision.get("reason")),
            score=float(score) if score is not None else None,
            evidence_refs=_strings("judge evidence_refs", decision.get("evidence_refs", [])),
            provider=_string("judge provider", decision.get("provider", "file")),
            version=_string("judge version", decision.get("version", EVAL_DATA_VERSION)),
        )

    def _case(self, case_id: SemanticId) -> Mapping[str, object]:
        value = self.cases.get(str(case_id))
        if value is None:
            raise EvalProviderError(f"No eval-data entry exists for `{case_id}`")
        return _object(f"case `{case_id}`", value)

    def _trial(self, case_id: SemanticId, trial_index: int) -> Mapping[str, object]:
        case = self._case(case_id)
        trials = _array(f"case `{case_id}` trials", case.get("trials"))
        if trial_index < 0 or trial_index >= len(trials):
            raise EvalProviderError(f"No trial {trial_index + 1} exists for `{case_id}`")
        return _object(f"case `{case_id}` trial {trial_index + 1}", trials[trial_index])


def _normalized_trace(
    request: EvalExecutionRequest,
    values: Sequence[object],
    source_name: str,
) -> tuple[NormalizedTrace, tuple[TraceAttemptClosure, ...]]:
    digest = hashlib.sha256(f"{request.case.id}:{request.trial_index}".encode()).hexdigest()[:12]
    run_id = f"eval-{digest}-{request.trial_index + 1:04d}"
    context = TraceRunContext(run_id, run_id, request.contract_digest, request.plan_digest)
    events: list[TraceEvent] = []
    attempts: dict[SemanticId, TraceAttempt] = {}
    previous: str | None = None
    for index, value in enumerate(values):
        item = _object("trace event", value)
        event_id = _string("trace event_id", item.get("event_id", f"evt-{index + 1:06d}"))
        parent = item.get("parent_event_id", previous)
        if parent is not None and not isinstance(parent, str):
            raise EvalProviderError("trace parent_event_id must be a string or null")
        semantic_data = _object("trace semantic", item.get("semantic", {}))
        semantic = TraceSemanticRefs(
            agent_id=_semantic(semantic_data.get("agent_id"), default=request.case.agent_id),
            capability_id=_semantic(semantic_data.get("capability_id")),
            composition_id=_semantic(semantic_data.get("composition_id")),
            context_id=_semantic(semantic_data.get("context_id")),
            grant_id=_semantic(semantic_data.get("grant_id")),
            isolation_id=_semantic(semantic_data.get("isolation_id")),
            quality_id=_semantic(semantic_data.get("quality_id")),
            control_ids=tuple(
                _semantic_required(control)
                for control in _array("trace semantic control_ids", semantic_data.get("control_ids", []))
            ),
        )
        agent_id = semantic.agent_id or request.case.agent_id
        attempt = attempts.setdefault(
            agent_id,
            TraceAttempt(
                invocation_id=f"{request.trial_id}:{agent_id}:invocation",
                attempt_id=f"{request.trial_id}:{agent_id}:attempt:1",
                number=1,
            ),
        )
        provider_data = item.get("provider")
        provider = (
            ProviderCorrelation.from_dict(provider_data)
            if provider_data is not None
            else ProviderCorrelation("file", run_id=run_id, span_id=event_id)
        )
        redaction_data = item.get("redaction")
        redaction = (
            RedactionMetadata.from_dict(redaction_data)
            if redaction_data is not None
            else RedactionMetadata()
        )
        evidence_refs = _strings(
            "trace evidence_refs",
            item.get("evidence_refs", [f"file:{request.case.id}:{request.trial_index + 1}:{index + 1}"]),
        )
        timestamp = item.get("timestamp", float(index))
        if isinstance(timestamp, bool) or not isinstance(timestamp, int | float):
            raise EvalProviderError("trace timestamp must be numeric")
        events.append(
            TraceEvent(
                context=context,
                event_id=event_id,
                parent_event_id=parent,
                event_type=_string("trace event_type", item.get("event_type")),
                timestamp=float(timestamp),
                semantic=semantic,
                data={**_object("trace data", item.get("data", {})), "attempt": attempt.to_dict()},
                provider=provider,
                evidence_refs=evidence_refs,
                provenance=_object("trace provenance", item.get("provenance", {"source": source_name})),
                redaction=redaction,
            )
        )
        previous = event_id
    for selection_index, (agent_id, attempt) in enumerate(
        sorted(attempts.items(), key=lambda item: str(item[0])),
        start=1,
    ):
        attempt_events = tuple(
            event
            for event in events
            if TraceAttempt.from_dict(event.data["attempt"]) == attempt
        )
        outcome = (
            "failed"
            if any(event.event_type in {"agent.failed", "output.schema_failed"} for event in attempt_events)
            else "succeeded"
        )
        selection_id = f"contract4agents:file:{attempt.attempt_id}:selected"
        events.append(
            TraceEvent(
                context=context,
                event_id=selection_id,
                parent_event_id=None,
                event_type="attempt.selected",
                timestamp=float(len(values) + selection_index),
                semantic=TraceSemanticRefs(agent_id=agent_id),
                data={"attempt": attempt.to_dict(), "outcome": outcome},
                provider=ProviderCorrelation("file", run_id=run_id, span_id=selection_id),
                evidence_refs=(
                    f"file:{request.case.id}:{request.trial_index + 1}:selection:{agent_id}",
                ),
                provenance={"source": source_name},
                redaction=RedactionMetadata(),
            )
        )
    trace = NormalizedTrace(tuple(events))
    attempt_closures = tuple(
        TraceAttemptClosure(
            attempt=attempt,
            agent_id=agent_id,
            lifecycle_status="complete",
            response_status="complete",
            evidence_refs=(f"file:{request.case.id}:{request.trial_index + 1}:attempt:{agent_id}",),
            reason="The deterministic file provider supplied the complete attempt lifecycle.",
        )
        for agent_id, attempt in sorted(attempts.items(), key=lambda item: str(item[0]))
    )
    return trace, attempt_closures


def _metrics(value: Mapping[str, object]) -> TrialMetrics:
    return TrialMetrics(
        latency_ms=_optional_float("latency_ms", value.get("latency_ms")),
        cost_usd=_optional_float("cost_usd", value.get("cost_usd")),
        input_tokens=_optional_int("input_tokens", value.get("input_tokens")),
        output_tokens=_optional_int("output_tokens", value.get("output_tokens")),
    )


def _trial_index(trial_id: str) -> int:
    try:
        return int(trial_id.rsplit(":", 1)[1]) - 1
    except (IndexError, ValueError) as exc:
        raise EvalProviderError(f"Invalid deterministic trial ID `{trial_id}`") from exc


def _semantic(value: object, *, default: SemanticId | None = None) -> SemanticId | None:
    if value is None:
        return default
    return _semantic_required(value)


def _semantic_required(value: object) -> SemanticId:
    return SemanticId.parse(_string("semantic ID", value))


def _object(label: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EvalProviderError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise EvalProviderError(f"{label} keys must be strings")
    return cast(Mapping[str, object], value)


def _array(label: str, value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise EvalProviderError(f"{label} must be an array")
    return cast(Sequence[object], value)


def _string(label: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvalProviderError(f"{label} must be a non-empty string")
    return value


def _strings(label: str, value: object) -> tuple[str, ...]:
    return tuple(_string(label, item) for item in _array(label, value))


def _optional_float(label: str, value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvalProviderError(f"{label} must be numeric")
    return float(value)


def _optional_int(label: str, value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvalProviderError(f"{label} must be an integer")
    return value


__all__ = ["EVAL_DATA_VERSION", "FileEvalProvider"]
