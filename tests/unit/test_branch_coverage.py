from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from contract4agents.adapters._openai_names import contract_tool_name, openai_tool_name
from contract4agents.assurance import (
    SemanticDiff,
    assess_controls,
    diff_contracts,
    diff_materialization_plans,
    semantic_diff,
)
from contract4agents.compiler import artifact_digests, build_artifacts
from contract4agents.eval_campaigns._expectations import assess_expectation
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    ContextRequirementIR,
    ControlIR,
    EvalIR,
    FrozenMap,
    GuidanceIR,
    IsolationProfileIR,
    ParameterIR,
    QualityIR,
    TypeFieldIR,
    TypeIR,
    contract_digest,
    parse_type_ref,
    semantic_id,
)
from contract4agents.materialization import MaterializationError
from contract4agents.materialization._types import (
    build_parameter_model,
    build_pydantic_types,
    output_type_for,
)
from contract4agents.planning import (
    AgentPlan,
    ControlMappingPlan,
    IsolationDimensionPlan,
    IsolationMappingPlan,
)
from contract4agents.tracing import (
    NormalizedTrace,
    ProviderCorrelation,
    TraceAttempt,
    TraceAttemptClosure,
    TraceClosureEvidence,
    TraceCompletenessResult,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
)
from tests.unit.test_assurance_bundle_diff import _small_ir
from tests.unit.test_eval_campaigns import _ir, _plan

_CONTRACT_DIGEST = f"sha256:{'a' * 64}"
_PLAN_DIGEST = f"sha256:{'b' * 64}"


def _event(
    event_id: str,
    event_type: str,
    *,
    timestamp: float = 1,
    agent: str | None = "SupportAgent",
    capability: str | None = "status.publish",
    grant: str | None = "SupportAgent:status.publish",
    controls: tuple[str, ...] = (),
    data: dict[str, object] | None = None,
) -> TraceEvent:
    return TraceEvent(
        TraceRunContext("run-1", "thread-1", _CONTRACT_DIGEST, _PLAN_DIGEST),
        event_id,
        None,
        event_type,
        timestamp,
        TraceSemanticRefs(
            semantic_id("agent", agent) if agent else None,
            semantic_id("tool", capability) if capability else None,
            semantic_id("grant", *grant.split(":")) if grant else None,
            tuple(semantic_id("control", *name.split(":")) for name in controls),
        ),
        data or {},
        ProviderCorrelation("test"),
    )


def _completeness(complete: bool) -> TraceCompletenessResult:
    return TraceCompletenessResult(
        "run-1",
        "complete" if complete else "unverified",
        "complete" if complete else "incomplete",
        closure_digest=f"sha256:{'c' * 64}" if complete else None,
        covered_channels=(
            "agent",
            "approval",
            "composition",
            "datasource",
            "guardrail",
            "handoff",
            "output",
            "provider_response",
            "tool",
        )
        if complete
        else (),
    )


def _conforming_trace(
    ir: CanonicalIR,
    plan: object,
    events: tuple[TraceEvent, ...],
) -> NormalizedTrace:
    from contract4agents.planning import MaterializationPlan

    assert isinstance(plan, MaterializationPlan)
    context = TraceRunContext(
        "run-1",
        "thread-1",
        contract_digest(ir),
        plan.plan_digest,
    )
    return NormalizedTrace(tuple(replace(event, context=context) for event in events))


@pytest.mark.parametrize(
    ("expression", "output", "status"),
    [
        ('output.status == "ok"', {"status": "ok"}, "passed"),
        ('output.status != "ok"', {"status": "ok"}, "violated"),
        ('output.message contains ready', {"message": "system ready"}, "passed"),
        ('output.message excludes secret', {"message": "secret leaked"}, "violated"),
        ('output.missing == "x"', {}, "violated"),
    ],
)
def test_eval_output_expectation_branches(
    expression: str,
    output: dict[str, object],
    status: str,
) -> None:
    result = assess_expectation(
        expression,
        ir=_ir(),
        output=output,
        trace=NormalizedTrace((_event("evt-1", "run.started"),)),
        trace_completeness=_completeness(True),
        schemas={},
        hidden_truth={},
    )

    assert result.status == status


