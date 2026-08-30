from __future__ import annotations

import json
from typing import Any, cast

import pytest

from contract4agents.ir import semantic_id
from contract4agents.materialization import (
    ConfigurationConformanceEvidence,
    GraphValidationEvidence,
    SchemaConformanceEvidence,
)
from contract4agents.materialization._configuration import (
    MISSING,
    configuration_evidence,
    digest_only_configuration_evidence,
    flatten_mapping,
    read_public_path,
)


def _graph(configuration: tuple[ConfigurationConformanceEvidence, ...]) -> GraphValidationEvidence:
    agent = semantic_id("agent", "Worker")
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return GraphValidationEvidence(
        adapter="test",
        adapter_version="1",
        contract_digest="sha256:contract",
        plan_digest="sha256:plan",
        agent_ids=(agent,),
        grant_ids=(),
        composition_ids=(),
        schema_conformance=(SchemaConformanceEvidence(agent, "agent_output", schema, schema),),
        configuration_conformance=configuration,
    )


def _complete_configuration() -> tuple[ConfigurationConformanceEvidence, ...]:
    agent = semantic_id("agent", "Worker")
    return tuple(
        configuration_evidence(agent, path, value, value)
        for path, value in (
            ("agent.name", "Worker"),
            ("agent.identity", {"name": "Worker"}),
            ("agent.model", "test-model"),
            ("agent.model_options", {}),
            ("agent.output_type", "Result"),
            ("agent.output_mode", "native"),
            ("agent.tools", []),
            ("agent.handoffs", []),
        )
    )


def test_configuration_evidence_preserves_false_zero_null_and_omission() -> None:
    agent = semantic_id("agent", "Worker")
    records = (
        configuration_evidence(agent, "agent.model_settings.store", False, False),
        configuration_evidence(agent, "agent.model_settings.retry.max_retries", 0, 0),
        configuration_evidence(agent, "agent.model_settings.reasoning.effort", None, None),
        configuration_evidence(agent, "agent.model_settings.temperature", MISSING, MISSING),
    )

    assert all(item.status == "passed" for item in records)
    assert records[0].planned_value is False
    assert records[1].planned_value == 0
    assert records[2].planned_present is True
    assert records[3].planned_present is False
    assert ConfigurationConformanceEvidence.from_dict(records[0].to_dict()) == records[0]
    assert json.dumps([item.to_dict() for item in records], sort_keys=True)


def test_arbitrary_option_payload_is_digest_only_and_excludes_credentials() -> None:
    agent = semantic_id("agent", "Worker")
    secret = "super-secret-api-key"
    record = configuration_evidence(
        agent,
        "agent.model_settings.extra_body",
        {"api_key": secret, "nested": {"enabled": True}},
        {"api_key": secret, "nested": {"enabled": True}},
        safe=False,
    )

    serialized = json.dumps(record.to_dict(), sort_keys=True)
    assert secret not in serialized
    assert record.planned_redacted is True
    assert record.planned_digest == record.observed_digest
    assert ConfigurationConformanceEvidence.from_dict(record.to_dict()) == record


def test_graph_completeness_requires_configuration_coverage_and_passes_round_trip() -> None:
    complete = _graph(_complete_configuration())
    assert complete.complete
    assert GraphValidationEvidence.from_dict(complete.to_dict()) == complete

    missing = _complete_configuration()[:-1]
    assert not _graph(missing).complete


@pytest.mark.parametrize(
    ("observed", "status"),
    ((True, "violated"), (MISSING, "unverified")),
)
def test_required_additional_configuration_record_blocks_completeness(observed: object, status: str) -> None:
    agent = semantic_id("agent", "Worker")
    extra = configuration_evidence(
        agent,
        "agent.model_settings.store",
        False,
        observed,
        required=True,
    )

    assert extra.status == status
    assert not _graph(_complete_configuration() + (extra,)).complete


def test_graph_rejects_unknown_and_duplicate_configuration_records() -> None:
    agent = semantic_id("agent", "Worker")
    unknown = configuration_evidence(semantic_id("agent", "Other"), "agent.name", "Other", "Other")
    with pytest.raises(ValueError, match="unknown semantic ID"):
        _graph((unknown,))
    record = configuration_evidence(agent, "agent.name", "Worker", "Worker")
    with pytest.raises(ValueError, match="duplicate property"):
        _graph((record, record))


