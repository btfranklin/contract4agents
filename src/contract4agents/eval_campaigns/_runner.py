"""Provider-neutral eval campaign orchestration and statistical reporting."""

from __future__ import annotations

from collections.abc import Mapping

from contract4agents.assurance import AssessorIdentity, AssuranceStatus, assess_controls
from contract4agents.compiler import build_artifacts
from contract4agents.eval_campaigns._expectations import assess_expectation
from contract4agents.eval_campaigns._models import (
    BaselineSnapshot,
    BaselineTolerance,
    CampaignConfig,
    CampaignResult,
    CampaignThresholds,
    CaseResult,
    ComparisonResult,
    EvalInventory,
    QualityResult,
    ResultSummary,
    TrialMetrics,
    TrialResult,
    summarize_trials,
)
from contract4agents.eval_campaigns._provider import (
    EvalExecutionRequest,
    EvalProvider,
    JudgeRequest,
)
from contract4agents.ir import CanonicalIR, EvalIR, SemanticId, contract_digest
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing import assess_trace_evidence, validate_trace_conformance


async def run_campaign(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    provider: EvalProvider,
    config: CampaignConfig,
) -> CampaignResult:
    """Run every canonical `.eval` case against one reviewed materialization plan."""

    digest = contract_digest(ir)
    if plan.contract_digest != digest:
        raise ValueError("Materialization plan contract digest does not match the canonical IR")
    if not ir.evals:
        raise ValueError("Canonical IR does not contain any eval cases")
    inventory = _inventory(ir, plan)
    schemas = build_artifacts(ir).schemas
    case_results: list[CaseResult] = []
    all_trials: list[TrialResult] = []
    for case in sorted(ir.evals.values(), key=lambda item: str(item.id)):
        trials = tuple(
            [
                await _run_trial(
                    case,
                    trial_index,
                    ir=ir,
                    plan=plan,
                    provider=provider,
                    inventory=inventory,
                    schemas=schemas,
                )
                for trial_index in range(config.trial_count)
            ]
        )
        all_trials.extend(trials)
        case_results.append(CaseResult(str(case.id), case.name, str(case.agent_id), trials, summarize_trials(trials)))
    summary = summarize_trials(tuple(all_trials))
    return CampaignResult(
        campaign_id=config.campaign_id,
        contract_digest=digest,
        plan_digest=plan.plan_digest,
        target=plan.target,
        profile=plan.profile,
        inventory=inventory,
        cases=tuple(case_results),
        summary=summary,
        threshold_results=_threshold_results(summary, config.thresholds),
        baseline_digest=config.baseline.digest if config.baseline is not None else None,
        regression_results=(
            _baseline_results(summary, config.baseline, config.baseline_tolerance)
            if config.baseline is not None
            else ()
        ),
    )


async def _run_trial(
    case: EvalIR,
    trial_index: int,
    *,
    ir: CanonicalIR,
    plan: MaterializationPlan,
    provider: EvalProvider,
    inventory: EvalInventory,
    schemas: Mapping[str, dict[str, object]],
) -> TrialResult:
    trial_id = f"trial:{case.id}:{trial_index + 1:04d}"
    try:
        inputs = await provider.resolve_inputs(case, trial_index=trial_index)
        execution = await provider.execute(
            EvalExecutionRequest(
                case=case,
                trial_id=trial_id,
                trial_index=trial_index,
                inputs=inputs,
                contract_digest=plan.contract_digest,
                plan_digest=plan.plan_digest,
                inventory=inventory,
            )
        )
    except Exception as exc:  # noqa: BLE001 - provider failures become explicit unverified trials.
        return TrialResult(
            case_id=str(case.id),
            trial_id=trial_id,
            status="unverified",
            inputs=locals().get("inputs", {}),
            output=None,
            trace=None,
            expectations=(),
            controls=(),
            qualities=(),
            trace_evidence=None,
            trace_closure=None,
            metrics=TrialMetrics(),
            diagnostic=f"Eval provider failed: {exc}",
        )

    validate_trace_conformance(ir, plan, execution.trace)
    trace_evidence = assess_trace_evidence(
        execution.trace,
        plan.expected_event_types,
        closure=execution.trace_closure,
    )
    hidden_truth_value = inputs.get("hidden_truth", {})
    hidden_truth = hidden_truth_value if isinstance(hidden_truth_value, Mapping) else {}
    expectations = tuple(
        assess_expectation(
            expression,
            output=execution.output,
            trace=execution.trace,
            trace_evidence=trace_evidence,
            ir=ir,
            schemas=schemas,
            hidden_truth=hidden_truth,
        )
        for expression in case.expectations
    )
    case_control_ids = {
        control.id for control in ir.controls.values() if control.agent_id == case.agent_id
    }
    controls = tuple(
        result
        for result in assess_controls(ir, plan, execution.trace, closure=execution.trace_closure)
        if SemanticId.parse(result.control_id) in case_control_ids
    )
    qualities = tuple(
        [
            await _judge_quality(case, trial_id, quality_id, execution.output, execution.trace, ir, provider)
            for quality_id in case.quality_ids
        ]
    )
    required_control_ids = {
        str(control.id)
        for control in ir.controls.values()
        if control.agent_id == case.agent_id and control.required
    }
    statuses = [result.status for result in expectations]
    statuses.extend(result.status for result in controls if result.control_id in required_control_ids)
    statuses.extend(result.status for result in qualities)
    if "violated" in statuses:
        status: AssuranceStatus = "violated"
    elif "unverified" in statuses or not statuses:
        status = "unverified"
    else:
        status = "passed"
    return TrialResult(
        case_id=str(case.id),
        trial_id=trial_id,
        status=status,
        inputs=inputs,
        output=execution.output,
        trace=execution.trace,
        expectations=expectations,
        controls=controls,
        qualities=qualities,
        trace_evidence=trace_evidence,
        trace_closure=execution.trace_closure,
        metrics=execution.metrics,
    )


