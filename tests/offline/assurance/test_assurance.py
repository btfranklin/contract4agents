from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from contract4agents.assurance import (
    AssessorIdentity,
    ControlResult,
)
from contract4agents.tracing import TraceEvidenceAssessment


def test_control_result_is_immutable_and_serializes_deterministically() -> None:
    result = ControlResult(
        control_id="control:IncidentCommander:approval:status.publish",
        status="passed",
        reason="Approval was granted before the capability started.",
        assessment="runtime",
        assessor=AssessorIdentity("contract4agents", "1"),
        evidence_event_ids=("evt-000005", "evt-000003", "evt-000004", "evt-000003"),
        evidence_refs=("provider:openai:span-2", "provider:openai:span-1"),
    )
    same_result = ControlResult(
        control_id="control:IncidentCommander:approval:status.publish",
        status="passed",
        reason="Approval was granted before the capability started.",
        assessment="runtime",
        assessor=AssessorIdentity("contract4agents", "1"),
        evidence_event_ids=("evt-000004", "evt-000005", "evt-000003"),
        evidence_refs=("provider:openai:span-1", "provider:openai:span-2"),
    )

    assert result.evidence_event_ids == ("evt-000003", "evt-000004", "evt-000005")
    assert result.to_json() == same_result.to_json()
    assert json.loads(result.to_json()) == result.to_dict()
    assert result.to_dict()["assessor"] == {"name": "contract4agents", "version": "1"}
    with pytest.raises(FrozenInstanceError):
        result.status = "violated"  # type: ignore[misc]


@pytest.mark.parametrize("status", ["passed", "violated", "unverified"])
def test_control_result_supports_every_assurance_status(status: str) -> None:
    result = ControlResult(
        control_id="control:A:example",
        status=status,  # type: ignore[arg-type]
        reason="Evidence was assessed.",
        assessment="post_run",
        assessor=AssessorIdentity("test-assessor", "1"),
    )

    assert result.status == status


@pytest.mark.parametrize(
    "assessment",
    ["static", "adapter", "runtime", "host_attested", "post_run", "semantic", "advisory"],
)
def test_control_result_supports_every_assessment_classification(assessment: str) -> None:
    result = ControlResult(
        control_id="control:A:example",
        status="unverified",
        reason="Evidence is not available.",
        assessment=assessment,  # type: ignore[arg-type]
        assessor=AssessorIdentity("test-assessor", "1"),
    )

    assert result.assessment == assessment


def test_control_result_rejects_unknown_values_and_empty_references() -> None:
    assessor = AssessorIdentity("test-assessor", "1")

    with pytest.raises(ValueError, match="assurance status"):
        ControlResult("control:A:x", "skipped", "No judge", "semantic", assessor)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="assessment classification"):
        ControlResult("control:A:x", "passed", "ok", "manual", assessor)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Evidence event ID"):
        ControlResult("control:A:x", "passed", "ok", "runtime", assessor, evidence_event_ids=("",))


def test_trace_evidence_reports_missing_expected_event_types() -> None:
    result = TraceEvidenceAssessment(
        run_id="run-123",
        status="incomplete",
        reason="Approval instrumentation did not emit a completion event.",
        expected_event_types=("tool", "approval", "agent", "approval"),
        observed_event_types=("tool", "agent"),
        evidence_refs=("trace:run-123",),
    )

    assert not result.complete
    assert result.expected_event_types == ("agent", "approval", "tool")
    assert result.missing_event_types == ("approval",)
    assert json.loads(result.to_json()) == result.to_dict()
    assert result.to_dict()["missing_event_types"] == ["approval"]


def test_trace_evidence_rejects_a_complete_result_with_missing_event_types() -> None:
    with pytest.raises(ValueError, match="Complete trace evidence"):
        TraceEvidenceAssessment(
            run_id="run-123",
            status="complete",
            reason="All expected event types were observed.",
            expected_event_types=("agent", "tool"),
            observed_event_types=("agent",),
        )


def test_trace_evidence_supports_unverified_without_claiming_completion() -> None:
    result = TraceEvidenceAssessment(
        run_id="run-123",
        status="unverified",
        reason="The imported provider trace does not attest instrumentation coverage.",
    )

    assert not result.complete
    assert result.missing_event_types == ()