def test_configuration_evidence_validation_is_strict_and_deterministic() -> None:
    agent = semantic_id("agent", "Worker")
    passed = configuration_evidence(agent, "agent.model", "model", "model")
    payload = passed.to_dict()

    assert passed.to_json() == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert ConfigurationConformanceEvidence.from_json(passed.to_json()) == passed

    invalid_payloads = (
        (None, TypeError, "must be an object"),
        ({**payload, "unexpected": True}, ValueError, "unexpected keys"),
        ({**payload, "planned_present": "yes"}, TypeError, "presence flags"),
        ({**payload, "required": "yes"}, TypeError, "required flag"),
        ({**payload, "reason": 1}, TypeError, "reason"),
    )
    for invalid, error, message in invalid_payloads:
        with pytest.raises(error, match=message):
            ConfigurationConformanceEvidence.from_dict(invalid)

    with pytest.raises(ValueError, match="Invalid configuration conformance evidence JSON"):
        ConfigurationConformanceEvidence.from_json("not-json")
    with pytest.raises(ValueError, match="status is invalid"):
        ConfigurationConformanceEvidence(agent, "agent.model", "model", "model", status=cast(Any, "bad"))
    with pytest.raises(ValueError, match="source is invalid"):
        ConfigurationConformanceEvidence(
            agent,
            "agent.model",
            "model",
            "model",
            status="passed",
            observation_source=cast(Any, "cache"),
        )
    with pytest.raises(ValueError, match="property path"):
        ConfigurationConformanceEvidence(agent, " agent.model", "model", "model", status="passed")
    with pytest.raises(TypeError, match="semantic ID"):
        ConfigurationConformanceEvidence(cast(Any, "agent:Worker"), "agent.model", "model", "model")
    with pytest.raises(TypeError, match="property path"):
        ConfigurationConformanceEvidence(agent, cast(Any, 1), "model", "model")
    with pytest.raises(TypeError, match="required flag"):
        ConfigurationConformanceEvidence(agent, "agent.model", "model", "model", required=cast(Any, 1))
    with pytest.raises(TypeError, match="presence flags"):
        ConfigurationConformanceEvidence(
            agent,
            "agent.model",
            "model",
            "model",
            planned_present=cast(Any, 1),
        )
    with pytest.raises(TypeError, match="redaction flags"):
        ConfigurationConformanceEvidence(
            agent,
            "agent.model",
            "model",
            "model",
            planned_redacted=cast(Any, 1),
        )
    with pytest.raises(ValueError, match="non-empty"):
        ConfigurationConformanceEvidence(agent, "agent.model", "model", "model", reason=" ")
    with pytest.raises(ValueError, match="digest is invalid"):
        ConfigurationConformanceEvidence(
            agent,
            "agent.model",
            "model",
            "model",
            planned_digest="bad",
        )
    with pytest.raises(ValueError, match="digest is invalid"):
        ConfigurationConformanceEvidence(
            agent,
            "agent.model",
            "model",
            "model",
            observed_digest="bad",
        )
    digest = "sha256:" + ("0" * 64)
    with pytest.raises(ValueError, match="digest is inconsistent"):
        ConfigurationConformanceEvidence(agent, "agent.model", "model", "model", planned_digest=digest)
    with pytest.raises(ValueError, match="digest is inconsistent"):
        ConfigurationConformanceEvidence(agent, "agent.model", "model", "model", observed_digest=digest)
    with pytest.raises(ValueError, match="Passed configuration evidence"):
        ConfigurationConformanceEvidence(
            agent,
            "agent.model",
            "model",
            None,
            status="passed",
            observed_present=False,
        )
    with pytest.raises(ValueError, match="Violated configuration evidence"):
        ConfigurationConformanceEvidence(
            agent,
            "agent.model",
            "model",
            "model",
            status="violated",
        )


def test_graph_evidence_rejects_malformed_and_inconsistent_json() -> None:
    graph = _graph(_complete_configuration())
    payload = graph.to_dict()
    with pytest.raises(TypeError, match="must be an object"):
        GraphValidationEvidence.from_dict(None)
    with pytest.raises(ValueError, match="unexpected keys"):
        GraphValidationEvidence.from_dict({**payload, "unexpected": True})
    with pytest.raises(TypeError, match="must be an array"):
        GraphValidationEvidence.from_dict({**payload, "agent_ids": None})
    with pytest.raises(TypeError, match="entries must be strings"):
        GraphValidationEvidence.from_dict({**payload, "agent_ids": [1]})
    with pytest.raises(ValueError, match="inconsistent"):
        GraphValidationEvidence.from_dict({**payload, "complete": False})


def test_configuration_helpers_read_public_values_and_digest_unsafe_values() -> None:
    class Public:
        visible = {"nested": 1}

    public = Public()
    assert flatten_mapping({"nested": {"value": 1}, "flag": False}) == (
        ("flag", False),
        ("nested.value", 1),
    )
    assert read_public_path(public, "visible.nested") == 1
    assert read_public_path({"visible": 2}, "visible") == 2
    assert read_public_path(public, "missing") is MISSING
    assert read_public_path(public, "_private") is MISSING
    assert read_public_path(public, "") is MISSING

    record = digest_only_configuration_evidence(
        semantic_id("agent", "Worker"),
        "agent.model_options.extra",
        {"secret": "value"},
        {"secret": "value"},
    )
    assert record.status == "passed"
    assert record.planned_value is None
