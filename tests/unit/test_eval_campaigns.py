from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from contract4agents.eval_campaigns import (
    ApprovalRequest,
    BaselineSnapshot,
    BaselineTolerance,
    CampaignConfig,
    CampaignThresholds,
    EvalProviderError,
    FileEvalProvider,
    run_campaign,
)
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    CapabilityIR,
    ControlIR,
    EvalIR,
    FrozenMap,
    GrantIR,
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
from contract4agents.tracing import TraceConformanceError


def _ir(*, missing_judge: bool = False, negative_expectation: bool = False) -> CanonicalIR:
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
        qualities=(
            QualityIR(quality_id, "useful", agent_id, "The response is useful."),
        ),
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


def _plan(ir: CanonicalIR, *, expected_event_types: tuple[str, ...] | None = None) -> MaterializationPlan:
    agent_id = semantic_id("agent", "SupportAgent")
    capability_id = semantic_id("tool", "status.publish")
    grant_id = semantic_id("grant", "SupportAgent", "status.publish")
    controls = tuple(ir.controls.values())
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
                agent_id: AgentPlan(agent_id, "SupportAgent", "deterministic", FrozenMap(), parse_type_ref("Result"))
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
                for control in controls
            }
        ),
        isolation=FrozenMap(),
        host_obligations=(),
        expected_event_types=telemetry,
        artifact_digests=FrozenMap(),
    )


def _event_data(*, complete: bool = True) -> list[dict[str, object]]:
    semantic = {
        "agent_id": "agent:SupportAgent",
        "capability_id": "tool:status.publish",
        "grant_id": "grant:SupportAgent:status.publish",
        "control_ids": ["control:SupportAgent:approval:status.publish"],
    }
    events: list[dict[str, object]] = [
        {"event_type": "approval.requested", "semantic": semantic},
        {"event_type": "approval.completed", "semantic": semantic, "data": {"approved": True}},
        {"event_type": "tool.started", "semantic": semantic},
        {"event_type": "tool.completed", "semantic": semantic},
    ]
    if complete:
        events.append(
            {
                "event_type": "output.accepted",
                "semantic": {
                    "agent_id": "agent:SupportAgent",
                    "control_ids": ["control:SupportAgent:output_conformance"],
                },
            }
        )
    return events


def _write_eval_data(
    path: Path,
    *,
    trials: list[dict[str, object]],
) -> FileEvalProvider:
    closed_trials = [
        {
            **trial,
            "closure": trial.get(
                "closure",
                {
                    "status": "complete",
                    "reason": "The deterministic fixture enumerates every execution path.",
                    "channels": [
                        "agent",
                        "approval",
                        "composition",
                        "datasource",
                        "guardrail",
                        "handoff",
                        "output",
                        "provider_response",
                        "tool",
                    ],
                    "evidence_refs": ["fixture:eval:closure"],
                },
            ),
        }
        for trial in trials
    ]
    path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "cases": {
                    "eval:SupportAgent:publishes_status": {
                        "inputs": {"tenant": "acme"},
                        "trials": closed_trials,
                    }
                },
            }
        )
    )
    return FileEvalProvider.load(path)


