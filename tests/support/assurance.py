"""Shared contract and plan builders for assurance test families."""

from __future__ import annotations

from typing import cast

from contract4agents.ir import (
    AgentIR,
    Authorization,
    CanonicalIR,
    CapabilityIR,
    ControlIR,
    EvalIR,
    FrozenMap,
    GrantIR,
    ParameterIR,
    QualityIR,
    TypeFieldIR,
    TypeIR,
    contract_digest,
    parse_type_ref,
    semantic_id,
)
from contract4agents.planning import (
    AdapterPlan,
    AgentPlan,
    BindingPlan,
    ControlMappingPlan,
    GrantMappingPlan,
    MaterializationPlan,
)


def campaign_ir(
    *,
    missing_judge: bool = False,
    negative_expectation: bool = False,
    hidden_truth_expectation: bool = False,
) -> CanonicalIR:
    agent_id = semantic_id("agent", "SupportAgent")
    capability_id = semantic_id("tool", "status.publish")
    grant_id = semantic_id("grant", "SupportAgent", "status.publish")
    quality_id = semantic_id("quality", "SupportAgent", "useful")
    expectations = (
        ('output.status == "ok"', "trace.not_called(other.tool)")
        if negative_expectation
        else (
            'output.status == "ok"',
            "output conforms Result",
            "trace.approval_granted(status.publish)",
            "trace.tool_called(status.publish)",
        )
    )
    if hidden_truth_expectation:
        expectations = (*expectations, "output discovers hidden_truth.expected_message")
    return CanonicalIR.create(
        types=(
            TypeIR(
                semantic_id("type", "Result"),
                "Result",
                (
                    TypeFieldIR("status", parse_type_ref("string")),
                    TypeFieldIR("message", parse_type_ref("string")),
                ),
            ),
        ),
        capabilities=(
            CapabilityIR(
                capability_id,
                "status.publish",
                "tool",
                (),
                parse_type_ref("Result"),
                "Publish a status update.",
                side_effect=True,
            ),
        ),
        agents=(
            AgentIR(
                agent_id,
                "SupportAgent",
                (),
                parse_type_ref("Result"),
                "Resolve the incident.",
                grant_ids=(grant_id,),
            ),
        ),
        grants=(
            GrantIR(
                grant_id,
                agent_id,
                capability_id,
                "enabled",
                "approval_required",
                "host",
            ),
        ),
        controls=(
            ControlIR(
                semantic_id("control", "SupportAgent", "approval", "status.publish"),
                "approval_required_status_publish",
                agent_id,
                "high",
                True,
                ("evaluator", "reviewer"),
                "runtime",
                derived_from=grant_id,
                expected_evidence=("approval.requested", "approval.completed", "tool.started"),
            ),
            ControlIR(
                semantic_id("control", "SupportAgent", "output_conformance"),
                "output_conformance",
                agent_id,
                "high",
                True,
                ("evaluator", "reviewer"),
                "adapter",
                derived_from=agent_id,
                expected_evidence=("output.accepted", "output.schema_failed"),
            ),
        ),
        qualities=(QualityIR(quality_id, "useful", agent_id, "The response is useful."),),
        evals=(
            EvalIR(
                semantic_id("eval", "SupportAgent", "publishes_status"),
                "publishes_status",
                agent_id,
                FrozenMap({"prompt": "Publish an update."}),
                expectations,
                () if missing_judge else (quality_id,),
            ),
        ),
    )


def campaign_plan(
    ir: CanonicalIR,
    *,
    expected_event_types: tuple[str, ...] | None = None,
) -> MaterializationPlan:
    agent_id = semantic_id("agent", "SupportAgent")
    capability_id = semantic_id("tool", "status.publish")
    grant_id = semantic_id("grant", "SupportAgent", "status.publish")
    telemetry = expected_event_types or (
        "approval.requested",
        "approval.completed",
        "tool.started",
        "tool.completed",
        "output.accepted",
    )
    return MaterializationPlan(
        contract_digest=contract_digest(ir),
        target="file",
        profile="test",
        adapter=AdapterPlan("file", "1"),
        agents=FrozenMap(
            {
                agent_id: AgentPlan(
                    agent_id,
                    "SupportAgent",
                    "deterministic",
                    FrozenMap(),
                    parse_type_ref("Result"),
                    (),
                )
            }
        ),
        bindings=FrozenMap(
            {
                capability_id: BindingPlan(
                    capability_id,
                    "tool",
                    FrozenMap({"provider": "file"}),
                    "exact",
                    "file.fixture",
                    "host",
                )
            }
        ),
        grants=FrozenMap(
            {
                grant_id: GrantMappingPlan(
                    grant_id,
                    agent_id,
                    capability_id,
                    "enabled",
                    "approval_required",
                    "host",
                    None,
                    "exact",
                    "file.approval",
                )
            }
        ),
        composition=FrozenMap(),
        controls=FrozenMap(
            {
                control.id: ControlMappingPlan(
                    control.id,
                    control.required,
                    control.assessment,
                    "exact",
                    "file.trace",
                    control.expected_evidence,
                )
                for control in ir.controls.values()
            }
        ),
        isolation=FrozenMap(),
        host_obligations=(),
        expected_event_types=telemetry,
        artifact_digests=FrozenMap(),
    )


def small_diff_ir(
    *,
    authorization: str,
    extra_field: bool,
    include_grant: bool,
) -> CanonicalIR:
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


__all__ = ["campaign_ir", "campaign_plan", "small_diff_ir"]
