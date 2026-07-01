"""Reusable Contract4Agents fixture runner."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from contract4agents.assertions import evaluate_run_contract
from contract4agents.compiler import compile_project
from contract4agents.evaluation import EvalRunner
from contract4agents.fixtures import _execution, _reports
from contract4agents.fixtures._artifacts import verify_fixture_artifacts
from contract4agents.fixtures._execution import load_fixture_metadata
from contract4agents.fixtures._models import (
    FixtureArtifactError,
    FixtureConfigError,
    FixtureReport,
    StartReport,
)
from contract4agents.monitor import MonitorRule, run_monitors
from contract4agents.runtime._utils import load_python_ref


async def run_fixture_project(
    *,
    project_root: Path,
    run_root: Path,
    mode: str = "local",
    keep_artifacts: bool | None = None,
) -> FixtureReport:
    keep = keep_artifacts if keep_artifacts is not None else os.getenv("CONTRACT4AGENTS_KEEP_FIXTURE_ARTIFACTS") == "1"
    artifact_checks: list[str] = []
    start_reports: list[StartReport] = []
    cleaned = False
    report = FixtureReport(str(project_root), mode, artifact_checks, start_reports, False, str(run_root))
    pending_error: Exception | None = None
    active_start_id: str | None = None
    try:
        metadata = load_fixture_metadata(project_root)
        _execution.prepare_fixture_import_roots(project_root)
        run_root.mkdir(parents=True, exist_ok=True)
        build_dir = run_root / "build"
        db_path = load_python_ref(metadata["seed"])(run_root / "data" / "fixture.sqlite")
        artifacts = compile_project(project_root, build_dir)
        compile_project(project_root, build_dir, check=True)
        artifact_checks = verify_fixture_artifacts(metadata, artifacts, build_dir)
        starts = load_python_ref(metadata["starts"])()
        runner = _execution.runner_for_mode(metadata, mode)
        evals_by_start = {_execution.clean_given(item["givens"]["start"]): item for item in artifacts["evals"]}
        eval_runner = EvalRunner(artifacts["schemas"])
        monitor_rules = [
            MonitorRule(item["name"], item["agent"], item["severity"], item["when"], item["expect"])
            for item in artifacts["monitors"]
        ]
        hidden_truth_func = load_python_ref(metadata["hidden_truth"])
        for start in starts:
            active_start_id = str(start.start_id)
            output, trace, attempts, retry_errors = await _execution.run_start_with_retry(
                runner, start, db_path, artifacts, run_root / "traces" / f"{start.start_id}.jsonl", mode
            )
            eval_pack = evals_by_start[start.start_id]
            hidden_truth = hidden_truth_func(db_path, start.start_id)
            eval_result = await eval_runner.evaluate(
                name=str(eval_pack["name"]),
                output=output,
                output_type=str(metadata["output_type"]),
                trace=trace,
                expectations=list(eval_pack["expects"]),
                semantic_expectations=list(eval_pack["semantic_expects"]),
                hidden_truth=hidden_truth,
            )
            assertion_result = evaluate_run_contract(
                contract=artifacts,
                trace=trace,
                outputs={str(metadata["entry_agent"]): output},
                target_agents=[str(metadata["entry_agent"])],
                hidden_truth=hidden_truth,
            )
            violations = run_monitors(monitor_rules, trace)
            start_reports.append(
                StartReport(
                    start_id=start.start_id,
                    passed=eval_result.passed and assertion_result.passed and not violations,
                    failures=[f"{failure.kind}: {failure.message}" for failure in eval_result.failures],
                    assertion_failures=[
                        f"{failure.kind}: {failure.message}" for failure in assertion_result.failures
                    ],
                    skipped_semantic=eval_result.skipped_semantic,
                    monitor_violations=[f"{item.severity}: {item.message}" for item in violations],
                    attempts=attempts,
                    retry_errors=retry_errors,
                )
            )
            active_start_id = None
        report = FixtureReport(str(project_root), mode, artifact_checks, start_reports, False, str(run_root))
    except Exception as exc:
        pending_error = exc
        failed_start_id = active_start_id or "__fixture__"
        if not any(item.start_id == failed_start_id for item in start_reports):
            start_reports.append(
                StartReport(
                    start_id=failed_start_id,
                    passed=False,
                    failures=[f"{type(exc).__name__}: {exc}"],
                )
            )
        report = FixtureReport(str(project_root), mode, artifact_checks, start_reports, False, str(run_root))
    finally:
        if run_root.exists():
            if not keep and report.passed:
                cleaned = _reports.cleanup_generated_artifacts(run_root)
            report.cleaned = cleaned
            _reports.write_report(report, run_root)
    if pending_error:
        raise pending_error
    return report


def run_fixture_project_sync(**kwargs: Any) -> FixtureReport:
    return asyncio.run(run_fixture_project(**kwargs))


__all__ = [
    "FixtureArtifactError",
    "FixtureConfigError",
    "FixtureReport",
    "StartReport",
    "load_fixture_metadata",
    "run_fixture_project",
    "run_fixture_project_sync",
    "verify_fixture_artifacts",
]
