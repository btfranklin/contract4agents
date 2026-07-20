from __future__ import annotations

from dataclasses import replace

import pytest

from contract4agents.assurance import (
    assess_controls,
)
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    ControlIR,
    FrozenMap,
    contract_digest,
    parse_type_ref,
    semantic_id,
)
from contract4agents.planning import (
    ControlMappingPlan,
)
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
)
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
    plan = _plan(ir, expected_event_types=("event.never_emitted",))
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
        expected_event_types=tuple(event.event_type for event in events),
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
        expected_event_types=("agent.completed",),
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
        TraceFrontier.from_trace(false_trace),
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
        closure=replace(
            closure,
            context=true_trace.events[0].context,
            frontier=TraceFrontier.from_trace(true_trace),
        ),
    )[0]

    assert (not_applicable.status, not_applicable.applicability) == ("passed", "not_applicable")
    assert (unknown.status, unknown.applicability) == ("unverified", "unverified")
    assert (applicable.status, applicable.applicability) == ("passed", "applicable")


def test_control_assessor_prefers_explicit_results_and_handles_output_failures() -> None:
    base = _ir()
    plan = _plan(base, expected_event_types=("control.assessed",))
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

    output_plan = _plan(base, expected_event_types=("output.schema_failed",))
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