@pytest.mark.asyncio
async def test_campaign_runs_repeated_trials_and_reports_deterministic_statistics(tmp_path: Path) -> None:
    ir = _ir()
    plan = _plan(ir)
    trials = [
        {
            "output": {"status": "ok", "message": "Published"},
            "events": _event_data(),
            "approvals": {"tool:status.publish": True},
            "judges": {
                "quality:SupportAgent:useful": {
                    "status": "passed",
                    "reason": "Useful and direct.",
                    "score": 0.9,
                    "provider": "fixture-judge",
                    "version": "1",
                }
            },
            "metrics": {"latency_ms": 100, "cost_usd": 0.01, "input_tokens": 10, "output_tokens": 20},
        },
        {
            "output": {"status": "bad", "message": "Published"},
            "events": _event_data(),
            "judges": {
                "quality:SupportAgent:useful": {
                    "status": "violated",
                    "reason": "Not sufficiently useful.",
                    "score": 0.2,
                }
            },
            "metrics": {"latency_ms": 300, "cost_usd": 0.03, "input_tokens": 30, "output_tokens": 40},
        },
    ]
    provider = _write_eval_data(tmp_path / "eval-data.json", trials=trials)
    config = CampaignConfig(
        "nightly",
        trial_count=2,
        thresholds=CampaignThresholds(min_pass_rate=0.75, max_violation_rate=0.5, max_mean_latency_ms=250),
        baseline=BaselineSnapshot("sha256:baseline", 0.8, 0.2, 150, 0.015),
        baseline_tolerance=BaselineTolerance(
            max_pass_rate_drop=0.1,
            max_violation_rate_increase=0.3,
            max_latency_increase_ratio=0.5,
            max_cost_increase_ratio=0.5,
        ),
    )

    result = await run_campaign(ir, plan, provider, config)
    repeated = await run_campaign(ir, plan, provider, config)

    assert [trial.status for trial in result.cases[0].trials] == ["passed", "violated"]
    assert result.summary.rates.pass_rate == 0.5
    assert result.summary.rates.violation_rate == 0.5
    assert 0 < result.summary.rates.pass_interval.lower < 0.5
    assert result.summary.rates.pass_interval.upper > 0.5
    assert result.summary.metrics.latency_ms.mean == 200
    assert result.summary.metrics.cost_usd.mean == 0.02
    assert result.summary.metrics.total_tokens.mean == 50
    assert result.inventory.agent_ids == ("agent:SupportAgent",)
    assert result.inventory.capability_ids == ("tool:status.publish",)
    assert result.inventory.control_ids == (
        "control:SupportAgent:approval:status.publish",
        "control:SupportAgent:output_conformance",
    )
    assert [item.status for item in result.threshold_results] == ["violated", "passed", "passed"]
    assert result.baseline_digest == "sha256:baseline"
    assert [item.status for item in result.regression_results] == [
        "violated",
        "passed",
        "passed",
        "passed",
    ]
    assert result.cases[0].trials[0].controls[0].assessor.name == "contract4agents"
    assert result.to_json() == repeated.to_json()
    assert result.campaign_digest == repeated.campaign_digest
    assert json.loads(result.to_json()) == result.to_dict()
    with pytest.raises(FrozenInstanceError):
        result.campaign_id = "changed"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_missing_event_types_and_judge_results_are_unverified(tmp_path: Path) -> None:
    quality_ir = _ir()
    quality_plan = _plan(quality_ir)
    provider = _write_eval_data(
        tmp_path / "eval-data.json",
        trials=[
            {
                "output": {"status": "ok", "message": "Published"},
                "events": _event_data(),
                "judges": {},
            }
        ],
    )
    quality_result = await run_campaign(quality_ir, quality_plan, provider, CampaignConfig("missing-judge"))

    assert quality_result.cases[0].trials[0].status == "unverified"
    assert quality_result.cases[0].trials[0].qualities[0].status == "unverified"

    negative_ir = _ir(missing_judge=True, negative_expectation=True)
    negative_plan = _plan(negative_ir, expected_event_types=("approval.requested", "output.accepted"))
    negative_result = await run_campaign(
        negative_ir,
        negative_plan,
        provider,
        CampaignConfig("incomplete-negative"),
    )
    negative_trial = negative_result.cases[0].trials[0]
    assert negative_trial.trace_evidence is not None
    assert negative_trial.trace_evidence.status == "complete"
    assert negative_trial.expectations[1].status == "unverified"

    incomplete_plan = _plan(negative_ir, expected_event_types=("approval.requested", "event.never_emitted"))
    incomplete = await run_campaign(
        negative_ir,
        incomplete_plan,
        provider,
        CampaignConfig("incomplete-negative"),
    )
    assert incomplete.cases[0].trials[0].expectations[1].status == "unverified"


@pytest.mark.asyncio
async def test_campaign_rejects_nonconforming_trace_before_scoring(tmp_path: Path) -> None:
    ir = _ir(missing_judge=True)
    plan = _plan(ir)
    provider = _write_eval_data(
        tmp_path / "undeclared.json",
        trials=[
            {
                "output": {"status": "ok", "message": "Published"},
                "events": [
                    {
                        "event_type": "capability.undeclared",
                        "data": {"provider_tool": "openai.web_search"},
                    }
                ],
            }
        ],
    )

    with pytest.raises(TraceConformanceError, match="TRC004"):
        await run_campaign(ir, plan, provider, CampaignConfig("nonconforming"))


@pytest.mark.asyncio
async def test_file_provider_supplies_approval_service_and_provider_failures_are_unverified(tmp_path: Path) -> None:
    ir = _ir(missing_judge=True)
    plan = _plan(ir)
    provider = _write_eval_data(
        tmp_path / "eval-data.json",
        trials=[
            {
                "output": {"status": "ok", "message": "Published"},
                "events": _event_data(),
                "approvals": {
                    "tool:status.publish": {
                        "approved": True,
                        "reason": "Test operator approved.",
                        "evidence_refs": ["fixture:approval:1"],
                    }
                },
            }
        ],
    )
    decision = await provider.approve(
        ApprovalRequest(
            semantic_id("eval", "SupportAgent", "publishes_status"),
            "trial:eval:SupportAgent:publishes_status:0001",
            semantic_id("tool", "status.publish"),
        )
    )
    assert decision is not None and decision.approved
    assert decision.evidence_refs == ("fixture:approval:1",)

    missing_trace_provider = _write_eval_data(
        tmp_path / "missing-trace.json",
        trials=[{"output": {"status": "ok", "message": "Published"}, "events": []}],
    )
    result = await run_campaign(ir, plan, missing_trace_provider, CampaignConfig("provider-failure"))
    assert result.cases[0].trials[0].status == "unverified"
    assert "does not contain normalized trace events" in (result.cases[0].trials[0].diagnostic or "")


def test_file_provider_rejects_unknown_eval_data_version(tmp_path: Path) -> None:
    path = tmp_path / "eval-data.json"
    path.write_text(json.dumps({"schema_version": "99", "cases": {}}))

    with pytest.raises(EvalProviderError, match="Unsupported eval-data schema_version"):
        FileEvalProvider.load(path)
