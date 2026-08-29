from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

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
    EnumIR,
    GrantIR,
    ParameterIR,
    TypeFieldIR,
    TypeIR,
    build_canonical_ir,
    parse_type_ref,
    semantic_id,
)
from contract4agents.materialization import (
    ConfigurationConformanceEvidence,
    GraphValidationEvidence,
    SchemaConformanceEvidence,
)
from contract4agents.materialization._configuration import MISSING, configuration_evidence
from contract4agents.parser import parse_project
from contract4agents.planning import MaterializationPlan, plan_materialization
from contract4agents.target_bindings import load_target_bindings
from contract4agents.tracing import (
    NormalizedTrace,
    ProviderCorrelation,
    TraceAttempt,
    TraceAttemptClosure,
    TraceClosureEvidence,
    TraceEvent,
    TraceFrontier,
    TraceRunContext,
    TraceSemanticRefs,
    dumps_trace_jsonl,
)


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


def test_contract_diff_classifies_enum_membership_changes() -> None:
    before = CanonicalIR.create(
        types=(EnumIR(semantic_id("type", "Status"), "Status", ("accepted", "failed")),)
    )
    after = CanonicalIR.create(
        types=(EnumIR(semantic_id("type", "Status"), "Status", ("accepted", "follow_up")),)
    )

    changes = diff_contracts(before, after)

    assert [(item.change, item.impact, item.semantic_id) for item in changes] == [
        ("removed", "breaking", "type:Status:failed"),
        ("added", "review", "type:Status:follow_up"),
    ]


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
    results = _control_results(ir)

    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    context = TraceRunContext("run-1", "run-1", plan.contract_digest, plan.plan_digest)
    trace = NormalizedTrace(
        (
            TraceEvent(
                context,
                "evt-000001",
                None,
                "output.accepted",
                1,
                TraceSemanticRefs(agent_id=semantic_id("agent", "IncidentCommander")),
                data={"attempt": attempt.to_dict()},
                provider=ProviderCorrelation("test"),
            ),
        )
    )
    closure = TraceClosureEvidence(
        context,
        "complete",
        "The test fixture covers the run.",
        TraceFrontier.from_trace(trace),
        ("output",),
        (
            TraceAttemptClosure(
                attempt,
                semantic_id("agent", "IncidentCommander"),
                "complete",
                "complete",
                evidence_refs=("fixture:attempt",),
            ),
        ),
        ("fixture:closure",),
    )
    materialization_evidence = _materialization_evidence(ir, plan)
    first = assemble_assurance_bundle(
        ir,
        plan,
        normalized_trace_jsonl=dumps_trace_jsonl(trace),
        trace_closures=(closure,),
        control_results=results,
        eval_results={"campaigns": []},
        provenance={"sources": ["test"]},
        materialization_evidence=materialization_evidence,
    )
    second = assemble_assurance_bundle(
        ir,
        plan,
        normalized_trace_jsonl=dumps_trace_jsonl(trace),
        trace_closures=(closure,),
        control_results=results,
        eval_results={"campaigns": []},
        provenance={"sources": ["test"]},
        materialization_evidence=materialization_evidence,
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
        "BUNDLE016",
    }
    assert '"status": "unverified"' in incomplete.files["control-results.json"]

    for supplied in ((), results[:1]):
        incomplete_inventory = assemble_assurance_bundle(
            ir,
            plan,
            normalized_trace_jsonl=dumps_trace_jsonl(trace),
            trace_closures=(closure,),
            control_results=supplied,
            eval_results={"campaigns": []},
            provenance={"sources": ["test"]},
            materialization_evidence=materialization_evidence,
        )
        diagnostic = next(
            item for item in incomplete_inventory.diagnostics if item.code == "BUNDLE018"
        )
        missing = sorted(
            str(item.id) for item in ir.controls.values() if str(item.id) not in {
                result.control_id for result in supplied
            }
        )
        assert diagnostic.message.endswith(f"{', '.join(missing)}.")
        assert not incomplete_inventory.complete
        assert json.loads(incomplete_inventory.files["control-results.json"])["status"] == "unverified"
        assert json.loads(incomplete_inventory.files["attestation.json"])["complete"] is False

    with pytest.raises(ValueError, match="must have unique IDs"):
        assemble_assurance_bundle(
            ir,
            plan,
            normalized_trace_jsonl=dumps_trace_jsonl(trace),
            trace_closures=(closure,),
            control_results=(results[0], results[0]),
            eval_results={"campaigns": []},
            provenance={"sources": ["test"]},
            materialization_evidence=materialization_evidence,
        )

    unknown = replace(results[0], control_id="control:Undeclared")
    with pytest.raises(ValueError, match="Undeclared IDs: control:Undeclared"):
        assemble_assurance_bundle(
            ir,
            plan,
            normalized_trace_jsonl=dumps_trace_jsonl(trace),
            trace_closures=(closure,),
            control_results=(unknown, *results[1:]),
            eval_results={"campaigns": []},
            provenance={"sources": ["test"]},
            materialization_evidence=materialization_evidence,
        )

    with pytest.raises(ValueError, match="Missing IDs:"):
        assemble_assurance_bundle(
            ir,
            plan,
            normalized_trace_jsonl=dumps_trace_jsonl(trace),
            trace_closures=(closure,),
            control_results=(unknown,),
            eval_results={"campaigns": []},
            provenance={"sources": ["test"]},
            materialization_evidence=materialization_evidence,
        )


