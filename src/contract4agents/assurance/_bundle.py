"""Deterministic portable assurance-bundle assembly and verification."""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from contract4agents.assurance._models import ControlResult
from contract4agents.assurance._run_specs import RunSpecResult, RunSpecSelection
from contract4agents.ir import CanonicalIR, FrozenMap, canonical_ir_data, contract_digest
from contract4agents.planning import MaterializationPlan, materialization_plan_data
from contract4agents.tracing import (
    TRACE_CLOSURE_MANIFEST_VERSION,
    TraceClosureEvidence,
    TraceClosureManifest,
    loads_trace_jsonl,
    validate_trace_closure,
)

BUNDLE_VERSION = "1"


@dataclass(frozen=True)
class BundleDiagnostic:
    code: str
    message: str
    artifact: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"artifact": self.artifact, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class AssuranceBundle:
    contract_digest: str
    plan_digest: str
    files: FrozenMap[str, str]
    diagnostics: tuple[BundleDiagnostic, ...] = ()
    bundle_version: str = BUNDLE_VERSION

    @property
    def complete(self) -> bool:
        return not self.diagnostics


def assemble_assurance_bundle(
    contract: CanonicalIR,
    plan: MaterializationPlan,
    *,
    normalized_trace_jsonl: str | None,
    control_results: tuple[ControlResult, ...] | None,
    eval_results: object | None,
    provenance: object | None,
    trace_closures: tuple[TraceClosureEvidence, ...] | None = None,
    run_spec_results: tuple[RunSpecResult, ...] | None = None,
    run_spec_selections: tuple[RunSpecSelection, ...] | None = None,
) -> AssuranceBundle:
    """Assemble all declared, planned, observed, and assessed evidence without timestamps."""

    expected_digest = contract_digest(contract)
    if plan.contract_digest != expected_digest:
        raise ValueError(f"Plan contract digest `{plan.contract_digest}` does not match contract `{expected_digest}`")
    diagnostics: list[BundleDiagnostic] = []
    trace = _required_text(
        "normalized-trace.jsonl",
        normalized_trace_jsonl,
        diagnostics,
        "BUNDLE001",
        "Normalized trace evidence is missing.",
    )
    loaded_trace = loads_trace_jsonl(trace) if trace else None
    closures = tuple(trace_closures or ())
    if loaded_trace is None and closures:
        raise ValueError("Trace closure evidence cannot be bundled without a normalized trace")
    closure_runs = [item.context.run_id for item in closures]
    if len(closure_runs) != len(set(closure_runs)):
        raise ValueError("Trace closures must have unique run_id values")
    trace_run_ids = set(loaded_trace.run_ids) if loaded_trace is not None else set()
    closure_coverage_incomplete = loaded_trace is not None and (
        trace_closures is None
        or set(closure_runs) != trace_run_ids
        or any(not item.complete for item in closures)
    )
    if closure_coverage_incomplete:
        diagnostics.append(
            BundleDiagnostic(
                "BUNDLE015",
                "Complete trace-closure evidence must cover every run in the normalized trace.",
                "trace-closure.json",
            )
        )
    if loaded_trace is not None:
        for closure in closures:
            validate_trace_closure(loaded_trace, closure)
            if closure.context.contract_digest != expected_digest or closure.context.plan_digest != plan.plan_digest:
                raise ValueError("Trace closure does not match the bundle contract and plan")
    controls: object
    if control_results is None:
        diagnostics.append(
            BundleDiagnostic("BUNDLE002", "Control assessment evidence is missing.", "control-results.json")
        )
        controls = {"results": [], "status": "unverified"}
    else:
        controls = {"results": [item.to_dict() for item in control_results]}
    declared_ids = {str(item.id) for item in contract.run_specs.values()}
    selections = tuple(run_spec_selections or ())
    results = tuple(run_spec_results or ())
    selection_runs = [item.run_id for item in selections]
    if len(selection_runs) != len(set(selection_runs)):
        raise ValueError("Run-spec selections must have unique run_id values")
    selection_trace_run_ids = trace_run_ids if contract.run_specs else set()
    selection_coverage_incomplete = bool(contract.run_specs) and (
        run_spec_selections is None or set(selection_runs) != selection_trace_run_ids
    )
    if selection_coverage_incomplete:
        diagnostics.append(
            BundleDiagnostic(
                "BUNDLE013",
                "Run-spec selection evidence must identify one selection for every run in the normalized trace.",
                "run-spec-results.json",
            )
        )
    unknown_selections = sorted(
        item.run_spec_id
        for item in selections
        if item.run_spec_id is not None and item.run_spec_id not in declared_ids
    )
    if unknown_selections:
        raise ValueError(
            f"Run-spec selections reference undeclared IDs: {', '.join(unknown_selections)}"
        )
    for result in results:
        if result.contract_digest != expected_digest or result.plan_digest != plan.plan_digest:
            raise ValueError(
                f"Run-spec result `{result.run_spec_id}` does not match the bundle contract and plan"
            )
        if result.run_spec_id not in declared_ids:
            raise ValueError(
                f"Run-spec assurance result references undeclared ID: {result.run_spec_id}"
            )
    selected_keys = {
        (item.run_id, item.run_spec_id)
        for item in selections
        if item.run_spec_id is not None
    }
    result_keys = [(item.run_id, item.run_spec_id) for item in results]
    if len(result_keys) != len(set(result_keys)):
        raise ValueError("Run-spec assurance results must be unique per run and run_spec_id")
    unexpected_results = sorted(set(result_keys) - selected_keys)
    if unexpected_results:
        raise ValueError("Run-spec assurance results were supplied without matching selection evidence")
    missing_results = sorted(selected_keys - set(result_keys))
    if missing_results:
        diagnostics.append(
            BundleDiagnostic(
                "BUNDLE014",
                "Assessment evidence is missing for one or more selected run specs.",
                "run-spec-results.json",
            )
        )
    run_specs: dict[str, object] = {
        "results": [item.to_dict() for item in results],
        "selections": [item.to_dict() for item in selections],
    }
    if selection_coverage_incomplete or missing_results:
        run_specs["status"] = "unverified"
    if eval_results is None:
        diagnostics.append(BundleDiagnostic("BUNDLE003", "Eval evidence is missing.", "eval-results.json"))
        eval_results = {"campaigns": [], "status": "unverified"}
    if provenance is None:
        diagnostics.append(BundleDiagnostic("BUNDLE004", "Provenance evidence is missing.", "provenance.json"))
        provenance = {"sources": [], "status": "unverified"}

    files: dict[str, str] = {
        "contract.snapshot.json": _pretty_json(canonical_ir_data(contract)),
        "materialization-plan.json": _pretty_json(materialization_plan_data(plan)),
        "normalized-trace.jsonl": trace,
        "trace-closure.json": _pretty_json(
            TraceClosureManifest(closures=closures).to_dict()
            if closures
            else {"closures": [], "version": TRACE_CLOSURE_MANIFEST_VERSION}
        ),
        "control-results.json": _pretty_json(controls),
        "run-spec-results.json": _pretty_json(run_specs),
        "eval-results.json": _pretty_json(eval_results),
        "provenance.json": _pretty_json(provenance),
    }
    files["summary.html"] = _summary_html(
        contract,
        plan,
        control_results or (),
        run_spec_results or (),
        diagnostics,
    )
    attestation = {
        "bundle_version": BUNDLE_VERSION,
        "complete": not diagnostics,
        "contract_digest": expected_digest,
        "diagnostics": [item.to_dict() for item in diagnostics],
        "files": {
            name: {"sha256": _sha256(source), "size": len(source.encode("utf-8"))}
            for name, source in sorted(files.items())
        },
        "plan_digest": plan.plan_digest,
    }
    files["attestation.json"] = _pretty_json(attestation)
    return AssuranceBundle(
        contract_digest=expected_digest,
        plan_digest=plan.plan_digest,
        files=FrozenMap(files),
        diagnostics=tuple(diagnostics),
    )