def test_eval_schema_hidden_truth_and_invalid_expression_branches() -> None:
    trace = NormalizedTrace((_event("evt-1", "run.started"),))
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    }
    common = {"trace": trace, "trace_completeness": _completeness(True), "ir": _ir()}

    assert assess_expectation(
        "output conforms Result",
        output={"status": "ok"},
        schemas={"Result": schema},
        hidden_truth={},
        **common,
    ).status == "passed"
    assert assess_expectation(
        "output conforms Result",
        output={"status": 3},
        schemas={"Result": schema},
        hidden_truth={},
        **common,
    ).status == "violated"
    assert assess_expectation(
        "output conforms Missing",
        output={},
        schemas={},
        hidden_truth={},
        **common,
    ).status == "unverified"
    assert assess_expectation(
        "output discovers hidden_truth.answer",
        output={"message": "alpha and beta"},
        schemas={},
        hidden_truth={"answer": {"contains_all": ["alpha", "beta"]}},
        **common,
    ).status == "passed"
    assert assess_expectation(
        "output discovers hidden_truth.answer",
        output={"message": "alpha"},
        schemas={},
        hidden_truth={"answer": {"contains_any": ["beta", "gamma"]}},
        **common,
    ).status == "violated"
    assert assess_expectation(
        "output discovers hidden_truth.missing",
        output={},
        schemas={},
        hidden_truth={},
        **common,
    ).status == "unverified"
    assert assess_expectation(
        "not valid syntax",
        output={},
        schemas={},
        hidden_truth={},
        **common,
    ).status == "unverified"


@pytest.mark.parametrize(
    ("expression", "complete", "status"),
    [
        ("trace.tool_called(status.publish)", True, "passed"),
        ("trace.not_called(other.tool)", True, "unverified"),
        ("trace.not_called(status.publish)", True, "violated"),
        ("trace.called_once(status.publish)", True, "passed"),
        ("trace.called_times(status.publish, 2)", True, "violated"),
        ("trace.called_times(status.publish, 3)", False, "unverified"),
        ("trace.max_calls(status.publish, 0)", True, "violated"),
        ("trace.max_calls(status.publish, 2)", False, "unverified"),
        ("trace.contains(tool.completed)", True, "passed"),
        ("trace.approval_denied(status.publish)", True, "violated"),
    ],
)
def test_eval_trace_presence_absence_and_count_branches(
    expression: str,
    complete: bool,
    status: str,
) -> None:
    trace = NormalizedTrace(
        (
            _event("evt-1", "approval.completed", data={"approved": True}),
            _event("evt-2", "tool.completed", timestamp=2),
        )
    )

    result = assess_expectation(
        expression,
        ir=_ir(),
        output={},
        trace=trace,
        trace_completeness=_completeness(complete),
        schemas={},
        hidden_truth={},
    )

    assert result.status == status


def test_eval_trace_ordering_approval_and_actor_specific_branches() -> None:
    base = _ir()
    other = AgentIR(
        semantic_id("agent", "Other"),
        "Other",
        (),
        parse_type_ref("Result"),
        "Be second.",
    )
    ir = replace(base, agents=FrozenMap((*base.agents.items(), (other.id, other))))
    trace = NormalizedTrace(
        (
            _event("evt-1", "agent.completed", agent="SupportAgent", capability=None, grant=None),
            _event("evt-2", "agent.completed", timestamp=2, agent="Other", capability=None, grant=None),
            _event("evt-3", "tool.completed", timestamp=3),
            _event("evt-4", "approval.completed", timestamp=4, data={"approved": False}),
        )
    )
    expressions = {
        "trace.called_before(SupportAgent, Other)": "passed",
        "trace.called_after(SupportAgent, Other)": "violated",
        "trace.called_before(SupportAgent, Missing)": "unverified",
        "trace.approval_denied(status.publish)": "passed",
        "trace.approval_granted(status.publish)": "violated",
        "trace.not_tool_called_by(Other, status.publish)": "passed",
        "trace.not_tool_called_by(SupportAgent, status.publish)": "violated",
    }
    for expression, status in expressions.items():
        result = assess_expectation(
            expression,
            ir=ir,
            output={},
            trace=trace,
            trace_completeness=_completeness(True),
            schemas={},
            hidden_truth={},
        )
        assert result.status == status, expression


