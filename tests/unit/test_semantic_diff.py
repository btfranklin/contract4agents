from __future__ import annotations

import json
from dataclasses import replace

import pytest

from contract4agents.assurance import (
    SemanticDiff,
    diff_contracts,
    diff_materialization_plans,
    semantic_diff,
)
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    ContextRequirementIR,
    ControlIR,
    EnumIR,
    EvalIR,
    FrozenMap,
    IsolationProfileIR,
    ParameterIR,
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
                agent_id: AgentPlan(agent_id, "SupportAgent", "old", FrozenMap(), parse_type_ref("Result"), ())
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
                agent_id: AgentPlan(agent_id, "SupportAgent", "new", FrozenMap(), parse_type_ref("Result"), ())
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


def test_contract_diff_reports_agent_input_signature_changes() -> None:
    before = _small_ir(authorization="approval_required", extra_field=False, include_grant=True)
    agent_id = semantic_id("agent", "Worker")
    old_agent = before.agents[agent_id]
    assert isinstance(old_agent, AgentIR)
    new_agent = replace(
        old_agent,
        parameters=(
            *old_agent.parameters,
            ParameterIR("locale", parse_type_ref("string")),
        ),
    )
    after = replace(
        before,
        agents=FrozenMap(
            (identifier, new_agent if identifier == agent_id else agent)
            for identifier, agent in before.agents.items()
        ),
    )

    changes = diff_contracts(before, after)

    assert any(
        item.semantic_id == "agent:Worker:input:locale"
        and item.summary == "Required agent input added."
        and item.impact == "breaking"
        for item in changes
    )


@pytest.mark.parametrize(
    ("type_name", "before_default", "after_default"),
    [
        ("string", "old", "new"),
        ("list[string]", ["old"], ["new"]),
        ("map[string,string]", {"z": "old", "a": "first"}, {"y": "new"}),
        (
            "map[string,list[map[string,string]]]",
            {"items": [{"z": "old", "a": "first"}]},
            {"items": [{"y": "new"}]},
        ),
        ("string?", None, "present"),
        ("Status", "draft", "final"),
    ],
)
def test_semantic_diff_serializes_every_portable_default(
    type_name: str,
    before_default: object,
    after_default: object,
) -> None:
    agent_id = semantic_id("agent", "Worker")
    types = (EnumIR(semantic_id("type", "Status"), "Status", ("draft", "final")),)

    def contract(default: object) -> CanonicalIR:
        return CanonicalIR.create(
            types=types,
            agents=(
                AgentIR(
                    agent_id,
                    "Worker",
                    (
                        ParameterIR(
                            "value",
                            parse_type_ref(type_name),
                            required=False,
                            has_default=True,
                            default=default,
                        ),
                    ),
                    parse_type_ref("Status"),
                    "Report the value.",
                ),
            ),
        )

    result = semantic_diff(contract(before_default), contract(after_default))
    first = result.to_json()
    second = result.to_json()
    payload = json.loads(first)
    change = payload["contract_changes"][0]

    assert change["before"] == before_default
    assert change["after"] == after_default
    assert first == second
    if isinstance(before_default, dict):
        assert first.index('"a"') < first.index('"z"')


@pytest.mark.parametrize(
    ("before_type", "after_type"),
    [
        ("list[string]", "list[string](min_items=1)"),
        ("list[string](min_items=1)", "list[string](min_items=2)"),
        ("list[string](max_items=20)", "list[string](max_items=10)"),
    ],
)
def test_list_cardinality_additions_and_tightenings_are_breaking(
    before_type: str,
    after_type: str,
) -> None:
    before = CanonicalIR.create(
        types=(
            TypeIR(
                semantic_id("type", "Result"),
                "Result",
                (TypeFieldIR("items", parse_type_ref(before_type)),),
            ),
        )
    )
    after = CanonicalIR.create(
        types=(
            TypeIR(
                semantic_id("type", "Result"),
                "Result",
                (TypeFieldIR("items", parse_type_ref(after_type)),),
            ),
        )
    )

    changes = diff_contracts(before, after)

    assert len(changes) == 1
    assert changes[0].impact == "breaking"
    assert changes[0].before == before_type
    assert changes[0].after == after_type