def verify_assurance_bundle(bundle: AssuranceBundle) -> tuple[BundleDiagnostic, ...]:
    """Verify every attested digest and internal contract/plan identity."""

    diagnostics: list[BundleDiagnostic] = []
    raw_attestation = bundle.files.get("attestation.json")
    if raw_attestation is None:
        return (BundleDiagnostic("BUNDLE005", "Bundle has no attestation.json.", "attestation.json"),)
    try:
        attestation = json.loads(raw_attestation)
    except json.JSONDecodeError as exc:
        return (BundleDiagnostic("BUNDLE006", f"Invalid attestation JSON: {exc}", "attestation.json"),)
    if attestation.get("contract_digest") != bundle.contract_digest:
        diagnostics.append(BundleDiagnostic("BUNDLE007", "Attested contract digest does not match bundle."))
    if attestation.get("plan_digest") != bundle.plan_digest:
        diagnostics.append(BundleDiagnostic("BUNDLE008", "Attested plan digest does not match bundle."))
    expected_files = attestation.get("files")
    if not isinstance(expected_files, dict):
        diagnostics.append(BundleDiagnostic("BUNDLE009", "Attestation file manifest is missing or invalid."))
        return tuple(diagnostics)
    for name, expected in sorted(expected_files.items()):
        source = bundle.files.get(name)
        if source is None:
            diagnostics.append(BundleDiagnostic("BUNDLE010", "Attested artifact is missing.", name))
            continue
        expected_digest = expected.get("sha256") if isinstance(expected, dict) else None
        if expected_digest != _sha256(source):
            diagnostics.append(BundleDiagnostic("BUNDLE011", "Artifact digest does not match attestation.", name))
    unattested = sorted(set(bundle.files) - {"attestation.json"} - set(expected_files))
    diagnostics.extend(
        BundleDiagnostic("BUNDLE012", "Artifact is present but not attested.", name) for name in unattested
    )
    return tuple(diagnostics)


