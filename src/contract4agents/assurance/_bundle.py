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
from contract4agents.ir import CanonicalIR, FrozenMap, canonical_ir_data, contract_digest
from contract4agents.planning import MaterializationPlan, materialization_plan_data

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
) -> AssuranceBundle:
    """Assemble all declared, planned, observed, and assessed evidence without timestamps."""

    expected_digest = contract_digest(contract)
    if plan.contract_digest != expected_digest:
        raise ValueError(
            f"Plan contract digest `{plan.contract_digest}` does not match contract `{expected_digest}`"
        )
    diagnostics: list[BundleDiagnostic] = []
    trace = _required_text(
        "normalized-trace.jsonl",
        normalized_trace_jsonl,
        diagnostics,
        "BUNDLE001",
        "Normalized trace evidence is missing.",
    )
    controls: object
    if control_results is None:
        diagnostics.append(
            BundleDiagnostic("BUNDLE002", "Control assessment evidence is missing.", "control-results.json")
        )
        controls = {"results": [], "status": "unverified"}
    else:
        controls = {"results": [item.to_dict() for item in control_results]}
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
        "control-results.json": _pretty_json(controls),
        "eval-results.json": _pretty_json(eval_results),
        "provenance.json": _pretty_json(provenance),
    }
    files["summary.html"] = _summary_html(contract, plan, control_results or (), diagnostics)
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
    diagnostics: list[BundleDiagnostic],
) -> str:
    counts = {"passed": 0, "violated": 0, "unverified": 0}
    for result in results:
        counts[result.status] += 1
    missing = "".join(f"<li>{html.escape(item.message)}</li>" for item in diagnostics) or "<li>None</li>"
    return (
        "<!doctype html>\n<html lang=\"en\"><meta charset=\"utf-8\">"
        "<title>Contract4Agents assurance summary</title>"
        "<body><main><h1>Assurance summary</h1>"
        f"<p>Contract <code>{html.escape(contract_digest(contract))}</code></p>"
        f"<p>Plan <code>{html.escape(plan.plan_digest)}</code></p>"
        f"<p>{len(contract.agents)} agents; {len(contract.controls)} declared or derived controls.</p>"
        f"<p>Passed: {counts['passed']}; violated: {counts['violated']}; "
        f"unverified: {counts['unverified']}.</p>"
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
