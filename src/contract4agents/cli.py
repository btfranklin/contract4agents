"""Click CLI for Contract4Agents."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from contract4agents.adapters._registry import get_adapter_registration
from contract4agents.assurance import (
    RunSpecAssessmentManifest,
    assess_assurance_evidence,
    assess_controls,
    semantic_diff,
    write_assurance_bundle,
)
from contract4agents.codegen import (
    GENERATION_TARGETS,
    CodeGenerationError,
    generate_code,
    write_generated_code,
)
from contract4agents.compiler import compile_project
from contract4agents.diagnostics import ContractError, Diagnostic, raise_if_errors
from contract4agents.eval_campaigns import CampaignConfig, CampaignThresholds, FileEvalProvider, run_campaign
from contract4agents.ir import build_canonical_ir
from contract4agents.materialization import (
    GraphValidationEvidence,
    MaterializationError,
    plan_project,
)
from contract4agents.output_paths import validate_output_dir
from contract4agents.parser import parse_project
from contract4agents.planning import (
    PlanningError,
    materialization_plan_data,
)
from contract4agents.semantics import analyze_project
from contract4agents.target_bindings import (
    AdapterBindingValidator,
    AdapterProfileValidator,
    load_target_bindings,
    validate_target_binding_conformance,
)
from contract4agents.tracing import (
    TraceClosureError,
    TraceClosureEvidence,
    TraceClosureManifest,
    TraceConformanceError,
    TraceLoadError,
    load_trace_jsonl,
)


@click.group()
def main() -> None:
    """Define agent systems in external contracts and build native SDK objects."""


@main.command()
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
def check(root: Path) -> None:
    """Check external agent contracts and discovered target bindings."""
    try:
        project = parse_project(root)
        result = analyze_project(project)
        diagnostics = list(result.diagnostics)
        loaded = load_target_bindings(project.root)
        diagnostics.extend(loaded.diagnostics)
        if result.ok and loaded.bindings is not None:
            ir = build_canonical_ir(project)
            for target_name in loaded.bindings.targets:
                target_binding = loaded.bindings.targets[target_name]
                conformance = validate_target_binding_conformance(
                    ir,
                    loaded.bindings,
                    target_name,
                    project_root=project.root,
                    adapter_validator=_adapter_binding_validator(target_binding.adapter),
                    profile_validator=_adapter_profile_validator(target_binding.adapter),
                )
                diagnostics.extend(conformance.diagnostics)
        _print_diagnostics(diagnostics)
        if any(item.severity == "error" for item in diagnostics):
            raise click.ClickException("Contract4Agents check failed")
        click.echo("Contract4Agents check passed")
    except ContractError as exc:
        _print_contract_error(exc)


@main.command("compile")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(path_type=Path),
    default=".contract/build",
    help="Generated artifact directory. Relative paths are resolved from the current working directory.",
)
@click.option("--check", "check_mode", is_flag=True, help="Fail if generated artifacts are stale.")
def compile_cmd(root: Path, output_dir: Path, check_mode: bool) -> None:
    """Compile external agent contracts into provider-neutral artifacts."""
    try:
        compile_project(root, output_dir, check=check_mode)
        click.echo("Contract4Agents compile passed")
    except ContractError as exc:
        _print_contract_error(exc)


@main.command("generate")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(path_type=Path),
    default=".contract/generated",
    help="Application-consumed generated-source directory.",
)
@click.option(
    "--target",
    "targets",
    type=click.Choice(GENERATION_TARGETS),
    multiple=True,
    required=True,
    help="Generated source target. Repeat to generate more than one target.",
)
@click.option("--check", "check_mode", is_flag=True, help="Fail if generated source is stale.")
def generate_cmd(root: Path, output_dir: Path, targets: tuple[str, ...], check_mode: bool) -> None:
    """Generate typed application source from external contracts."""

    try:
        artifacts = compile_project(root)
        # Generated models are application source artifacts by design. The
        # writer owns only its fixed generated files and supports freshness
        # checks, so explicit source-tree destinations are valid here.
        output_path = output_dir if output_dir.is_absolute() else Path.cwd() / output_dir
        write_generated_code(generate_code(artifacts.ir, targets=targets), output_path, check=check_mode)
        click.echo("Contract4Agents generate passed")
    except ContractError as exc:
        _print_contract_error(exc)
    except CodeGenerationError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("plan")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option("--target", required=True, help="Target adapter name.")
@click.option("--profile", required=True, help="Complete target profile name.")
@click.option(
    "--bindings",
    "bindings_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Target-binding TOML path.",
)
@click.option(
    "--out",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the materialization plan to a JSON file instead of stdout.",
)
def plan_cmd(
    root: Path,
    target: str,
    profile: str,
    bindings_path: Path | None,
    output_path: Path | None,
) -> None:
    """Plan native SDK construction without building SDK objects."""

    try:
        system = plan_project(
            root,
            target=target,
            profile=profile,
            bindings=bindings_path,
        )
        rendered = (
            json.dumps(
                materialization_plan_data(system.plan),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if output_path is None:
            click.echo(rendered, nl=False)
        else:
            destination = output_path if output_path.is_absolute() else Path.cwd() / output_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered)
            click.echo(f"Contract4Agents plan written to {destination}")
    except ContractError as exc:
        _print_contract_error(exc)
    except PlanningError as exc:
        for issue in exc.issues:
            click.echo(issue.format(), err=True)
        raise click.ClickException("Contract4Agents planning failed") from exc
    except MaterializationError as exc:
        for materialization_issue in exc.issues:
            click.echo(materialization_issue.format(), err=True)
        raise click.ClickException("Contract4Agents planning failed") from exc


def _adapter_binding_validator(adapter: str) -> AdapterBindingValidator | None:
    registration = get_adapter_registration(adapter)
    return registration.binding_validator if registration is not None else None


def _adapter_profile_validator(adapter: str) -> AdapterProfileValidator | None:
    registration = get_adapter_registration(adapter)
    return registration.profile_validator if registration is not None else None


@main.command("visualize")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option("--target", default=None, help="Optional target adapter for the planned layer.")
@click.option("--profile", default=None, help="Optional target profile for the planned layer.")
@click.option("--bindings", "bindings_path", type=click.Path(path_type=Path), default=None)
@click.option("--trace", "trace_path", type=click.Path(path_type=Path), default=None)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(path_type=Path),
    default=".contract/build/visualization",
    help="Generated visualization directory. Relative paths are resolved from the current working directory.",
)
def visualize_cmd(
    root: Path,
    target: str | None,
    profile: str | None,
    bindings_path: Path | None,
    trace_path: Path | None,
    output_dir: Path,
) -> None:
    """Visualize a declared agent system and supplied evidence.

    ROOT defaults to the current directory. The default output directory is
    .contract/build/visualization.
    """
    try:
        from contract4agents.visualization import build_visualization_graph, write_visualization_artifacts

        if (target is None) != (profile is None):
            raise click.ClickException("--target and --profile must be supplied together")
        project = parse_project(root)
        raise_if_errors(analyze_project(project).diagnostics)
        ir = build_canonical_ir(project)
        plan = None
        planned = None
        if target is not None and profile is not None:
            planned = plan_project(
                root,
                target=target,
                profile=profile,
                bindings=bindings_path,
            )
            plan = planned.plan
        trace = load_trace_jsonl(trace_path) if trace_path is not None else None
        results = assess_controls(planned, trace) if planned is not None and trace is not None else ()
        graph = build_visualization_graph(
            ir,
            project_root=project.root,
            plan=plan,
            trace=trace,
            control_results=results,
        )
        output_path = validate_output_dir(project.root, output_dir, artifact_label="visualization artifacts")
        write_visualization_artifacts(graph, output_path)
        click.echo(f"Contract4Agents visualization written to {output_path}")
    except ContractError as exc:
        _print_contract_error(exc)


@main.group("eval")
def eval_group() -> None:
    """Run optional assessments for declared eval cases."""


@eval_group.command("replay")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option("--target", required=True, help="Target adapter name.")
@click.option("--profile", required=True, help="Complete test profile name.")
@click.option("--bindings", "bindings_path", type=click.Path(path_type=Path), default=None)
@click.option("--data", "data_path", type=click.Path(path_type=Path), default=None, help="Eval-data JSON file.")
@click.option("--trials", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--min-pass-rate", type=click.FloatRange(min=0, max=1), default=None)
@click.option("--max-violation-rate", type=click.FloatRange(min=0, max=1), default=None)
@click.option("--out", "output_path", type=click.Path(path_type=Path), default=None)
def eval_replay_cmd(
    root: Path,
    target: str,
    profile: str,
    bindings_path: Path | None,
    data_path: Path | None,
    trials: int,
    min_pass_rate: float | None,
    max_violation_rate: float | None,
    output_path: Path | None,
) -> None:
    """Assess declared eval cases from replayed evidence."""
    try:
        planned = plan_project(
            root,
            target=target,
            profile=profile,
            bindings=bindings_path,
        )
        provider_path = data_path or Path(root) / "eval-data.json"
        provider = FileEvalProvider.load(provider_path)
        report = asyncio.run(
            run_campaign(
                planned,
                provider,
                CampaignConfig(
                    campaign_id=f"{target}:{profile}",
                    trial_count=trials,
                    thresholds=CampaignThresholds(
                        min_pass_rate=min_pass_rate,
                        max_violation_rate=max_violation_rate,
                    ),
                ),
            )
        )
        rendered = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
        destination = output_path or Path(root) / ".contract" / "eval-replay.json"
        destination = destination if destination.is_absolute() else Path.cwd() / destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered)
        rates = report.summary.rates
        click.echo(
            f"Eval replay: {rates.passed} passed, {rates.violated} violated, "
            f"{rates.unverified} unverified ({rates.total} trials)"
        )
        click.echo(f"Results written to {destination}")
        failed_comparisons = tuple(
            item for item in report.threshold_results + report.regression_results if item.status != "passed"
        )
        if rates.violated or rates.unverified or failed_comparisons:
            raise click.ClickException("Contract4Agents eval replay failed")
    except ContractError as exc:
        _print_contract_error(exc)
    except PlanningError as exc:
        for issue in exc.issues:
            click.echo(issue.format(), err=True)
        raise click.ClickException("Contract4Agents planning failed") from exc
    except Exception as exc:
        if isinstance(exc, click.ClickException):
            raise
        raise click.ClickException(f"Contract4Agents eval replay failed: {exc}") from exc


@main.command("assess")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option("--target", required=True, help="Named target from contract4agents.targets.toml.")
@click.option("--profile", required=True, help="Complete model/provider profile to assess.")
@click.option(
    "--bindings",
    "bindings_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the discovered target-binding file.",
)
@click.option(
    "--trace",
    "trace_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Normalized trace JSONL to assess.",
)
@click.option(
    "--trace-closure",
    "trace_closure_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Versioned trace-closure manifest matching the trace frontier.",
)
@click.option("--run-id", default=None, help="Select one run when the trace contains multiple runs.")
def assess_cmd(
    root: Path,
    target: str,
    profile: str,
    bindings_path: Path | None,
    trace_path: Path,
    trace_closure_path: Path | None,
    run_id: str | None,
) -> None:
    """Optionally assess declared controls against a normalized trace."""
    try:
        planned = plan_project(
            root,
            target=target,
            profile=profile,
            bindings=bindings_path,
        )
        trace = load_trace_jsonl(trace_path)
        closure_manifest = _load_trace_closure_manifest(trace_closure_path)
        selected_run = run_id or (trace.run_ids[0] if len(trace.run_ids) == 1 else None)
        closure = _closure_for_run(closure_manifest, selected_run)
        results = assess_controls(planned, trace, closure=closure, run_id=run_id)
        for result in results:
            click.echo(f"{result.status.upper()} {result.control_id}: {result.reason}")
        if any(result.status != "passed" for result in results):
            raise click.ClickException("Contract4Agents assessment found violated or unverified controls")
        click.echo("Contract4Agents assessment passed")
    except TraceLoadError as exc:
        raise click.ClickException(f"Invalid normalized trace `{trace_path}`: {exc}") from exc
    except TraceConformanceError as exc:
        raise click.ClickException(f"Nonconforming normalized trace `{trace_path}`: {exc}") from exc
    except TraceClosureError as exc:
        raise click.ClickException(f"Invalid trace closure: {exc}") from exc


@main.command("assure")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option("--target", required=True, help="Named target from contract4agents.targets.toml.")
@click.option("--profile", required=True, help="Complete model/provider profile used by the evidence.")
@click.option(
    "--bindings",
    "bindings_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the discovered target-binding file.",
)
@click.option(
    "--trace",
    "trace_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Normalized trace JSONL to include and assess.",
)
@click.option(
    "--materialization-evidence",
    "materialization_evidence_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Materialized configuration and schema conformance evidence from the native graph.",
)
@click.option(
    "--trace-closure",
    "trace_closure_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Versioned trace-closure manifest matching every trace run.",
)
@click.option(
    "--run-spec-evidence",
    "run_spec_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Versioned host-selected run-spec evidence manifest.",
)
@click.option(
    "--eval-results",
    type=click.Path(path_type=Path),
    default=None,
    help="Eval campaign result JSON to include.",
)
@click.option("--provenance", type=click.Path(path_type=Path), default=None, help="Provenance JSON to include.")
@click.option(
    "--out",
    "output_dir",
    type=click.Path(path_type=Path),
    default=".contract/assurance",
    help="Destination directory for the assurance bundle.",
)
def assure_cmd(
    root: Path,
    target: str,
    profile: str,
    bindings_path: Path | None,
    trace_path: Path | None,
    materialization_evidence_path: Path | None,
    trace_closure_path: Path | None,
    run_spec_path: Path | None,
    eval_results: Path | None,
    provenance: Path | None,
    output_dir: Path,
) -> None:
    """Optionally assemble a deterministic assurance bundle."""
    planned = plan_project(
        root,
        target=target,
        profile=profile,
        bindings=bindings_path,
    )
    trace = load_trace_jsonl(trace_path) if trace_path is not None else None
    closure_manifest = _load_trace_closure_manifest(trace_closure_path)
    run_spec_manifest = _load_run_spec_manifest(run_spec_path)
    if run_spec_manifest is not None and trace is None:
        raise click.ClickException("--run-spec-evidence requires --trace")
    bundle = assess_assurance_evidence(
        planned,
        trace=trace,
        trace_closures=closure_manifest,
        run_spec_evidence=run_spec_manifest,
        eval_results=_load_json_file(eval_results),
        provenance=_load_json_file(provenance),
        materialization_evidence=(
            GraphValidationEvidence.load(materialization_evidence_path)
            if materialization_evidence_path is not None
            else None
        ),
    )
    destination = output_dir if output_dir.is_absolute() else Path.cwd() / output_dir
    write_assurance_bundle(bundle, destination)
    click.echo(f"Assurance bundle written to {destination}")
    for diagnostic in bundle.diagnostics:
        click.echo(f"UNVERIFIED {diagnostic.code}: {diagnostic.message}")


@main.command("diff")
@click.argument("before", type=click.Path(path_type=Path, exists=True))
@click.argument("after", type=click.Path(path_type=Path, exists=True))
@click.option("--out", "output_path", type=click.Path(path_type=Path), default=None)
def diff_cmd(before: Path, after: Path, output_path: Path | None) -> None:
    """Compare the semantics of two external contract projects."""
    before_ir = compile_project(before).ir
    after_ir = compile_project(after).ir
    result = semantic_diff(before_ir, after_ir)
    rendered = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
    if output_path is None:
        click.echo(rendered, nl=False)
    else:
        destination = output_path if output_path.is_absolute() else Path.cwd() / output_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered)
        click.echo(f"Semantic diff written to {destination}")


def _load_json_file(path: Path | None) -> object | None:
    if path is None:
        return None
    try:
        value: object = json.loads(path.read_text())
        return value
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Could not load JSON `{path}`: {exc}") from exc


def _load_trace_closure_manifest(path: Path | None) -> TraceClosureManifest | None:
    if path is None:
        return None
    try:
        return TraceClosureManifest.load(path)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(f"Could not load trace closure `{path}`: {exc}") from exc


def _load_run_spec_manifest(path: Path | None) -> RunSpecAssessmentManifest | None:
    if path is None:
        return None
    try:
        return RunSpecAssessmentManifest.load(path)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(f"Could not load run-spec evidence `{path}`: {exc}") from exc


def _closure_for_run(
    manifest: TraceClosureManifest | None,
    run_id: str | None,
) -> TraceClosureEvidence | None:
    if manifest is None or run_id is None:
        return None
    return next((item for item in manifest.closures if item.context.run_id == run_id), None)


def _print_diagnostics(diagnostics: list[Diagnostic]) -> None:
    for diagnostic in diagnostics:
        click.echo(diagnostic.format(), err=diagnostic.severity == "error")


def _print_contract_error(exc: ContractError) -> None:
    for diagnostic in exc.diagnostics:
        click.echo(diagnostic.format(), err=True)
    sys.exit(1)