def test_compiler_builds_nested_schema_docs_instructions_and_stable_digests() -> None:
    detail = TypeIR(
        semantic_id("type", "Detail"),
        "Detail",
        (
            TypeFieldIR("at", parse_type_ref("datetime")),
            TypeFieldIR("labels", parse_type_ref("map[string, string]"), True, FrozenMap({"a": "b"})),
        ),
    )
    result = TypeIR(
        semantic_id("type", "Result"),
        "Result",
        (
            TypeFieldIR("detail", parse_type_ref("Detail")),
            TypeFieldIR("items", parse_type_ref("list[Detail?]")),
            TypeFieldIR("score", parse_type_ref("float?")),
        ),
    )
    agent_id = semantic_id("agent", "Worker")
    control = ControlIR(
        semantic_id("control", "Worker", "be_safe"),
        "be_safe",
        agent_id,
        "high",
        True,
        ("model", "evaluator"),
        "runtime",
        requirement="trace.not_called(danger)",
    )
    agent = AgentIR(
        agent_id,
        "Worker",
        (ParameterIR("query", parse_type_ref("string")),),
        parse_type_ref("Result"),
        "Return a result.",
        description="A careful worker.",
        guidance=(GuidanceIR("Do the safe thing.", ("model",)), GuidanceIR("hidden", ("host",))),
    )
    ir = CanonicalIR.create(types=(detail, result), agents=(agent,), controls=(control,))

    artifacts = build_artifacts(ir)

    schema = artifacts.schemas["Result"]
    assert schema["$defs"]["Detail"]["properties"]["at"] == {"type": "string", "format": "date-time"}  # type: ignore[index]
    assert schema["properties"]["score"]["anyOf"][-1] == {"type": "null"}  # type: ignore[index]
    assert "Do the safe thing." in artifacts.instructions["Worker"]
    assert "hidden" not in artifacts.instructions["Worker"]
    assert "trace.not_called(danger)" in artifacts.instructions["Worker"]
    assert "None." in artifacts.docs[Path("agents/Worker.md")]
    digests = artifact_digests(artifacts)
    assert all(value.startswith("sha256:") for value in digests.values())
    assert digests == artifact_digests(artifacts)


def test_materialized_pydantic_types_cover_collections_defaults_and_parameters() -> None:
    child = TypeIR(
        semantic_id("type", "Child"),
        "Child",
        (TypeFieldIR("value", parse_type_ref("integer")),),
    )
    result = TypeIR(
        semantic_id("type", "Result"),
        "Result",
        (
            TypeFieldIR("child", parse_type_ref("Child")),
            TypeFieldIR("names", parse_type_ref("list[string]"), True, ("a", "b")),
            TypeFieldIR("scores", parse_type_ref("map[string, float]"), True, FrozenMap({"x": 1.5})),
            TypeFieldIR("when", parse_type_ref("datetime?")),
            TypeFieldIR("enabled", parse_type_ref("boolean"), True, True),
        ),
    )
    output_types = build_pydantic_types(CanonicalIR.create(types=(child, result)))
    result_type = cast(type, output_types["Result"])

    instance = result_type(child={"value": 3})
    assert instance.child.value == 3
    assert instance.names == ["a", "b"]
    assert instance.scores == {"x": 1.5}
    assert instance.when is None
    assert instance.enabled is True
    with pytest.raises(ValidationError):
        result_type(child={"value": "wrong"})

    parameter_type = cast(
        type,
        build_parameter_model(
            "Input",
            (
                ParameterIR("result", parse_type_ref("Result")),
                ParameterIR("at", parse_type_ref("datetime?"), required=False),
                ParameterIR("limit", parse_type_ref("integer"), required=False, has_default=True, default=2),
                ParameterIR(
                    "flags",
                    parse_type_ref("list[boolean]"),
                    required=False,
                    has_default=True,
                    default=(True,),
                ),
            ),
            output_types,
        ),
    )
    parameters = parameter_type(result={"child": {"value": 1}}, at=datetime(2026, 1, 1))
    assert parameters.limit == 2
    assert parameters.flags == [True]
    assert build_parameter_model("Empty", (), output_types) is None
    assert output_type_for(parse_type_ref("Result"), output_types) is result_type
    with pytest.raises(MaterializationError, match="MAT204"):
        output_type_for(parse_type_ref("string"), output_types)


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