async def _judge_quality(
    case: EvalIR,
    trial_id: str,
    quality_id: SemanticId,
    output: Mapping[str, object],
    trace: object,
    ir: CanonicalIR,
    provider: EvalProvider,
) -> QualityResult:
    from contract4agents.tracing import NormalizedTrace

    assert isinstance(trace, NormalizedTrace)
    quality = ir.qualities.get(quality_id)
    if quality is None:
        return QualityResult(
            str(quality_id),
            "unverified",
            "The eval references an unknown quality rubric.",
            AssessorIdentity("contract4agents", "1"),
        )
    try:
        decision = await provider.judge(
            JudgeRequest(case.id, trial_id, quality.id, quality.rubric, output, trace)
        )
    except Exception as exc:  # noqa: BLE001 - judge failures must not disappear or become passes.
        return QualityResult(
            str(quality.id),
            "unverified",
            f"Judge failed: {exc}",
            AssessorIdentity("unavailable-judge", "0"),
        )
    if decision is None:
        return QualityResult(
            str(quality.id),
            "unverified",
            "No judge result was available for this quality rubric.",
            AssessorIdentity("unavailable-judge", "0"),
        )
    return QualityResult(
        str(quality.id),
        "passed" if decision.passed else "violated",
        decision.reason,
        AssessorIdentity(decision.provider, decision.version),
        decision.score,
        decision.evidence_refs,
    )


def _inventory(ir: CanonicalIR, plan: MaterializationPlan) -> EvalInventory:
    return EvalInventory(
        agent_ids=tuple(str(identifier) for identifier in ir.agents),
        capability_ids=tuple(str(identifier) for identifier in ir.capabilities),
        grant_ids=tuple(str(identifier) for identifier in ir.grants),
        control_ids=tuple(str(identifier) for identifier in ir.controls),
        expected_event_types=plan.expected_event_types,
    )


def _threshold_results(
    summary: ResultSummary,
    thresholds: CampaignThresholds,
) -> tuple[ComparisonResult, ...]:
    comparisons: list[ComparisonResult] = []
    if thresholds.min_pass_rate is not None:
        comparisons.append(
            _comparison("threshold.pass_rate", summary.rates.pass_rate, ">=", thresholds.min_pass_rate)
        )
    if thresholds.max_violation_rate is not None:
        comparisons.append(
            _comparison("threshold.violation_rate", summary.rates.violation_rate, "<=", thresholds.max_violation_rate)
        )
    if thresholds.max_mean_latency_ms is not None:
        comparisons.append(
            _comparison(
                "threshold.mean_latency_ms",
                summary.metrics.latency_ms.mean,
                "<=",
                thresholds.max_mean_latency_ms,
            )
        )
    if thresholds.max_mean_cost_usd is not None:
        comparisons.append(
            _comparison(
                "threshold.mean_cost_usd",
                summary.metrics.cost_usd.mean,
                "<=",
                thresholds.max_mean_cost_usd,
            )
        )
    return tuple(comparisons)


def _baseline_results(
    summary: ResultSummary,
    baseline: BaselineSnapshot,
    tolerance: BaselineTolerance,
) -> tuple[ComparisonResult, ...]:
    comparisons = [
        _comparison(
            "baseline.pass_rate",
            summary.rates.pass_rate,
            ">=",
            max(0.0, baseline.pass_rate - tolerance.max_pass_rate_drop),
        ),
        _comparison(
            "baseline.violation_rate",
            summary.rates.violation_rate,
            "<=",
            min(1.0, baseline.violation_rate + tolerance.max_violation_rate_increase),
        ),
    ]
    if tolerance.max_latency_increase_ratio is not None and baseline.mean_latency_ms is not None:
        comparisons.append(
            _comparison(
                "baseline.mean_latency_ms",
                summary.metrics.latency_ms.mean,
                "<=",
                baseline.mean_latency_ms * (1 + tolerance.max_latency_increase_ratio),
            )
        )
    if tolerance.max_cost_increase_ratio is not None and baseline.mean_cost_usd is not None:
        comparisons.append(
            _comparison(
                "baseline.mean_cost_usd",
                summary.metrics.cost_usd.mean,
                "<=",
                baseline.mean_cost_usd * (1 + tolerance.max_cost_increase_ratio),
            )
        )
    return tuple(comparisons)


def _comparison(name: str, actual: float | None, operator: str, target: float) -> ComparisonResult:
    if actual is None:
        return ComparisonResult(name, "unverified", "The metric was not reported.", None, target, operator)
    passed = actual >= target if operator == ">=" else actual <= target
    return ComparisonResult(
        name,
        "passed" if passed else "violated",
        f"Observed {actual}; required {operator} {target}.",
        actual,
        target,
        operator,
    )


__all__ = ["run_campaign"]