@pytest.mark.parametrize(
    ("observed", "status"),
    ((True, "violated"), (MISSING, "unverified")),
)
def test_required_additional_configuration_record_emits_bundle017(
    observed: object,
    status: str,
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
    evidence = _materialization_evidence(ir, plan)
    agent_id = next(iter(plan.agents))
    extra = configuration_evidence(
        agent_id,
        "agent.model_settings.store",
        False,
        observed,
        required=True,
    )
    assert extra.status == status
    evidence = replace(
        evidence,
        configuration_conformance=evidence.configuration_conformance + (extra,),
    )

    assert not evidence.complete
    bundle = assemble_assurance_bundle(
        ir,
        plan,
        normalized_trace_jsonl=None,
        control_results=None,
        eval_results=None,
        provenance=None,
        materialization_evidence=evidence,
    )

    diagnostic = next(item for item in bundle.diagnostics if item.code == "BUNDLE017")
    assert f"{agent_id}:agent.model_settings.store ({status})" in diagnostic.message


def _materialization_evidence(ir: CanonicalIR, plan: MaterializationPlan) -> GraphValidationEvidence:
    schema = {"additionalProperties": False, "properties": {}, "type": "object"}
    checks = [
        SchemaConformanceEvidence(agent_id, "agent_output", schema, schema)
        for agent_id in ir.agents
    ]
    checks.extend(
        SchemaConformanceEvidence(grant.id, "tool_input", schema, schema)
        for grant in ir.grants.values()
        if grant.availability == "enabled"
        and grant.capability_id.kind == "tool"
        and plan.bindings[grant.capability_id].execution == "host"
    )
    checks.extend(
        SchemaConformanceEvidence(edge.id, "delegate_input", schema, schema)
        for edge in ir.composition.values()
        if edge.mode == "delegate" and ir.agents[edge.target_agent_id].parameters
    )
    configuration = tuple(
        ConfigurationConformanceEvidence(agent_id, property_path, status="passed")
        for agent_id in plan.agents
        for property_path in (
            "agent.name",
            "agent.identity",
            "agent.model",
            "agent.model_options",
            "agent.output_type",
            "agent.output_mode",
            "agent.tools",
            "agent.handoffs",
        )
    ) + tuple(
        ConfigurationConformanceEvidence(grant_id, property_path, status="passed")
        for grant_id in plan.grants
        for property_path in ("grant.identity", "grant.approval")
    ) + tuple(
        ConfigurationConformanceEvidence(edge_id, property_path, status="passed")
        for edge_id in plan.composition
        for property_path in ("edge.identity", "edge.schema")
    )
    return GraphValidationEvidence(
        adapter=plan.adapter.name,
        adapter_version=plan.adapter.version,
        contract_digest=plan.contract_digest,
        plan_digest=plan.plan_digest,
        agent_ids=tuple(plan.agents),
        grant_ids=tuple(plan.grants),
        composition_ids=tuple(plan.composition),
        schema_conformance=tuple(checks),
        configuration_conformance=configuration,
    )


def _control_results(ir: CanonicalIR) -> tuple[ControlResult, ...]:
    return tuple(
        ControlResult(
            control_id=str(control.id),
            status="passed",
            reason="The fixture supplies complete control evidence.",
            assessment="adapter",
            assessor=AssessorIdentity("contract4agents", "1"),
            evidence_event_ids=("evt-000001",),
        )
        for control in ir.controls.values()
    )


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