def test_control_assessor_handles_approval_output_and_explicit_evidence() -> None:
    ir = _ir()
    plan = _plan(ir)
    approval_control = "SupportAgent:approval:status.publish"
    output_control = "SupportAgent:output_conformance"
    trace_events = (
            _event("evt-1", "approval.completed", timestamp=1, controls=(approval_control,), data={"approved": True}),
            _event("evt-2", "tool.started", timestamp=2, controls=(approval_control,)),
            _event("evt-3", "tool.completed", timestamp=3, controls=(approval_control,)),
            _event(
                "evt-4",
                "output.accepted",
                timestamp=4,
                capability=None,
                grant=None,
                controls=(output_control,),
            ),
            _event("evt-5", "approval.requested", timestamp=0, controls=(approval_control,)),
    )
    trace = _conforming_trace(ir, plan, trace_events)

    results = assess_controls(ir, plan, trace)

    assert {result.status for result in results} == {"passed"}
    assert any(result.evidence_event_ids == ("evt-1", "evt-2", "evt-3", "evt-5") for result in results)


@pytest.mark.parametrize(
    ("events", "expected_status"),
    [
        (("tool.started",), "violated"),
        (("tool.started", "approval.completed"), "violated"),
        ((), "unverified"),
    ],
)
def test_control_assessor_approval_failure_and_missing_evidence_branches(
    events: tuple[str, ...],
    expected_status: str,
) -> None:
    ir = _ir()
    plan = _plan(ir, expected_telemetry=("event.never_emitted",))
    trace_events = tuple(
        _event(
            f"evt-{index}",
            event_type,
            timestamp=float(index),
            controls=("SupportAgent:approval:status.publish",),
            data={"approved": True} if event_type == "approval.completed" else None,
        )
        for index, event_type in enumerate(events, 1)
    ) or (_event("evt-0", "run.started", capability=None, grant=None),)

    result = assess_controls(ir, plan, _conforming_trace(ir, plan, trace_events))[0]

    assert result.status == expected_status


@pytest.mark.parametrize(
    ("requirement", "expected_status"),
    [
        ('trace.agent_called("SupportAgent")', "passed"),
        ("trace.tool_called(status.publish)", "passed"),
        ("trace.not_called(missing)", "unverified"),
        ('trace.called_before("SupportAgent", Other)', "passed"),
        ("trace.approval_granted(status.publish)", "passed"),
        ("trace.tool_called(status.publish) and trace.not_called(missing)", "unverified"),
        ("trace.agent_called(Missing)", "unverified"),
        ("trace.unknown(operation)", "unverified"),
        ("not deterministic prose", "unverified"),
    ],
)
def test_control_assessor_deterministic_requirement_language(
    requirement: str,
    expected_status: str,
) -> None:
    base = _ir()
    agent_id = semantic_id("agent", "SupportAgent")
    control = ControlIR(
        semantic_id("control", "SupportAgent", "requirement"),
        "requirement",
        agent_id,
        "high",
        True,
        ("evaluator",),
        "post_run",
        requirement=requirement,
    )
    other = AgentIR(
        semantic_id("agent", "Other"),
        "Other",
        (),
        parse_type_ref("Result"),
        "Be second.",
    )
    ir = replace(
        base,
        agents=FrozenMap((*base.agents.items(), (other.id, other))),
        controls=FrozenMap({control.id: control}),
    )
    base_plan = _plan(base)
    events = (
        _event("evt-1", "agent.completed", agent="SupportAgent", capability=None, grant=None),
        _event("evt-2", "agent.completed", timestamp=2, agent="Other", capability=None, grant=None),
        _event("evt-3", "tool.completed", timestamp=3),
        _event("evt-4", "approval.completed", timestamp=2.5, data={"approved": True}),
    )
    plan = replace(
        base_plan,
        contract_digest=contract_digest(ir),
        controls=FrozenMap(
            {
                control.id: ControlMappingPlan(
                    control.id,
                    True,
                    "post_run",
                    "exact",
                    "test",
                    tuple(event.event_type for event in events),
                )
            }
        ),
        expected_telemetry=tuple(event.event_type for event in events),
    )

    result = assess_controls(ir, plan, _conforming_trace(ir, plan, events))[0]

    assert result.status == expected_status


