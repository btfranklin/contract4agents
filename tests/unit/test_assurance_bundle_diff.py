from __future__ import annotations

from pathlib import Path
from typing import cast

from contract4agents.adapters.openai import openai_planner_capabilities
from contract4agents.assurance import (
    AssessorIdentity,
    ControlResult,
    assemble_assurance_bundle,
    diff_contracts,
    verify_assurance_bundle,
    write_assurance_bundle,
)
from contract4agents.ir import (
    AgentIR,
    Authorization,
    CanonicalIR,
    CapabilityIR,
    GrantIR,
    ParameterIR,
    TypeFieldIR,
    TypeIR,
    build_canonical_ir,
    parse_type_ref,
    semantic_id,
)
from contract4agents.parser import parse_project
from contract4agents.planning import plan_materialization
from contract4agents.target_bindings import load_target_bindings


def test_contract_diff_flags_new_access_weakened_approval_and_breaking_schema() -> None:
    before = _small_ir(authorization="approval_required", extra_field=False, include_grant=True)
    after = _small_ir(authorization="preapproved", extra_field=True, include_grant=True)

    changes = diff_contracts(before, after)

    authorization = next(item for item in changes if item.area == "authorization")
    schema = next(item for item in changes if item.area == "schema")
    assert authorization.impact == "security_critical"
    assert schema.impact == "breaking"
    assert schema.semantic_id == "type:Result:required_new_field"

    no_access = _small_ir(authorization="preapproved", extra_field=False, include_grant=False)
    access_added = diff_contracts(no_access, after)
    grant = next(item for item in access_added if item.area == "capability_access")
    assert grant.change == "added"
    assert grant.impact == "security_critical"


def test_assurance_bundle_is_deterministic_verified_and_explicit_about_missing_evidence(
    tmp_path: Path,
) -> None:
    root = Path("examples/incident-command")
    ir = build_canonical_ir(parse_project(root))
    loaded = load_target_bindings(root, required=True)
    assert loaded.bindings is not None
    plan = plan_materialization(
        ir,
        loaded.bindings,
        target="openai",
        profile="test",
        capabilities=openai_planner_capabilities(),
    )
    result = ControlResult(
        control_id="control:IncidentCommander:output_conformance",
        status="passed",
        reason="Output matched the canonical schema.",
        assessment="adapter",
        assessor=AssessorIdentity("contract4agents", "1"),
        evidence_event_ids=("evt-000001",),
    )

    first = assemble_assurance_bundle(
        ir,
        plan,
        normalized_trace_jsonl='{"schema_version":"1"}\n',
        control_results=(result,),
        eval_results={"campaigns": []},
        provenance={"sources": ["test"]},
    )
    second = assemble_assurance_bundle(
        ir,
        plan,
        normalized_trace_jsonl='{"schema_version":"1"}\n',
        control_results=(result,),
        eval_results={"campaigns": []},
        provenance={"sources": ["test"]},
    )

    assert first.files == second.files
    assert first.complete
    assert verify_assurance_bundle(first) == ()
    written = write_assurance_bundle(first, tmp_path / "bundle")
    assert {path.name for path in written} >= {"attestation.json", "summary.html"}

    incomplete = assemble_assurance_bundle(
        ir,
        plan,
        normalized_trace_jsonl=None,
        control_results=None,
        eval_results=None,
        provenance=None,
    )
    assert not incomplete.complete
    assert {item.code for item in incomplete.diagnostics} == {
        "BUNDLE001",
        "BUNDLE002",
        "BUNDLE003",
        "BUNDLE004",
    }
    assert '"status": "unverified"' in incomplete.files["control-results.json"]


def _small_ir(*, authorization: str, extra_field: bool, include_grant: bool) -> CanonicalIR:
    request = TypeIR(
        semantic_id("type", "Request"),
        "Request",
        (TypeFieldIR("value", parse_type_ref("string")),),
    )
    fields = [TypeFieldIR("value", parse_type_ref("string"))]
    if extra_field:
        fields.append(TypeFieldIR("required_new_field", parse_type_ref("string")))
    result = TypeIR(semantic_id("type", "Result"), "Result", tuple(fields))
    tool = CapabilityIR(
        semantic_id("tool", "lookup"),
        "lookup",
        "tool",
        (ParameterIR("request", parse_type_ref("Request")),),
        parse_type_ref("Result"),
        "Look up a value.",
        side_effect=False,
    )
    grant = GrantIR(
        semantic_id("grant", "Worker", "lookup"),
        semantic_id("agent", "Worker"),
        tool.id,
        "enabled",
        authorization=cast(Authorization, authorization),
        execution="host",
    )
    agent = AgentIR(
        semantic_id("agent", "Worker"),
        "Worker",
        (ParameterIR("request", parse_type_ref("Request")),),
        parse_type_ref("Result"),
        "Return a value.",
        grant_ids=(grant.id,) if include_grant else (),
    )
    return CanonicalIR.create(
        types=(request, result),
        capabilities=(tool,),
        agents=(agent,),
        grants=(grant,) if include_grant else (),
    )
