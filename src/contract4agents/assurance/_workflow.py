"""Library orchestration for assessing evidence and assembling one bundle."""

from __future__ import annotations

from contract4agents.assurance._assess import assess_controls
from contract4agents.assurance._bundle import AssuranceBundle, assemble_assurance_bundle
from contract4agents.assurance._inputs import RunSpecAssessmentManifest
from contract4agents.assurance._run_specs import assess_run_spec
from contract4agents.ir import CanonicalIR
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing import (
    NormalizedTrace,
    TraceClosureEvidence,
    TraceClosureManifest,
    dumps_trace_jsonl,
)


def assess_assurance_evidence(
    contract: CanonicalIR,
    plan: MaterializationPlan,
    *,
    trace: NormalizedTrace | None,
    trace_closures: TraceClosureManifest | None,
    run_spec_evidence: RunSpecAssessmentManifest | None,
    eval_results: object | None,
    provenance: object | None,
) -> AssuranceBundle:
    """Assess raw trace/run-spec evidence and assemble a deterministic bundle."""

    closures = trace_closures.closures if trace_closures is not None else None
    control_closure = None
    if trace is not None and len(trace.run_ids) == 1:
        control_closure = _closure_for_run(trace_closures, trace.run_ids[0])
    control_results = (
        assess_controls(contract, plan, trace, closure=control_closure)
        if trace is not None
        else None
    )
    selections = (
        None
        if run_spec_evidence is None
        else tuple(item.selection for item in run_spec_evidence.runs)
    )
    run_spec_results = None
    if run_spec_evidence is not None:
        if trace is None:
            raise ValueError("Run-spec evidence requires a normalized trace")
        assessed = []
        for item in run_spec_evidence.runs:
            if item.selection.run_spec_id is None:
                continue
            assert item.evidence is not None
            assessed.append(
                assess_run_spec(
                    contract,
                    plan,
                    trace,
                    item.selection.run_spec_id,
                    item.evidence,
                    closure=_closure_for_run(trace_closures, item.selection.run_id),
                    run_id=item.selection.run_id,
                )
            )
        run_spec_results = tuple(assessed)
    return assemble_assurance_bundle(
        contract,
        plan,
        normalized_trace_jsonl=dumps_trace_jsonl(trace) if trace is not None else None,
        control_results=control_results,
        trace_closures=closures,
        run_spec_selections=selections,
        run_spec_results=run_spec_results,
        eval_results=eval_results,
        provenance=provenance,
    )


def _closure_for_run(
    manifest: TraceClosureManifest | None,
    run_id: str,
) -> TraceClosureEvidence | None:
    if manifest is None:
        return None
    return next(
        (item for item in manifest.closures if item.context.run_id == run_id),
        None,
    )


__all__ = ["assess_assurance_evidence"]
