from __future__ import annotations

from dataclasses import replace

from contract4agents.assurance import (
    SemanticDiff,
    diff_contracts,
    diff_materialization_plans,
    semantic_diff,
)
from contract4agents.ir import (
    CanonicalIR,
    ContextRequirementIR,
    ControlIR,
    EvalIR,
    FrozenMap,
    IsolationProfileIR,
    QualityIR,
    TypeFieldIR,
    TypeIR,
    parse_type_ref,
    semantic_id,
)
from contract4agents.planning import (
    AgentPlan,
    IsolationDimensionPlan,
    IsolationMappingPlan,
)
from tests.unit.test_assurance_bundle_diff import _small_ir
from tests.unit.test_eval_campaigns import _ir, _plan


def test_contract_diff_covers_removals_optional_fields_context_controls_and_named_coverage() -> None:
    base = _small_ir(authorization="approval_required", extra_field=False, include_grant=True)
    agent_id = semantic_id("agent", "Worker")
    context = ContextRequirementIR(
        semantic_id("context", "Worker", "request"),
        agent_id,
        "request",
        parse_type_ref("Request"),
        "invocation",
    )
    isolation = IsolationProfileIR(
        semantic_id("isolation", "Clean"),
        "Clean",
        context="explicit_only",
        network="denied",
    )
    control = ControlIR(
        semantic_id("control", "Worker", "safe"),
        "safe",
        agent_id,
        "high",
        True,
        ("evaluator",),
        "runtime",
        requirement="trace.not_called(danger)",
    )
    quality = QualityIR(semantic_id("quality", "Worker", "clear"), "clear", agent_id, "Be clear.")
    evaluation = EvalIR(semantic_id("eval", "Worker", "case"), "case", agent_id)
    before = CanonicalIR.create(
        types=base.types.values(),
        capabilities=base.capabilities.values(),
        agents=base.agents.values(),
        grants=base.grants.values(),
        contexts=(context,),
        isolation_profiles=(isolation,),
        controls=(control,),
        qualities=(quality,),
        evals=(evaluation,),
    )
    request = TypeIR(
        semantic_id("type", "Request"),
        "Request",
        (
            TypeFieldIR("value", parse_type_ref("integer")),
            TypeFieldIR("note", parse_type_ref("string?")),
        ),
    )
    after = CanonicalIR.create(
        types=(request,),
        capabilities=base.capabilities.values(),
        agents=base.agents.values(),
    )

    changes = diff_contracts(before, after)
    areas = {item.area for item in changes}

    assert areas >= {
        "approval",
        "capability_access",
        "context_exposure",
        "eval_coverage",
        "isolation",
        "quality",
        "schema",
    }
    assert any(item.summary == "Type removed." and item.impact == "breaking" for item in changes)
    assert any("Optional/defaulted" in item.summary for item in changes)
    assert any(item.area == "approval" and item.impact == "security_critical" for item in changes)


def test_diff_objects_and_plan_outcomes_report_worsening_and_improvement() -> None:
    ir = _ir()
    before = _plan(ir)
    agent_id = semantic_id("agent", "SupportAgent")
    iso_id = semantic_id("isolation", "Clean")
    before = replace(
        before,
        agents=FrozenMap(
            {
                agent_id: AgentPlan(agent_id, "SupportAgent", "old", FrozenMap(), parse_type_ref("Result"))
            }
        ),
        isolation=FrozenMap(
            {
                iso_id: IsolationMappingPlan(
                    iso_id,
                    "in_process",
                    "test",
                    FrozenMap({"network": IsolationDimensionPlan("denied", "exact", "sandbox")}),
                )
            }
        ),
    )
    grant_id = semantic_id("grant", "SupportAgent", "status.publish")
    after = replace(
        before,
        agents=FrozenMap(
            {
                agent_id: AgentPlan(agent_id, "SupportAgent", "new", FrozenMap(), parse_type_ref("Result"))
            }
        ),
        grants=FrozenMap({grant_id: replace(before.grants[grant_id], outcome="degraded")}),
        isolation=FrozenMap(
            {
                iso_id: IsolationMappingPlan(
                    iso_id,
                    "in_process",
                    "test",
                    FrozenMap({"network": IsolationDimensionPlan("denied", "unsupported", None)}),
                )
            }
        ),
    )

    plan_changes = diff_materialization_plans(before, after)
    combined = semantic_diff(ir, ir, before, after)

    assert {item.area for item in plan_changes} == {"model", "enforcement", "isolation"}
    assert sum(item.impact == "security_critical" for item in plan_changes) == 2
    assert isinstance(combined, SemanticDiff)
    assert combined.has_breaking_changes
    assert combined.to_dict()["has_breaking_changes"] is True
    assert '"security_critical"' in combined.to_json()
