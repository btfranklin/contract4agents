"""Click CLI for Contract4Agents."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from contract4agents.compiler import build_artifacts, compile_project
from contract4agents.diagnostics import ContractError, raise_if_errors
from contract4agents.docscheck import check_docs
from contract4agents.fixtures import FixtureReport, run_fixture_project_sync
from contract4agents.monitor import MonitorRule, run_monitors
from contract4agents.parser import parse_project
from contract4agents.runtime._trace_io import TraceFileError, load_trace_jsonl
from contract4agents.semantics import analyze_project
from contract4agents.visualization import build_visualization_graph, write_visualization_artifacts


@click.group()
def main() -> None:
    """Compile, validate, and evaluate Contract4Agents projects."""


@main.command()
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
def check(root: Path) -> None:
    """Parse and semantically validate a Contract4Agents project."""
    try:
        project = parse_project(root)
        result = analyze_project(project)
        for diagnostic in result.diagnostics:
            click.echo(diagnostic.format(), err=diagnostic.severity == "error")
        if not result.ok:
            raise click.ClickException("Contract4Agents check failed")
        click.echo("Contract4Agents check passed")
    except ContractError as exc:
        _print_contract_error(exc)


@main.command("compile")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option("--out", "output_dir", type=click.Path(path_type=Path), default=".contract/build")
@click.option("--check", "check_mode", is_flag=True, help="Fail if generated artifacts are stale.")
def compile_cmd(root: Path, output_dir: Path, check_mode: bool) -> None:
    """Compile a Contract4Agents project into provider-neutral artifacts."""
    try:
        compile_project(root, output_dir, check=check_mode)
        click.echo("Contract4Agents compile passed")
    except ContractError as exc:
        _print_contract_error(exc)


@main.command("visualize")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option("--out", "output_dir", type=click.Path(path_type=Path), default=".contract/build/visualization")
def visualize_cmd(root: Path, output_dir: Path) -> None:
    """Generate static HTML visualization artifacts.

    ROOT defaults to the current directory. The default output directory is
    .contract/build/visualization.
    """
    try:
        project = parse_project(root)
        raise_if_errors(analyze_project(project).diagnostics)
        artifacts = build_artifacts(project)
        graph = build_visualization_graph(project, artifacts)
        write_visualization_artifacts(graph, output_dir)
        click.echo(f"Contract4Agents visualization written to {output_dir}")
    except ContractError as exc:
        _print_contract_error(exc)


@main.command("eval")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
def eval_cmd(root: Path) -> None:
    """Run local evals for a fixture.json project."""
    if not (root / "fixture.json").exists():
        raise click.ClickException("Contract4Agents eval requires ROOT/fixture.json")
    try:
        report = run_fixture_project_sync(project_root=root, run_root=root / ".contract" / "runs" / "last")
    except Exception as exc:
        raise click.ClickException(f"Contract4Agents fixture eval failed: {exc}") from exc
    _print_fixture_report(report)
    if not report.passed:
        raise click.ClickException("Contract4Agents eval failed")


def _print_fixture_report(report: FixtureReport) -> None:
    click.echo(f"Fixture eval {'passed' if report.passed else 'failed'}: {len(report.starts)} starts")
    for start in report.starts:
        status = "PASS" if start.passed and not start.monitor_violations else "FAIL"
        click.echo(f"{status} {start.start_id}")
        for failure in start.failures:
            click.echo(f"  {failure}")
        for failure in start.assertion_failures:
            click.echo(f"  assertion: {failure}")
        for violation in start.monitor_violations:
            click.echo(f"  monitor: {violation}")
        for skipped in start.skipped_semantic:
            click.echo(f"  semantic skipped: {skipped}")


@main.command()
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
@click.option("--trace", "trace_path", type=click.Path(path_type=Path), required=True, help="Trace JSONL file.")
def monitor(root: Path, trace_path: Path) -> None:
    """Run project monitors against a trace JSONL file."""
    artifacts = compile_project(root)
    try:
        trace = load_trace_jsonl(trace_path)
    except TraceFileError as exc:
        raise click.ClickException(str(exc)) from exc
    rules = [
        MonitorRule(item["name"], item["agent"], item["severity"], item["when"], item["expect"])
        for item in artifacts["monitors"]
    ]
    violations = run_monitors(rules, trace)
    for violation in violations:
        click.echo(f"{violation.severity.upper()} {violation.rule}: {violation.message}")
    if violations:
        raise click.ClickException("Contract4Agents monitor failed")
    click.echo("Contract4Agents monitor passed")


@main.command("docs-check")
@click.argument("root", type=click.Path(path_type=Path), default=".", required=False)
def docs_check(root: Path) -> None:
    """Check required documentation files and local markdown links.

    ROOT defaults to the current directory.
    """
    diagnostics = check_docs(root)
    for diagnostic in diagnostics:
        click.echo(diagnostic.format(), err=True)
    if diagnostics:
        raise click.ClickException("Docs check failed")
    click.echo("Docs check passed")


def _print_contract_error(exc: ContractError) -> None:
    for diagnostic in exc.diagnostics:
        click.echo(diagnostic.format(), err=True)
    sys.exit(1)
