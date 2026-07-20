from __future__ import annotations

from dataclasses import replace

import pytest

from contract4agents.eval_campaigns._expectations import assess_expectation
from contract4agents.ir import (
    AgentIR,
    FrozenMap,
    parse_type_ref,
    semantic_id,
)
from contract4agents.tracing import (
    NormalizedTrace,
    ProviderCorrelation,
    TraceEvent,
    TraceEvidenceAssessment,
    TraceRunContext,
    TraceSemanticRefs,
)
from tests.unit.test_eval_campaigns import _ir

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


def _trace_evidence(complete: bool) -> TraceEvidenceAssessment:
    return TraceEvidenceAssessment(
        "run-1",
        "complete" if complete else "unverified",
        "complete" if complete else "incomplete",
        closure_digest=f"sha256:{'c' * 64}" if complete else None,
        closed_channels=(
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
        trace_evidence=_trace_evidence(True),
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
    common = {"trace": trace, "trace_evidence": _trace_evidence(True), "ir": _ir()}

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
        trace_evidence=_trace_evidence(complete),
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
            trace_evidence=_trace_evidence(True),
            schemas={},
            hidden_truth={},
        )
        assert result.status == status, expression
