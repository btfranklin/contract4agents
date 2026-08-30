from __future__ import annotations

import json

import pytest

from contract4agents.eval_campaigns import TrialMetrics
from contract4agents.ir import (
    semantic_id,
)
from contract4agents.tracing import (
    ProviderOutcomeEvidence,
    ProviderUsageEvidence,
    provider_outcome_event_data,
    provider_usage_event_data,
)


def _outcome(**overrides: object) -> ProviderOutcomeEvidence:
    values: dict[str, object] = {
        "agent_id": semantic_id("agent", "Researcher"),
        "attempt_id": "attempt-1",
        "phase": "response",
        "outcome": "succeeded",
        "category": "transport",
        "state": "observed",
        "classifier_provenance": "test.provider",
    }
    values.update(overrides)
    return ProviderOutcomeEvidence(**values)  # type: ignore[arg-type]


def _usage(**overrides: object) -> ProviderUsageEvidence:
    values: dict[str, object] = {
        "scope": "attempt",
        "coverage": "complete",
        "aggregation_identity": "attempt-1",
        "aggregation_basis": "one test call",
        "provenance": "test.provider",
        "request_count": 1,
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "output_tokens": 5,
        "reasoning_tokens": 1,
        "total_tokens": 15,
        "agent_id": semantic_id("agent", "Researcher"),
        "attempt_id": "attempt-1",
    }
    values.update(overrides)
    return ProviderUsageEvidence(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("state", ("observed", "inferred", "unavailable", "unverified"))
def test_outcome_states_are_exact_and_round_trip(state: str) -> None:
    value = _outcome(state=state)
    encoded = value.to_json()
    assert json.loads(encoded) == value.to_dict()
    assert ProviderOutcomeEvidence.from_dict(json.loads(encoded)) == value
    assert value.claim_state == state


def test_outcome_rejects_negative_and_content_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _outcome(http_status=-1)
    with pytest.raises(ValueError, match="unsupported content"):
        _outcome(provider_error_code="provider error")
    with pytest.raises(ValueError, match="failure category"):
        _outcome(category="provider_error")
    with pytest.raises(ValueError, match="unsupported content"):
        _outcome(response_id="response\nsecret")


def test_outcome_event_helper_has_only_evidence_payload() -> None:
    value = _outcome(response_id="response-1")
    assert provider_outcome_event_data(value) == {"evidence": value.to_dict()}
    assert "secret" not in json.dumps(provider_outcome_event_data(value))


def test_usage_zero_is_distinct_from_unavailable() -> None:
    zero = _usage(
        request_count=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        total_tokens=0,
    )
    unavailable = _usage(
        coverage="unavailable",
        request_count=None,
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        total_tokens=None,
    )
    assert zero.request_count == 0
    assert unavailable.request_count is None
    assert unavailable.to_dict()["input_tokens"] is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scope", "invalid", "Unsupported scope"),
        ("coverage", "invalid", "Unsupported coverage"),
        ("request_count", -1, "non-negative"),
        ("cached_input_tokens", 11, "cannot exceed"),
        ("reasoning_tokens", 6, "cannot exceed"),
        ("agent_id", 42, "semantic ID"),
    ],
)
def test_usage_rejects_invalid_fields(field: str, value: object, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _usage(**{field: value})


def test_usage_rejects_inconsistent_totals_and_complete_missing_values() -> None:
    with pytest.raises(ValueError, match="equal"):
        _usage(total_tokens=16)
    with pytest.raises(ValueError, match="requires input"):
        _usage(input_tokens=None)
    with pytest.raises(ValueError, match="unavailable"):
        _usage(
            coverage="unavailable",
            request_count=None,
            input_tokens=0,
            cached_input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("agent_id", 42, "semantic ID"),
        ("attempt_number", 0, "at least one"),
        ("http_status", 1000, "at most 999"),
        ("retry_after_seconds", True, "numeric"),
        ("retry_after_seconds", float("inf"), "finite"),
    ],
)
def test_outcome_rejects_invalid_structured_facts(field: str, value: object, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _outcome(**{field: value})


def test_outcome_rejects_refusal_and_cancellation_category_mismatches() -> None:
    with pytest.raises(ValueError, match="refused outcome"):
        _outcome(outcome="refused", category="provider_error")
    with pytest.raises(ValueError, match="cancelled outcome"):
        _outcome(outcome="cancelled", category="provider_error")


def test_evidence_deserializers_are_strict() -> None:
    with pytest.raises(TypeError, match="object"):
        ProviderOutcomeEvidence.from_dict([])
    with pytest.raises(ValueError, match="missing required fields"):
        ProviderOutcomeEvidence.from_dict({"agent_id": "agent:Researcher"})
    with pytest.raises(ValueError, match="unknown fields"):
        ProviderUsageEvidence.from_dict(
            {
                "scope": "run",
                "coverage": "unavailable",
                "aggregation_identity": "run-1",
                "aggregation_basis": "fixture",
                "provenance": "fixture",
                "unknown": True,
            }
        )
    with pytest.raises(TypeError, match="object"):
        ProviderUsageEvidence.from_dict([])
    with pytest.raises(ValueError, match="non-empty"):
        _outcome(classifier_provenance="")
    with pytest.raises(ValueError, match="non-empty"):
        _usage(aggregation_basis="")


def test_usage_round_trip_and_trial_metric_derivation_deduplicates_identity() -> None:
    value = _usage()
    encoded = json.loads(value.to_json())
    assert ProviderUsageEvidence.from_dict(encoded) == value
    assert provider_usage_event_data(value) == {"evidence": value.to_dict()}
    derived = TrialMetrics.from_provider_usage((value, value))
    assert derived.input_tokens == 10
    assert derived.output_tokens == 5
    conflicting = _usage(output_tokens=6, total_tokens=16)
    assert TrialMetrics.from_provider_usage((value, conflicting)).input_tokens is None