def test_conditional_control_distinguishes_false_true_and_unverified_applicability() -> None:
    base = _ir()
    agent_id = semantic_id("agent", "SupportAgent")
    control = ControlIR(
        semantic_id("control", "SupportAgent", "conditional"),
        "conditional",
        agent_id,
        "high",
        True,
        ("evaluator",),
        "post_run",
        condition="trace.tool_called(status.publish)",
        requirement="trace.agent_called(SupportAgent)",
    )
    ir = replace(base, controls=FrozenMap({control.id: control}))
    base_plan = _plan(base)
    plan = replace(
        base_plan,
        contract_digest=contract_digest(ir),
        controls=FrozenMap(
            {control.id: ControlMappingPlan(control.id, True, "post_run", "exact", "test", ())}
        ),
        expected_telemetry=("agent.completed",),
    )
    attempt = TraceAttempt("support:1", "support-attempt-1", 1)
    agent_event = _event(
        "evt-agent",
        "agent.completed",
        capability=None,
        grant=None,
        data={"attempt": attempt.to_dict()},
    )
    false_trace = _conforming_trace(ir, plan, (agent_event,))
    closure = TraceClosureEvidence(
        false_trace.events[0].context,
        "complete",
        "The fixture covers all tool and agent paths.",
        ("agent", "tool"),
        (
            TraceAttemptClosure(
                attempt,
                agent_id,
                "complete",
                "complete",
                evidence_refs=("fixture:attempt",),
            ),
        ),
        ("fixture:closure",),
    )

    not_applicable = assess_controls(ir, plan, false_trace, closure=closure)[0]
    unknown = assess_controls(ir, plan, false_trace)[0]
    tool_event = _event(
        "evt-tool",
        "tool.completed",
        timestamp=2,
        data={"attempt": attempt.to_dict()},
    )
    true_trace = _conforming_trace(ir, plan, (agent_event, tool_event))
    applicable = assess_controls(
        ir,
        plan,
        true_trace,
        closure=replace(closure, context=true_trace.events[0].context),
    )[0]

    assert (not_applicable.status, not_applicable.applicability) == ("passed", "not_applicable")
    assert (unknown.status, unknown.applicability) == ("unverified", "unverified")
    assert (applicable.status, applicable.applicability) == ("passed", "applicable")


def test_control_assessor_prefers_explicit_results_and_handles_output_failures() -> None:
    base = _ir()
    plan = _plan(base, expected_telemetry=("control.assessed",))
    custom_id = semantic_id("control", "SupportAgent", "custom")
    custom = ControlIR(
        custom_id,
        "custom",
        semantic_id("agent", "SupportAgent"),
        "medium",
        False,
        ("evaluator",),
        "runtime",
        requirement="unsupported",
    )
    ir = replace(base, controls=FrozenMap({custom_id: custom}))
    plan = replace(
        plan,
        contract_digest=contract_digest(ir),
        controls=FrozenMap(
            {custom_id: ControlMappingPlan(custom_id, False, "runtime", "exact", "test", ())}
        ),
    )
    trace_events = (
            _event(
                "evt-1",
                "control.assessed",
                capability=None,
                grant=None,
                controls=("SupportAgent:custom",),
                data={"status": "violated", "reason": "Explicit judge result."},
            ),
    )
    trace = _conforming_trace(ir, plan, trace_events)

    explicit = assess_controls(ir, plan, trace)[0]
    assert explicit.status == "violated"
    assert explicit.reason == "Explicit judge result."

    output_plan = _plan(base, expected_telemetry=("output.schema_failed",))
    failed = assess_controls(
        base,
        output_plan,
        _conforming_trace(
            base,
            output_plan,
            (
                _event(
                    "evt-2",
                    "output.schema_failed",
                    capability=None,
                    grant=None,
                    controls=("SupportAgent:output_conformance",),
                ),
            ),
        ),
    )
    assert next(item for item in failed if item.control_id.endswith("output_conformance")).status == "violated"


def test_openai_tool_names_round_trip_and_validate_prefixed_encodings() -> None:
    contract_name = "crm.create_note"
    encoded = openai_tool_name(contract_name)

    assert encoded == "c4a_3_crm11_create_note"
    assert contract_tool_name(encoded) == contract_name
    assert contract_tool_name("ordinary_tool") == "ordinary_tool"
    for malformed in ("c4a_bad", "c4a__bad", "c4a_x_bad", "c4a_9_short"):
        with pytest.raises(ValueError, match="not valid"):
            contract_tool_name(malformed)