def write_assurance_bundle(bundle: AssuranceBundle, output_dir: Path | str) -> tuple[Path, ...]:
    """Replace a bundle directory atomically enough for local review workflows."""

    root = Path(output_dir)
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    try:
        for name, source in bundle.files.items():
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        if root.exists():
            shutil.rmtree(root)
        temporary.replace(root)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return tuple(root / name for name in bundle.files)


def _required_text(
    artifact: str,
    value: str | None,
    diagnostics: list[BundleDiagnostic],
    code: str,
    message: str,
) -> str:
    if value is None or not value.strip():
        diagnostics.append(BundleDiagnostic(code, message, artifact))
        return ""
    return value if value.endswith("\n") else value + "\n"


def _summary_html(
    contract: CanonicalIR,
    plan: MaterializationPlan,
    results: tuple[ControlResult, ...],
    run_spec_results: tuple[RunSpecResult, ...],
    diagnostics: list[BundleDiagnostic],
) -> str:
    counts = {"passed": 0, "violated": 0, "unverified": 0}
    for result in results:
        counts[result.status] += 1
    run_spec_counts = {"passed": 0, "violated": 0, "unverified": 0}
    for run_spec_result in run_spec_results:
        run_spec_counts[run_spec_result.status] += 1
    missing = "".join(f"<li>{html.escape(item.message)}</li>" for item in diagnostics) or "<li>None</li>"
    return (
        '<!doctype html>\n<html lang="en"><meta charset="utf-8">'
        "<title>Contract4Agents assurance summary</title>"
        "<body><main><h1>Assurance summary</h1>"
        f"<p>Contract <code>{html.escape(contract_digest(contract))}</code></p>"
        f"<p>Plan <code>{html.escape(plan.plan_digest)}</code></p>"
        f"<p>{len(contract.agents)} agents; {len(contract.controls)} declared or derived controls.</p>"
        f"<p>Passed: {counts['passed']}; violated: {counts['violated']}; "
        f"unverified: {counts['unverified']}.</p>"
        f"<p>Run specs passed: {run_spec_counts['passed']}; violated: "
        f"{run_spec_counts['violated']}; unverified: {run_spec_counts['unverified']}.</p>"
        f"<h2>Missing evidence</h2><ul>{missing}</ul>"
        "</main></body></html>\n"
    )


def _pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _sha256(source: str) -> str:
    return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"


__all__ = [
    "BUNDLE_VERSION",
    "AssuranceBundle",
    "BundleDiagnostic",
    "assemble_assurance_bundle",
    "verify_assurance_bundle",
    "write_assurance_bundle",
]
