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
    EvaluatorTruth,
    FinalizedTrialEvidence,
    QualityResult,
    RedactedTrialView,
    ResolvedTrialData,
    ResultSummary,
    TrialMetrics,
    TrialResult,
    summarize_trials,
)
from contract4agents.eval_campaigns._provider import (
    EvalExecutionRequest,
    EvalProvider,
    JudgeOutcome,
    JudgeRequest,
)
from contract4agents.ir import CanonicalIR, EvalIR, SemanticId, contract_digest
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing import ProviderUsageEvidence, assess_trace_evidence, validate_trace_conformance


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
    trial_data: ResolvedTrialData | None = None
    try:
        trial_data = await provider.resolve_trial_data(case, trial_index=trial_index)
        evidence = await provider.execute(
            EvalExecutionRequest(
                case=case,
                trial_id=trial_id,
                trial_index=trial_index,
                invocation=trial_data.invocation,
                host_context=trial_data.host_context,
                contract_digest=plan.contract_digest,
                plan_digest=plan.plan_digest,
                inventory=inventory,
            )
        )
    except Exception as exc:  # noqa: BLE001 - provider failures become explicit unverified trials.
        evidence = FinalizedTrialEvidence(
            "failed",
            None,
            None,
            None,
            TrialMetrics(),
            f"Eval provider failed: {exc}",
        )

    judge_outcomes: Mapping[SemanticId, JudgeOutcome] = {}
    if evidence.execution_status == "succeeded":
        assert evidence.output is not None
        assert evidence.trace is not None
        validate_trace_conformance(ir, plan, evidence.trace)
        judge_outcomes = await _resolve_judge_outcomes(
            case,
            trial_id,
            evidence,
            ir,
            provider,
        )
    return assess_finalized_evidence(
        ir=ir,
        plan=plan,
        case=case,
        trial_id=trial_id,
        evidence=evidence,
        evaluator_truth=(
            trial_data.evaluator_truth if trial_data is not None else EvaluatorTruth()
        ),
        invocation_digest=(
            trial_data.invocation.digest if trial_data is not None else None
        ),
        report_view=(
            trial_data.report_view if trial_data is not None else RedactedTrialView()
        ),
        judge_outcomes=judge_outcomes,
        schemas=schemas,
    )


def assess_finalized_evidence(
    *,
    ir: CanonicalIR,
    plan: MaterializationPlan,
    case: EvalIR,
    trial_id: str,
    evidence: FinalizedTrialEvidence,
    evaluator_truth: EvaluatorTruth,
    invocation_digest: str | None,
    report_view: RedactedTrialView,
    judge_outcomes: Mapping[SemanticId, JudgeOutcome],
    schemas: Mapping[str, dict[str, object]],
) -> TrialResult:
    """Assess already finalized evidence without acquiring or executing a trial."""

    if evidence.execution_status == "failed":
        return TrialResult(
            case_id=str(case.id),
            trial_id=trial_id,
            status="unverified",
            invocation_digest=invocation_digest,
            report_view=report_view,
            output=evidence.output,
            trace=evidence.trace,
            expectations=(),
            controls=(),
            qualities=(),
            trace_evidence=None,
            trace_closure=evidence.closure,
            metrics=evidence.metrics,
            diagnostic=evidence.diagnostic,
        )

    assert evidence.output is not None
    assert evidence.trace is not None
    assert evidence.closure is not None
    metrics = _derive_usage_metrics(evidence)
    validate_trace_conformance(ir, plan, evidence.trace)
    trace_evidence = assess_trace_evidence(
        evidence.trace,
        plan.expected_event_types,
        closure=evidence.closure,
    )
    expectations = tuple(
        assess_expectation(
            expression,
            output=evidence.output,
            trace=evidence.trace,
            trace_evidence=trace_evidence,
            ir=ir,
            schemas=schemas,
            hidden_truth=evaluator_truth.values,
        )
        for expression in case.expectations
    )
    case_control_ids = {
        control.id for control in ir.controls.values() if control.agent_id == case.agent_id
    }
    controls = tuple(
        result
        for result in assess_controls(ir, plan, evidence.trace, closure=evidence.closure)
        if SemanticId.parse(result.control_id) in case_control_ids
    )
    qualities = tuple(
        _quality_result(quality_id, ir, judge_outcomes.get(quality_id))
        for quality_id in case.quality_ids
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
        invocation_digest=invocation_digest,
        report_view=report_view,
        output=evidence.output,
        trace=evidence.trace,
        expectations=expectations,
        controls=controls,
        qualities=qualities,
        trace_evidence=trace_evidence,
        trace_closure=evidence.closure,
        metrics=metrics,
        diagnostic=evidence.diagnostic,
    )


async def _resolve_judge_outcomes(
    case: EvalIR,
    trial_id: str,
    evidence: FinalizedTrialEvidence,
    ir: CanonicalIR,
    provider: EvalProvider,
) -> Mapping[SemanticId, JudgeOutcome]:
    assert evidence.output is not None
    assert evidence.trace is not None
    outcomes: dict[SemanticId, JudgeOutcome] = {}
    for quality_id in case.quality_ids:
        quality = ir.qualities.get(quality_id)
        if quality is None:
            continue
        try:
            decision = await provider.judge(
                JudgeRequest(
                    case.id,
                    trial_id,
                    quality.id,
                    quality.rubric,
                    evidence.output,
                    evidence.trace,
                )
            )
        except Exception as exc:  # noqa: BLE001 - judge failures remain explicit.
            outcomes[quality_id] = JudgeOutcome(None, f"Judge failed: {exc}")
        else:
            outcomes[quality_id] = JudgeOutcome(decision)
    return outcomes


def _quality_result(
    quality_id: SemanticId,
    ir: CanonicalIR,
    outcome: JudgeOutcome | None,
) -> QualityResult:
    quality = ir.qualities.get(quality_id)
    if quality is None:
        return QualityResult(
            str(quality_id),
            "unverified",
            "The eval references an unknown quality rubric.",
            AssessorIdentity("contract4agents", "1"),
        )
    if outcome is not None and outcome.diagnostic is not None:
        return QualityResult(
            str(quality.id),
            "unverified",
            outcome.diagnostic,
            AssessorIdentity("unavailable-judge", "0"),
        )
    decision = outcome.decision if outcome is not None else None
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


def _derive_usage_metrics(evidence: FinalizedTrialEvidence) -> TrialMetrics:
    """Use complete provider usage only when the provider did not supply metrics."""

    explicit = evidence.metrics
    if explicit.input_tokens is not None or explicit.output_tokens is not None:
        return explicit
    if evidence.trace is None:
        return explicit
    values: list[ProviderUsageEvidence] = []
    for event in evidence.trace.events:
        if event.event_type != "provider.usage.reported":
            continue
        payload = event.data.get("evidence")
        try:
            usage = ProviderUsageEvidence.from_dict(payload)
        except (TypeError, ValueError):
            return explicit
        values.append(usage)
    derived = TrialMetrics.from_provider_usage(tuple(values))
    return derived if derived.input_tokens is not None else explicit


__all__ = ["assess_finalized_evidence", "run_campaign"]
